"""Opt-in single-authority file queue. No arbitrary operations or implicit archiving."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import UUID, uuid4
from fastapi import HTTPException

from . import share_capture
from .share_store import ShareStore
from .storage_layout import StorageLayout, checked_path, workspace_identity

MAX_ENVELOPE = 800_000
HEADER = "# Torch queue v1\n\n```json\n"


def envelope(data):
    return HEADER + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n```\n"


def decode(text):
    text = text.replace("\r\n", "\n")
    if not text.startswith(HEADER) or not text.endswith("\n```\n") or len(text.encode()) > MAX_ENVELOPE:
        raise ValueError("队列文件格式无效")
    result = json.loads(text[len(HEADER):-5])
    if not isinstance(result, dict):
        raise ValueError("队列记录应为对象")
    return result


class ShareQueue:
    def __init__(self, database, vault, *, layout=None, fetch=share_capture.capture):
        self.vault = checked_path(vault)
        self.workspace = workspace_identity(self.vault)
        if not self.workspace:
            raise ValueError("队列需要已验证的工作区身份")
        self.layout = layout or StorageLayout.legacy(vault, database.path)
        self.path = self.layout.state / "share-queue.sqlite3"
        self.store = ShareStore(database, vault, storage_layout=self.layout)
        self.fetch = fetch

    def _request(self, path):
        checked_path(path)
        if not path.is_file() or path.stat().st_size > 16384:
            raise ValueError("请求过大")
        raw = path.read_bytes()
        request = decode(raw.decode("utf-8"))
        required = {"schema_version", "request_id", "workspace_id", "device_id", "created_at", "action"}
        fields = {"source_url", "rounds", "answers_only", "client_note", "preview_request", "content_hash", "title", "chapter", "allow_partial", "target"}
        if not required <= request.keys() or request.keys() - required - fields or request["schema_version"] != 1:
            raise ValueError("不支持的队列字段")
        if str(UUID(request["request_id"])) != path.stem or request["workspace_id"] != self.workspace:
            raise ValueError("队列身份不匹配")
        UUID(request["device_id"])
        if request["action"] not in {"share_preview", "share_commit", "cancel"}:
            raise ValueError("不支持的队列动作")
        for key in ("answers_only", "allow_partial"):
            if key in request and type(request[key]) is not bool:
                raise ValueError("确认字段类型无效")
        for key in fields - {"answers_only", "allow_partial"}:
            if key in request and (not isinstance(request[key], str) or len(request[key]) > 2048):
                raise ValueError("请求字段过长或类型无效")
        return request, hashlib.sha256(raw).hexdigest()

    def _publish(self, request_id, data):
        root = checked_path(self.vault / "_Torch/回执")
        root.mkdir(parents=True, exist_ok=True)
        path = checked_path(root / f"{request_id}.md")
        raw = envelope(data).encode()
        if len(raw) > MAX_ENVELOPE:
            raise ValueError("回执超过预算，请缩小轮次")
        if path.exists():
            if path.read_bytes() != raw:
                raise ValueError("回执被外部修改，保留双方并停止")
            return
        temp = root / (".pending-" + uuid4().hex)
        try:
            with temp.open("xb") as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            os.link(temp, path)
        finally:
            temp.unlink(missing_ok=True)

    def run_once(self, *, limit=20):
        # SQLite transaction serializes duplicate local consumers. Only ONE physical
        # authority may be configured; a replicated SQLite file is not a lease.
        root = checked_path(self.vault / "_Torch/收件")
        if not root.exists():
            return {"processed": 0, "errors": 0}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        counts = {"processed": 0, "errors": 0}
        with closing(sqlite3.connect(self.path, timeout=1)) as con:
            con.row_factory = sqlite3.Row
            con.execute("CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY, hash TEXT, receipt TEXT, snapshot TEXT)")
            con.execute("CREATE TABLE IF NOT EXISTS cancellations(id TEXT PRIMARY KEY)")
            con.commit()
            paths = sorted(root.glob("*.md"))
            if len(paths) > 10000:
                raise ValueError("队列条目超出扫描预算")
            # Validate before sorting cancels first; no malformed request performs a fetch.
            valid = []
            for path in paths:
                try:
                    request, digest = self._request(path)
                    valid.append((path, request, digest))
                except (ValueError, TypeError, OSError):
                    counts["errors"] += 1
            valid.sort(key=lambda p: p[1]["action"] != "cancel")
            for path, request, digest in valid:
                if counts["processed"] >= limit:
                    break
                rid = request["request_id"]
                con.execute("BEGIN IMMEDIATE")
                try:
                    previous = con.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
                    if previous:
                        if previous["hash"] != digest:
                            raise ValueError("不可变请求被修改")
                        con.commit()
                        self._publish(rid, json.loads(previous["receipt"]))
                        continue
                    receipt = {"schema_version": 1, "request_id": rid, "workspace_id": self.workspace,
                               "action": request["action"], "status": "error", "retryable": False}
                    snapshot = None
                    if con.execute("SELECT 1 FROM cancellations WHERE id=?", (rid,)).fetchone():
                        receipt["status"] = "cancelled"
                    elif request["action"] == "cancel":
                        target = str(UUID(request.get("target", "")))
                        already = con.execute("SELECT 1 FROM requests WHERE id=?", (target,)).fetchone()
                        if not already:
                            con.execute("INSERT OR IGNORE INTO cancellations VALUES(?)", (target,))
                        receipt["status"] = "too_late" if already else "cancelled"
                    elif request["action"] == "share_preview":
                        try:
                            snapshot = self.fetch(request.get("source_url", ""), rounds=request.get("rounds", ""), answers_only=request.get("answers_only", False))
                            if len(snapshot["markdown"].encode()) > 400000:
                                raise ValueError("预览过长，请缩小轮次")
                            receipt.update(status="preview_ready", preview_id=rid, content_hash=snapshot["content_hash"],
                                           title=snapshot["title"], markdown=snapshot["markdown"], completeness=snapshot["completeness"])
                        except (share_capture.legacy.ShareLinkError, ValueError):
                            receipt.update(error="来源不可访问或结构不支持；可重建请求重试或粘贴正文", retryable=True)
                    else:
                        parent = con.execute("SELECT * FROM requests WHERE id=?", (request.get("preview_request", ""),)).fetchone()
                        if not parent:
                            # Out-of-order delivery is not a terminal error; leave unclaimed.
                            con.rollback(); continue
                        data = json.loads(parent["snapshot"]) if parent["snapshot"] else None
                        try:
                            if not data or request.get("content_hash") != data["content_hash"]:
                                raise ValueError("确认预览不匹配")
                            if data["completeness"] == "partial" and request.get("allow_partial") is not True:
                                raise ValueError("未确认不完整附件")
                            chapter = request.get("chapter") or "inbox"
                            from .knowledge_share import _knowledge_chapter
                            chapter = _knowledge_chapter(chapter, "inbox", self.vault)
                            result = self.store.archive(data, chapter=chapter, title=request.get("title", ""))
                            receipt.update(status="archived", path=result["note"]["path"], duplicate=result["duplicate"])
                        except (ValueError, HTTPException):
                            receipt.update(error="确认范围或目标不可用，请重新预览或在桌面核对已有导入副本")
                    encoded = json.dumps(receipt, ensure_ascii=False)
                    con.execute("INSERT INTO requests VALUES(?,?,?,?)", (rid, digest, encoded, json.dumps(snapshot, ensure_ascii=False) if snapshot else None))
                    con.commit()  # durable before sync-visible receipt
                    self._publish(rid, receipt)
                    counts["processed"] += 1
                except Exception:
                    con.rollback()
                    counts["errors"] += 1  # don't leak credentials/content/host paths
        return counts
