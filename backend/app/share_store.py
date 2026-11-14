"""Persistent previews and recoverable archive operations, scoped to one database/vault."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

from . import note_editor, pillar_notes
from .storage_layout import StorageLayout


class ShareStore:
    def __init__(self, database, vault_dir, *, storage_layout=None):
        self.database = database
        self.vault = Path(vault_dir).resolve()
        layout = storage_layout or StorageLayout.legacy(self.vault, database.path)
        if layout.notes != self.vault:
            raise ValueError("存储配置不属于当前笔记库")
        self.path = layout.share_database

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        try:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS previews(id TEXT PRIMARY KEY, created REAL, payload TEXT);
                CREATE TABLE IF NOT EXISTS imports(fingerprint TEXT PRIMARY KEY, import_id TEXT UNIQUE,
                  state TEXT, chapter TEXT, result TEXT);
            """)
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def save_preview(self, result):
        pid = uuid4().hex
        with self.connect() as con:
            con.execute("INSERT INTO previews VALUES(?,?,?)", (pid, time.time(), json.dumps(result, ensure_ascii=False)))
            # Expired snapshots are not used for archiving. Bound persistent private content.
            con.execute("DELETE FROM previews WHERE created < ?", (time.time() - 86400,))
        return dict(result, preview_id=pid, expires_in=86400)

    def load(self, pid):
        with self.connect() as con:
            row = con.execute("SELECT * FROM previews WHERE id=?", (pid,)).fetchone()
        if not row or row["created"] < time.time() - 86400:
            raise HTTPException(409, "预览不存在或已过期，请重新预览。")
        return json.loads(row["payload"])

    def archive(self, result, *, chapter, title, read_only=False, allow_copy=False):
        if read_only:
            raise HTTPException(409, "当前工作区只读")
        fingerprint = result["content_hash"] + (":" + chapter if allow_copy else "")
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM imports WHERE fingerprint=?", (fingerprint,)).fetchone()
            if row and row["state"] == "committed":
                previous = json.loads(row["result"])
                previous["duplicate"] = True
                return previous
            if not row:
                iid = uuid4().hex
                con.execute("INSERT INTO imports VALUES(?,?,?,?,?)", (fingerprint, iid, "prepared", chapter, None))
                con.commit()  # recovery identity must survive a crash during note creation
                con.execute("BEGIN IMMEDIATE")
                row = con.execute("SELECT * FROM imports WHERE fingerprint=?", (fingerprint,)).fetchone()
                if row["state"] == "committed":
                    return dict(json.loads(row["result"]), duplicate=True)
            iid = row["import_id"]
            previous_import_id = ""
            if result["share_url"]:
                for previous in con.execute("SELECT import_id,result FROM imports WHERE state='committed' AND fingerprint<>? ORDER BY rowid DESC", (fingerprint,)):
                    if json.loads(previous["result"]).get("source") == result["share_url"]:
                        previous_import_id = previous["import_id"]
                        break
            # Reconcile a file written before an interrupted index/audit/ledger commit.
            recovered = []
            for item in pillar_notes.list_notes(self.vault, pillar="knowledge", limit=None):
                note = pillar_notes.read_note(self.vault, item["path"])
                if note["raw_metadata"].get("share_import_id") == iid:
                    recovered.append(note)
            if len(recovered) > 1:
                raise HTTPException(409, "发现多个导入副本，请核对后再重试。")
            note = recovered[0] if recovered else note_editor.create_note(
                self.database, self.vault, chapter=row["chapter"], title=title or result["title"],
                body=result["markdown"], source=result["share_url"], generated_by="share-link",
                extra_metadata={"share_import_id": iid, "capture_hash": result["content_hash"],
                                "capture_method": result["capture_method"], "content_hash": result["content_hash"],
                                "previous_import_id": previous_import_id})
            output = {"note": note, "chapter": note.get("chapter", chapter), "source": result["share_url"],
                      "turns": result["turns"], "chars": result["chars_markdown"], "duplicate": bool(recovered)}
            con.execute("UPDATE imports SET state='committed',result=? WHERE fingerprint=?",
                        (json.dumps(output, ensure_ascii=False), fingerprint))
            return output
