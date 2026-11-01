"""恋爱相册（2026-11-01）：照片/视频相簿与本地缩略图。

文件在 ``02_生活运营/01_恋爱系统/05_恋爱相册/``：
- ``albums.db``：相簿与媒体元数据；
- ``照片/<相簿 id>/``、``视频/<相簿 id>/``：原文件（复制进库，不引用外部路径）；
- ``thumbnails/``：图片缩略图；视频缩略图可由浏览器上传首帧，缺失时显示占位。

每个媒体属于一个相簿；另有“全部照片/视频”总览。删除相簿会把媒体移回“未分类”。
"""
from __future__ import annotations

import hashlib
import mimetypes
import os
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .love_layout import LovePaths
from .vault_connection import VaultReadOnlyError, ensure_vault_writable

DAY = ZoneInfo("Asia/Shanghai")
DEFAULT_ALBUM = "未分类"
MAX_IMAGE_BYTES = 80 * 1024 * 1024
MAX_VIDEO_BYTES = 1024 * 1024 * 1024
MAX_BATCH = 80
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif", ".heic", ".heif"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}
ALLOWED_EXT = IMAGE_EXT | VIDEO_EXT
THUMB_MAX = 480

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS albums (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    created_at TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS media (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    album_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    filename TEXT NOT NULL,
    original_name TEXT NOT NULL DEFAULT '',
    mime TEXT NOT NULL DEFAULT '',
    size INTEGER NOT NULL DEFAULT 0,
    width INTEGER NOT NULL DEFAULT 0,
    height INTEGER NOT NULL DEFAULT 0,
    duration REAL NOT NULL DEFAULT 0,
    thumb TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL UNIQUE,
    taken_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY(album_id) REFERENCES albums(id)
);
CREATE INDEX IF NOT EXISTS ix_love_gallery_album ON media(album_id);
CREATE INDEX IF NOT EXISTS ix_love_gallery_kind ON media(kind);
"""


def _now() -> str:
    return datetime.now(DAY).isoformat(timespec="seconds")


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    return connection


def ensure_gallery_schema(paths: LovePaths) -> None:
    with _connect(paths.gallery_db) as connection:
        connection.executescript(SCHEMA_SQL)
        connection.execute(
            "INSERT OR IGNORE INTO albums (id, name, created_at, sort_order) VALUES (1, ?, ?, 0)",
            (DEFAULT_ALBUM, _now()))
        connection.commit()


def _album_json(row: sqlite3.Row, counts: dict[int, dict[str, int]] | None = None) -> dict[str, Any]:
    stat = (counts or {}).get(int(row["id"]), {})
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "created_at": row["created_at"],
        "sort_order": int(row["sort_order"] or 0),
        "protected": int(row["id"]) == 1,
        "count": stat.get("total", 0),
        "photos": stat.get("photo", 0),
        "videos": stat.get("video", 0),
        "cover": stat.get("cover", ""),
    }


def _media_json(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "album_id": int(row["album_id"]),
        "kind": row["kind"],
        "original_name": row["original_name"],
        "mime": row["mime"],
        "size": int(row["size"] or 0),
        "width": int(row["width"] or 0),
        "height": int(row["height"] or 0),
        "duration": float(row["duration"] or 0),
        "taken_at": row["taken_at"],
        "created_at": row["created_at"],
        "has_thumb": bool(row["thumb"]),
        "file_url": f"/api/love/gallery/media/{int(row['id'])}/file",
        "thumb_url": f"/api/love/gallery/media/{int(row['id'])}/thumb" if row["thumb"] else "",
    }


def _album_counts(connection: sqlite3.Connection) -> dict[int, dict[str, int]]:
    result: dict[int, dict[str, int]] = {}
    for row in connection.execute(
            "SELECT album_id, kind, COUNT(*) AS count FROM media GROUP BY album_id, kind"):
        item = result.setdefault(int(row["album_id"]), {})
        item[str(row["kind"])] = int(row["count"])
        item["total"] = item.get("total", 0) + int(row["count"])
    for row in connection.execute(
            "SELECT album_id, id FROM media WHERE thumb != '' ORDER BY id DESC"):
        item = result.setdefault(int(row["album_id"]), {})
        item.setdefault("cover", f"/api/love/gallery/media/{int(row['id'])}/thumb")
    return result


def _safe_album_name(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise HTTPException(422, "相簿名称不能为空")
    if len(text) > 48:
        raise HTTPException(422, "相簿名称最多 48 个字符")
    if any(character in text for character in '<>:"/\\|?*') or text.startswith("."):
        raise HTTPException(422, "相簿名称不能包含 < > : \" / \\ | ? *，也不能以点开头")
    return text


def _media_dir(paths: LovePaths, kind: str, album_id: int) -> Path:
    base = paths.gallery_photos if kind == "photo" else paths.gallery_videos
    target = base / str(int(album_id))
    target.mkdir(parents=True, exist_ok=True)
    return target


def _save_thumbnail(source: Path, target: Path) -> tuple[int, int]:
    """生成图片缩略图；失败返回 0,0（不影响原图入库）。"""
    try:
        from PIL import Image, ImageOps
        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()
        except Exception:
            pass
        with Image.open(source) as image:
            image = ImageOps.exif_transpose(image)
            width, height = image.size
            image.thumbnail((THUMB_MAX, THUMB_MAX))
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            target.parent.mkdir(parents=True, exist_ok=True)
            image.save(target, "JPEG", quality=82, optimize=True)
            return int(width), int(height)
    except Exception:
        return 0, 0


def _save_upload(upload: UploadFile, target_dir: Path, *, max_bytes: int,
                 kind: str) -> tuple[Path, str, int]:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXT:
        raise HTTPException(422, f"不支持的文件类型：{suffix or '未知'}")
    temporary = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=target_dir)
    temporary_path = Path(temporary.name)
    size = 0
    digest = hashlib.sha256()
    try:
        while True:
            chunk = upload.file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise HTTPException(413, "文件超过大小上限")
            digest.update(chunk)
            temporary.write(chunk)
        temporary.close()
        if not size:
            raise HTTPException(400, "文件内容为空")
        name = digest.hexdigest()[:40] + suffix
        target = target_dir / name
        if target.exists():
            temporary_path.unlink(missing_ok=True)
        else:
            os.replace(temporary_path, target)
        return target, digest.hexdigest(), size
    except Exception:
        try:
            temporary.close()
        except Exception:
            pass
        temporary_path.unlink(missing_ok=True)
        raise


class AlbumPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=48)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        try:
            return _safe_album_name(value)
        except HTTPException as error:
            raise ValueError(str(error.detail)) from error


class MovePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    album_id: int = Field(ge=1)


class BulkPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[int] = Field(min_length=1, max_length=MAX_BATCH)
    action: str = Field(pattern=r"^(move|delete)$")
    album_id: int | None = Field(default=None, ge=1)


def create_gallery_router(paths: LovePaths, *, read_only: bool = False,
                          prefix: str = "/api/love/gallery") -> APIRouter:
    if not read_only:
        ensure_gallery_schema(paths)
    router = APIRouter(prefix=prefix, tags=["love-gallery"])

    def connection() -> sqlite3.Connection:
        if not paths.gallery_db.is_file():
            if read_only:
                raise HTTPException(404, "还没有相册数据")
            ensure_gallery_schema(paths)
        return _connect(paths.gallery_db)

    @router.get("/overview")
    def overview(limit: int = Query(default=36, ge=1, le=200)):
        with connection() as con:
            counts = _album_counts(con)
            albums = [_album_json(row, counts) for row in
                      con.execute("SELECT * FROM albums ORDER BY sort_order, id")]
            recent = [_media_json(row) for row in con.execute(
                "SELECT * FROM media ORDER BY id DESC LIMIT ?", (limit,))]
            totals = con.execute(
                "SELECT kind, COUNT(*) AS count FROM media GROUP BY kind").fetchall()
        total = {row["kind"]: int(row["count"]) for row in totals}
        return {
            "albums": albums,
            "recent": recent,
            "total": total.get("photo", 0) + total.get("video", 0),
            "photos": total.get("photo", 0),
            "videos": total.get("video", 0),
            "paths": {"db": str(paths.gallery_db), "photos": str(paths.gallery_photos),
                      "videos": str(paths.gallery_videos)},
        }

    @router.get("/albums")
    def list_albums():
        with connection() as con:
            counts = _album_counts(con)
            return {"items": [_album_json(row, counts) for row in
                              con.execute("SELECT * FROM albums ORDER BY sort_order, id")]}

    @router.post("/albums")
    def create_album(payload: AlbumPayload):
        try:
            ensure_vault_writable(read_only, "新建相簿")
        except VaultReadOnlyError as error:
            raise HTTPException(409, str(error)) from error
        with connection() as con:
            try:
                cursor = con.execute(
                    "INSERT INTO albums (name, created_at, sort_order) VALUES (?, ?, 100)",
                    (payload.name, _now()))
                con.commit()
            except sqlite3.IntegrityError as error:
                raise HTTPException(409, "这个相簿已经存在") from error
            row = con.execute("SELECT * FROM albums WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return _album_json(row)

    @router.put("/albums/{album_id}")
    def rename_album(album_id: int, payload: AlbumPayload):
        if album_id == 1:
            raise HTTPException(422, "默认相簿「未分类」不能改名")
        try:
            ensure_vault_writable(read_only, "重命名相簿")
        except VaultReadOnlyError as error:
            raise HTTPException(409, str(error)) from error
        with connection() as con:
            try:
                con.execute("UPDATE albums SET name = ? WHERE id = ?", (payload.name, album_id))
                con.commit()
            except sqlite3.IntegrityError as error:
                raise HTTPException(409, "这个相簿名称已经存在") from error
            row = con.execute("SELECT * FROM albums WHERE id = ?", (album_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "相簿不存在")
            return _album_json(row)

    @router.delete("/albums/{album_id}")
    def delete_album(album_id: int):
        if album_id == 1:
            raise HTTPException(422, "默认相簿「未分类」不能删除")
        try:
            ensure_vault_writable(read_only, "删除相簿")
        except VaultReadOnlyError as error:
            raise HTTPException(409, str(error)) from error
        with connection() as con:
            row = con.execute("SELECT * FROM albums WHERE id = ?", (album_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "相簿不存在")
            media_rows = con.execute(
                "SELECT * FROM media WHERE album_id = ?", (album_id,)).fetchall()
            for media in media_rows:
                old_dir = _media_dir(paths, media["kind"], album_id)
                new_dir = _media_dir(paths, media["kind"], 1)
                source = old_dir / media["filename"]
                target = new_dir / media["filename"]
                if source.is_file() and source != target:
                    os.replace(source, target)
            con.execute("UPDATE media SET album_id = 1 WHERE album_id = ?", (album_id,))
            con.execute("DELETE FROM albums WHERE id = ?", (album_id,))
            con.commit()
        return {"deleted": True, "moved_to": 1}

    @router.get("/media")
    def list_media(album_id: int = Query(default=0, ge=0),
                   kind: str = Query(default="", pattern=r"^(photo|video|)$"),
                   offset: int = Query(default=0, ge=0),
                   limit: int = Query(default=120, ge=1, le=500)):
        clauses: list[str] = []
        values: list[Any] = []
        if album_id:
            clauses.append("album_id = ?")
            values.append(album_id)
        if kind:
            clauses.append("kind = ?")
            values.append(kind)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with connection() as con:
            total = con.execute(f"SELECT COUNT(*) AS count FROM media{where}", values).fetchone()["count"]
            rows = con.execute(
                f"SELECT * FROM media{where} ORDER BY id DESC LIMIT ? OFFSET ?",
                (*values, limit, offset)).fetchall()
        return {"total": int(total), "items": [_media_json(row) for row in rows]}

    @router.post("/upload")
    async def upload(files: list[UploadFile] = File(...),
                     album_id: int = Form(default=1),
                     thumbs: list[UploadFile] | None = File(default=None)):
        if len(files) > MAX_BATCH:
            raise HTTPException(422, f"一次最多上传 {MAX_BATCH} 个文件")
        try:
            ensure_vault_writable(read_only, "上传照片/视频")
        except VaultReadOnlyError as error:
            raise HTTPException(409, str(error)) from error
        with connection() as con:
            album = con.execute("SELECT * FROM albums WHERE id = ?", (album_id,)).fetchone()
            if album is None:
                raise HTTPException(404, "相簿不存在")
        saved: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        thumb_files = list(thumbs or [])
        for index, upload_file in enumerate(files):
            suffix = Path(upload_file.filename or "").suffix.lower()
            kind = "photo" if suffix in IMAGE_EXT else "video" if suffix in VIDEO_EXT else ""
            if not kind:
                skipped.append({"name": upload_file.filename or "", "reason": "不支持的文件类型"})
                continue
            max_bytes = MAX_IMAGE_BYTES if kind == "photo" else MAX_VIDEO_BYTES
            target_dir = _media_dir(paths, kind, album_id)
            try:
                target, digest, size = _save_upload(
                    upload_file, target_dir, max_bytes=max_bytes, kind=kind)
            except HTTPException as error:
                skipped.append({"name": upload_file.filename or "", "reason": str(error.detail)})
                continue
            with connection() as con:
                existing = con.execute(
                    "SELECT * FROM media WHERE content_hash = ?", (digest,)).fetchone()
                if existing is not None:
                    skipped.append({"name": upload_file.filename or "", "reason": "重复文件"})
                    continue
            thumb_rel = ""
            width = height = 0
            duration = 0.0
            if kind == "photo":
                thumb_name = f"{digest[:40]}.jpg"
                thumb_path = paths.gallery_thumbs / thumb_name
                width, height = _save_thumbnail(target, thumb_path)
                if thumb_path.is_file():
                    thumb_rel = thumb_name
            elif index < len(thumb_files) and thumb_files[index].filename:
                thumb_upload = thumb_files[index]
                thumb_name = f"{digest[:40]}.jpg"
                thumb_path = paths.gallery_thumbs / thumb_name
                try:
                    data = await thumb_upload.read(2 * 1024 * 1024 + 1)
                    if len(data) <= 2 * 1024 * 1024:
                        thumb_path.parent.mkdir(parents=True, exist_ok=True)
                        thumb_path.write_bytes(data)
                        thumb_rel = thumb_name
                except OSError:
                    thumb_rel = ""
            original = Path(upload_file.filename or target.name).name[:200]
            mime = mimetypes.guess_type(original)[0] or upload_file.content_type or ""
            with connection() as con:
                try:
                    cursor = con.execute(
                        """INSERT INTO media
                        (album_id, kind, filename, original_name, mime, size, width, height,
                         duration, thumb, content_hash, taken_at, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?)""",
                        (album_id, kind, target.name, original, mime, size, width, height,
                         duration, thumb_rel, digest, _now()))
                    con.commit()
                    row = con.execute("SELECT * FROM media WHERE id = ?",
                                      (cursor.lastrowid,)).fetchone()
                    saved.append(_media_json(row))
                except sqlite3.IntegrityError:
                    skipped.append({"name": original, "reason": "重复文件"})
        return {"saved": saved, "skipped": skipped}

    @router.patch("/media/{media_id}")
    def move_media(media_id: int, payload: MovePayload):
        try:
            ensure_vault_writable(read_only, "移动照片/视频")
        except VaultReadOnlyError as error:
            raise HTTPException(409, str(error)) from error
        with connection() as con:
            if con.execute("SELECT 1 FROM albums WHERE id = ?", (payload.album_id,)).fetchone() is None:
                raise HTTPException(404, "目标相簿不存在")
            row = con.execute("SELECT * FROM media WHERE id = ?", (media_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "媒体不存在")
            old_dir = _media_dir(paths, row["kind"], int(row["album_id"]))
            new_dir = _media_dir(paths, row["kind"], payload.album_id)
            source = old_dir / row["filename"]
            target = new_dir / row["filename"]
            if source.is_file() and source != target:
                try:
                    os.replace(source, target)
                except OSError as error:
                    raise HTTPException(409, f"移动文件失败：{error}") from error
            con.execute("UPDATE media SET album_id = ? WHERE id = ?",
                        (payload.album_id, media_id))
            con.commit()
            row = con.execute("SELECT * FROM media WHERE id = ?", (media_id,)).fetchone()
            return _media_json(row)

    def _delete_media_row(con: sqlite3.Connection, row: sqlite3.Row) -> None:
        folder = _media_dir(paths, row["kind"], int(row["album_id"]))
        (folder / row["filename"]).unlink(missing_ok=True)
        if row["thumb"]:
            (paths.gallery_thumbs / row["thumb"]).unlink(missing_ok=True)
        con.execute("DELETE FROM media WHERE id = ?", (int(row["id"]),))

    @router.delete("/media/{media_id}")
    def delete_media(media_id: int):
        try:
            ensure_vault_writable(read_only, "删除照片/视频")
        except VaultReadOnlyError as error:
            raise HTTPException(409, str(error)) from error
        with connection() as con:
            row = con.execute("SELECT * FROM media WHERE id = ?", (media_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "媒体不存在")
            _delete_media_row(con, row)
            con.commit()
        return {"deleted": True}

    @router.post("/media/bulk")
    def bulk(payload: BulkPayload):
        try:
            ensure_vault_writable(read_only, "批量操作照片/视频")
        except VaultReadOnlyError as error:
            raise HTTPException(409, str(error)) from error
        done = 0
        with connection() as con:
            if payload.action == "move":
                if payload.album_id is None:
                    raise HTTPException(422, "移动需要目标相簿")
                if con.execute("SELECT 1 FROM albums WHERE id = ?",
                               (payload.album_id,)).fetchone() is None:
                    raise HTTPException(404, "目标相簿不存在")
            for media_id in payload.ids[:MAX_BATCH]:
                row = con.execute("SELECT * FROM media WHERE id = ?", (media_id,)).fetchone()
                if row is None:
                    continue
                if payload.action == "delete":
                    _delete_media_row(con, row)
                else:
                    old_dir = _media_dir(paths, row["kind"], int(row["album_id"]))
                    new_dir = _media_dir(paths, row["kind"], int(payload.album_id))
                    source = old_dir / row["filename"]
                    target = new_dir / row["filename"]
                    if source.is_file() and source != target:
                        try:
                            os.replace(source, target)
                        except OSError:
                            continue
                    con.execute("UPDATE media SET album_id = ? WHERE id = ?",
                                (payload.album_id, media_id))
                done += 1
            con.commit()
        return {"done": done}

    @router.get("/media/{media_id}/file")
    def media_file(media_id: int):
        with connection() as con:
            row = con.execute("SELECT * FROM media WHERE id = ?", (media_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "媒体不存在")
            folder = _media_dir(paths, row["kind"], int(row["album_id"]))
            target = (folder / row["filename"]).resolve()
            if not target.is_relative_to(folder.resolve()) or not target.is_file():
                raise HTTPException(404, "文件不存在")
            return FileResponse(target, media_type=row["mime"] or "application/octet-stream")

    @router.get("/media/{media_id}/thumb")
    def media_thumb(media_id: int):
        with connection() as con:
            row = con.execute("SELECT * FROM media WHERE id = ?", (media_id,)).fetchone()
            if row is None or not row["thumb"]:
                raise HTTPException(404, "没有缩略图")
            target = (paths.gallery_thumbs / row["thumb"]).resolve()
            if not target.is_relative_to(paths.gallery_thumbs.resolve()) or not target.is_file():
                raise HTTPException(404, "缩略图不存在")
            return FileResponse(target, media_type="image/jpeg")

    return router


__all__ = ["ensure_gallery_schema", "create_gallery_router", "SCHEMA_SQL"]
