"""Explicit, read-only source selection; local export never uploads to ChatGPT."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from . import vault_layout
from .vault import safe_note_path

EXCLUDED = re.compile(r"微信|回收|备份|backup|trash|credential|secret|apikey|api_key|冲突", re.I)
KEY = re.compile(r"(?:sk-[A-Za-z0-9_-]{16,}|-----BEGIN .*PRIVATE KEY|(?:api[_-]?key|access[_-]?token|password|密码)\s*[:=]\s*['\"]?[A-Za-z0-9_+/=-]{12,})", re.I)


class PackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paths: list[str] = Field(min_length=1, max_length=1000)
    budget_bytes: int = Field(default=2_000_000, ge=1000, le=8_000_000)
    expected_hash: str = Field(default="", max_length=64)


def source_path(vault, relative):
    relative = str(relative).replace("\\", "/")
    parts = relative.split("/")
    if (not parts or parts[0] not in vault_layout.PILLAR_DIRS.values()
            or any(p in ("", ".", "..", "_Torch", "_系统") or p.startswith(".") or EXCLUDED.search(p) for p in parts)
            or not relative.endswith(".md")):
        raise HTTPException(422, "资料包只能选择四库内的普通 Markdown，系统/备份/微信原始资料不可导出。")
    root = Path(vault).resolve()
    path = root
    for part in parts:
        path = path / part
        if path.is_symlink() or path.is_junction():
            raise HTTPException(422, "资料包不跟随符号链接或目录联接。")
    if not path.resolve().is_relative_to(root) or not path.is_file():
        raise HTTPException(422, "源笔记不存在或超出工作区。")
    return safe_note_path(root, relative)


def build(vault, request):
    sources, groups = [], {name: [] for name in vault_layout.PILLAR_DIRS.values()}
    total = 0
    for relative in sorted({p.replace("\\", "/") for p in request.paths}):
        path = source_path(vault, relative)
        if path.stat().st_size > request.budget_bytes:
            raise HTTPException(413, f"源文件超过本次预算：{relative}")
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeError as error:
            raise HTTPException(422, f"源笔记不是 UTF-8：{relative}") from error
        if KEY.search(text):
            raise HTTPException(422, f"疑似包含密钥，请先检查：{relative}")
        total += len(raw)
        if total > request.budget_bytes:
            raise HTTPException(413, "所选正文超过本次预算，请减少选项；未截断原文。")
        source = {"path": relative, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
                  "modified_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()}
        sources.append(source)
        groups[relative.split("/")[0]].append(f"## 来源：{relative}\n\n{text}\n")
    fingerprint = hashlib.sha256(json.dumps(sources, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    stamp = datetime.now(timezone.utc).isoformat()
    files = [{"name": f"{i + 1:02d}-{root}.md", "markdown":
              f"# {root}\n\n资料导出于：{stamp}\n附件未包含。区分用户原文与 AI 建议；以源笔记日期为准。\n\n" + "\n\n".join(texts)}
             for i, (root, texts) in enumerate(groups.items()) if texts]
    output_bytes = sum(len(f["markdown"].encode()) for f in files)
    if output_bytes > request.budget_bytes:
        raise HTTPException(413, "加上来源信息后的资料包超过预算，请减少选项。")
    return {"sources": sources, "files": files, "source_hash": fingerprint, "bytes": output_bytes,
            "exported_at": stamp, "uploaded": False,
            "warnings": ["未检出密钥不代表完全脱敏；上传前请检查正文。", "附件未包含；导出后需手动上传或替换 ChatGPT 项目文件。"]}


def create_context_pack_router(database, vault_dir, *, read_only=False):
    router = APIRouter()
    scope = hashlib.sha256(str(Path(vault_dir).resolve()).encode()).hexdigest()[:16]
    exports = database.path.parent / "context-packs" / scope

    @router.get("/knowledge/context-pack/sources")
    def sources():
        found = []
        for root in vault_layout.PILLAR_DIRS.values():
            for path in (Path(vault_dir) / root).rglob("*.md"):
                relative = path.relative_to(vault_dir).as_posix()
                try:
                    checked = source_path(vault_dir, relative)
                except HTTPException:
                    continue
                # Generated conversations are opt-in through API, not preselected.
                found.append({"path": relative, "bytes": checked.stat().st_size,
                              "modified_at": datetime.fromtimestamp(checked.stat().st_mtime, timezone.utc).isoformat()})
        return {"sources": sorted(found, key=lambda s: s["path"]), "default_selected": []}

    @router.post("/knowledge/context-pack/preview")
    def preview(payload: PackRequest):
        return build(vault_dir, payload)

    @router.get("/knowledge/context-pack/status")
    def status():
        manifests = list(exports.glob("*/manifest.json"))
        if not manifests:
            return {"exported": False, "uploaded": False}
        latest = max(manifests, key=lambda p: p.stat().st_mtime)
        try:
            previous = json.loads(latest.read_text(encoding="utf-8"))
            changed = []
            for source in previous["sources"]:
                try:
                    path = source_path(vault_dir, source["path"])
                    if path.stat().st_size > 8_000_000 or hashlib.sha256(path.read_bytes()).hexdigest() != source["sha256"]:
                        changed.append(source["path"])
                except (OSError, HTTPException, ValueError):
                    changed.append(source["path"])
            return {"exported": True, "uploaded": False, "exported_at": previous["exported_at"],
                    "changed": changed, "stale": bool(changed), "note": "仅比较上次选源，新增笔记需手动选入；不知道是否已上传 ChatGPT。"}
        except (OSError, ValueError, KeyError):
            raise HTTPException(409, "上次资料包记录损坏，请重新预览导出。")

    @router.post("/knowledge/context-pack/export")
    def export(payload: PackRequest):
        if read_only:
            raise HTTPException(409, "当前工作区只读")
        result = build(vault_dir, payload)
        if not payload.expected_hash or payload.expected_hash != result["source_hash"]:
            raise HTTPException(409, "选源或源笔记已变化，请重新预览。")
        pack_id = uuid4().hex
        target = exports / pack_id
        target.mkdir(parents=True)
        for item in result["files"]:
            (target / item["name"]).write_text(item["markdown"], encoding="utf-8")
        (target / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        previous = []
        for entry in exports.glob("*/manifest.json"):
            if entry.parent != target:
                previous.append(entry)
        old = json.loads(max(previous, key=lambda p: p.stat().st_mtime).read_text(encoding="utf-8")) if previous else {}
        before = {s["path"]: s["sha256"] for s in old.get("sources", [])}
        after = {s["path"]: s["sha256"] for s in result["sources"]}
        return {"pack_id": pack_id, "source_hash": result["source_hash"], "uploaded": False,
                "changed": sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k)),
                "files": [{"name": f["name"], "url": f"/api/knowledge/context-pack/{pack_id}/{f['name']}"} for f in result["files"]]}

    @router.get("/knowledge/context-pack/{pack_id}/{filename}")
    def download(pack_id: str, filename: str):
        if not re.fullmatch(r"[0-9a-f]{32}", pack_id):
            raise HTTPException(404)
        directory = exports / pack_id
        try:
            manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise HTTPException(404)
        if filename not in {f["name"] for f in manifest["files"]}:
            raise HTTPException(404)
        return FileResponse(directory / filename, filename=filename, media_type="text/markdown")

    return router
