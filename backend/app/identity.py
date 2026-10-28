"""Identity: the user's own name, motto and three views, kept in the Vault.

Markdown is the source of truth (四库设计把身份放在
`01_自我档案/01_内核与三观/`)，so `身份（identity）.md` is a normal note the user
can edit in Obsidian and dsh can read through the vault tools. The app only
offers a structured form on top of the same file: front matter carries the
short fields, the body carries free notes. Writing is atomic, respects the
vault read-only switch and requires the revision the client last read, so two
windows cannot silently overwrite each other.
"""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .db import utc_now
from .markdown_store import revision as _revision
from .vault import note_relative, safe_note_path
from .vault_connection import VaultReadOnlyError, ensure_vault_writable

IDENTITY_RELATIVE = "01_自我档案/01_内核与三观/身份（identity）.md"
IDENTITY_LEGACY_RELATIVE = "01-Identity/身份（identity）.md"
IDENTITY_LEGACY_ALT = "01-Identity/identity.md"


def _identity_path(vault_dir: Path | str) -> Path:
    """四库路径优先；未迁移的旧库继续读写 `01-Identity/`（不自动改名）。"""
    root = Path(vault_dir)
    current = safe_note_path(root, IDENTITY_RELATIVE)
    if current.exists():
        return current
    for legacy in (IDENTITY_LEGACY_RELATIVE, IDENTITY_LEGACY_ALT,
                   "01_自我档案/01_内核与三观/identity.md"):
        candidate = safe_note_path(root, legacy)
        if candidate.exists():
            return candidate
    return current
TEXT_FIELDS = ("name", "motto", "worldview", "lifeview", "values", "notes")
FIELD_LABELS = {"name": "称呼", "motto": "座右铭", "worldview": "世界观",
                "lifeview": "人生观", "values": "价值观", "notes": "自己写的"}
MAX_FIELD = 4_000


class IdentityWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(default="", max_length=60)
    motto: str = Field(default="", max_length=200)
    worldview: str = Field(default="", max_length=MAX_FIELD)
    lifeview: str = Field(default="", max_length=MAX_FIELD)
    values: str = Field(default="", max_length=MAX_FIELD)
    notes: str = Field(default="", max_length=MAX_FIELD)
    revision: str = Field(default="", max_length=64)


def _float_in(value: Any, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, round(number, 2)))


# 横幅字体是一个 CSS 字体栈（'"LXGW WenKai","霞鹜文楷",serif'）：只放行字体名会用到的字符，
# 别的（分号、花括号、反斜杠…）一律当成没填，免得它被写进样式里还能拼出别的声明。
FONT_PATTERN = re.compile(r"^[A-Za-z0-9\s,'\"()\-_.㐀-鿿]{1,120}$")


def _clean_font(value: Any) -> str:
    text = str(value or "").strip()
    return text if FONT_PATTERN.fullmatch(text) else ""


def _split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        loaded = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        loaded = {}
    if not isinstance(loaded, dict):
        loaded = {}
    return loaded, parts[2].lstrip("\n")


def read_identity(vault_dir: Path | str) -> dict[str, Any]:
    """Read the identity note; a missing file is an empty identity, not an error."""
    path = _identity_path(vault_dir)
    if not path.is_file():
        return {
            "path": note_relative(path, vault_dir),
            "exists": False,
            "revision": "",
            "updated_at": None,
            "mottos": [],
            "hero": "",
            "heroes": [],
            "hero_overlay": 0.45,
            "hero_focal": "",
            "banner_mode": "quote",
            "banner_accent": "",
            "banner_blur": 6.0,
            "banner_darkness": 30.0,
            "banner_left_stat": "notes",
            "banner_center_stat": "streak",
            "banner_details": True,
            "banner_font": "",
            **{field: "" for field in TEXT_FIELDS},
        }
    raw = path.read_bytes()
    metadata, body = _split_front_matter(raw.decode("utf-8", errors="replace"))
    values = {}
    for field in TEXT_FIELDS:
        value = metadata.get(field, "")
        values[field] = str(value).strip() if value is not None else ""
    values["notes"] = values["notes"] or body.strip()
    raw_mottos = metadata.get("mottos") or []
    mottos = [str(item).strip() for item in raw_mottos if str(item).strip()] \
        if isinstance(raw_mottos, list) else []
    hero = str(metadata.get("hero") or "").strip()
    raw_heroes = metadata.get("heroes") or []
    heroes = [str(item).strip() for item in raw_heroes if str(item).strip()] \
        if isinstance(raw_heroes, list) else []
    if not heroes and hero:
        heroes = [hero]
    try:
        overlay = float(metadata.get("hero_overlay"))
    except (TypeError, ValueError):
        overlay = 0.45
    overlay = max(0.0, min(0.9, overlay))
    mode = str(metadata.get("banner_mode") or "quote").strip()
    left_stat = str(metadata.get("banner_left_stat") or "notes").strip()
    if left_stat not in ("notes", "memories", "open", "files"):
        left_stat = "notes"
    center_stat = str(metadata.get("banner_center_stat") or "streak").strip()
    if center_stat not in ("streak", "percent"):
        center_stat = "streak"
    return {
        "path": note_relative(path, vault_dir),
        "exists": True,
        "revision": _revision(raw),
        "updated_at": metadata.get("updated_at"),
        "mottos": mottos,
        "hero": hero,
        "heroes": heroes,
        "hero_overlay": overlay,
        "hero_focal": str(metadata.get("hero_focal") or "").strip(),
        "banner_mode": mode if mode in ("quote", "stats") else "quote",
        "banner_accent": str(metadata.get("banner_accent") or "").strip(),
        "banner_blur": _float_in(metadata.get("banner_blur"), 6.0, 0.0, 20.0),
        "banner_darkness": _float_in(metadata.get("banner_darkness"), 30.0, 0.0, 60.0),
        "banner_left_stat": left_stat,
        "banner_center_stat": center_stat,
        "banner_details": metadata.get("banner_details") is not False,
        "banner_font": _clean_font(metadata.get("banner_font")),
        **values,
    }


def write_identity(vault_dir: Path | str, payload: IdentityWrite, *, read_only: bool = False) -> dict[str, Any]:
    """Replace the identity note. Requires the revision the client last saw."""
    try:
        ensure_vault_writable(read_only, "编辑个人档案")
    except VaultReadOnlyError as error:
        raise HTTPException(409, str(error)) from error
    path = _identity_path(vault_dir)
    current = path.read_bytes() if path.is_file() else None
    expected = payload.revision.strip()
    if current is not None and expected != _revision(current):
        raise HTTPException(409, "档案已在别处修改：请重新读取后再保存")
    if current is None and expected:
        raise HTTPException(409, "档案已被删除：请重新读取后再保存")
    if not any(getattr(payload, field) for field in TEXT_FIELDS):
        raise HTTPException(422, "档案不能全部为空；至少留一个字段")

    # 多座右铭与横幅字段目前不在表单里编辑：写回时原样保留（工作台横幅在用）。
    preserved: dict[str, Any] = {}
    if current is not None:
        existing, _ = _split_front_matter(current.decode("utf-8", errors="replace"))
        for key in ("mottos", "hero", "heroes", "hero_overlay", "hero_focal", "banner_mode",
                    "banner_accent", "banner_blur", "banner_darkness", "banner_left_stat",
                    "banner_center_stat", "banner_details", "banner_font"):
            if existing.get(key) not in (None, "", []):
                preserved[key] = existing[key]
    metadata = {
        "type": "identity",
        "updated_at": utc_now(),
        "visibility": "private",
        **preserved,
    }
    for field in ("name", "motto", "worldview", "lifeview", "values"):
        value = getattr(payload, field)
        if value:
            metadata[field] = value
    body = payload.notes.strip()
    text = "---\n" + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False) + "---\n\n" + body + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)
    return read_identity(vault_dir)


HERO_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

# 顶层数据规则（2026-10-27）：个人数据库里，Markdown 正文属于四库；
# 系统配置、小组件个性化数据和非 Markdown 资源统一放在库内 `.torch-os/`。
# 横幅图片的落点写死在这里——前端只负责选文件，不允许自己决定存哪。
TORCH_DATA_DIR = ".torch-os"
BANNER_IMAGE_DIR = "横幅背景"
MAX_BANNER_IMAGE_BYTES = 10 * 1024 * 1024
_BANNER_IMAGE_SUFFIXES = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
)


def _image_suffix(raw: bytes) -> str:
    """按文件头判定图片类型，不信任浏览器报的 MIME/扩展名。"""
    for signature, suffix in _BANNER_IMAGE_SUFFIXES:
        if raw.startswith(signature):
            return suffix
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return ".webp"
    return ""


def _banner_filename(original: str | None, suffix: str) -> str:
    stem = Path(original or "").stem.strip()
    stem = re.sub(r"[^0-9A-Za-z一-鿿._-]+", "_", stem).strip("._-")
    return f"{(stem or 'banner')[:60]}-{uuid.uuid4().hex[:8]}{suffix}"


def _hero_image_path(vault_dir: Path | str, relative: str) -> Path:
    """横幅图片路径：新数据是库内相对路径；旧数据可能写成 Vault 内的绝对路径，仍允许读取。"""
    root = Path(vault_dir).resolve()
    candidate = Path(relative)
    if not candidate.is_absolute():
        return safe_note_path(root, relative)
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("横幅图片路径不在个人数据库内")
    for part in [resolved, *resolved.parents]:
        if part == root:
            break
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ValueError("不读取链接目录或符号链接")
    return resolved


class BannerWrite(BaseModel):
    """工作台横幅（2026-10-05）：座右铭、图片列表、遮罩深浅、焦点、模式与字体——都写回 身份（identity）.md。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    mottos: list[str] = Field(default_factory=list, max_length=12)
    heroes: list[str] = Field(default_factory=list, max_length=12)
    hero_overlay: float = Field(default=0.45, ge=0, le=0.9)
    hero_focal: str = Field(default="", max_length=40)
    banner_mode: str = "quote"
    banner_accent: str = Field(default="", max_length=9)
    banner_blur: float = Field(default=6.0, ge=0, le=20)
    banner_darkness: float = Field(default=30.0, ge=0, le=60)
    banner_left_stat: str = "notes"
    banner_center_stat: str = "streak"
    banner_details: bool = True
    banner_font: str = Field(default="", max_length=120)
    revision: str = Field(default="", max_length=64)

    @model_validator(mode="after")
    def validate_banner(self):
        if self.banner_mode not in ("quote", "stats"):
            raise ValueError("banner_mode 只能是 quote 或 stats")
        cleaned_mottos = [item.strip() for item in self.mottos if item.strip()]
        if any(len(item) > 200 for item in cleaned_mottos):
            raise ValueError("每条座右铭最多 200 字")
        cleaned_heroes = [item.strip() for item in self.heroes if item.strip()]
        if any(len(item) > 500 for item in cleaned_heroes):
            raise ValueError("图片路径最多 500 字")
        self.mottos, self.heroes = cleaned_mottos, cleaned_heroes
        focal = self.hero_focal.strip()
        if focal and not re.fullmatch(r"\d{1,3}(\.\d+)?% \d{1,3}(\.\d+)?%", focal):
            raise ValueError("焦点格式应为 “50% 40%”")
        self.hero_focal = focal
        accent = self.banner_accent.strip()
        if accent and not re.fullmatch(r"#[0-9a-fA-F]{6}", accent):
            raise ValueError("强调色应为 #RRGGBB 或留空")
        self.banner_accent = accent
        if self.banner_left_stat not in ("notes", "memories", "open", "files"):
            raise ValueError("左侧统计只能是 notes/memories/open/files")
        if self.banner_center_stat not in ("streak", "percent"):
            raise ValueError("中间统计只能是 streak/percent")
        font = _clean_font(self.banner_font)
        if self.banner_font.strip() and not font:
            raise ValueError("字体只能是字体名（字母、数字、空格、引号、逗号与中文名）")
        self.banner_font = font
        return self


def write_banner(vault_dir: Path | str, payload: BannerWrite, *,
                 read_only: bool = False) -> dict[str, Any]:
    """只更新 身份（identity）.md 里横幅相关的 front-matter 键；其余键与正文原样保留。"""
    try:
        ensure_vault_writable(read_only, "编辑工作台横幅")
    except VaultReadOnlyError as error:
        raise HTTPException(409, str(error)) from error
    path = _identity_path(vault_dir)
    current = path.read_bytes() if path.is_file() else None
    expected = payload.revision.strip()
    if current is not None and expected != _revision(current):
        raise HTTPException(409, "档案已在别处修改：请重新读取后再保存横幅")
    if current is None and expected:
        raise HTTPException(409, "档案已被删除：请重新读取后再保存横幅")
    metadata, body = _split_front_matter(current.decode("utf-8", errors="replace")) if current else ({}, "")
    metadata["type"] = metadata.get("type") or "identity"
    metadata["updated_at"] = utc_now()
    metadata["visibility"] = metadata.get("visibility") or "private"
    metadata["mottos"] = payload.mottos
    metadata["heroes"] = payload.heroes
    metadata["hero"] = payload.heroes[0] if payload.heroes else ""
    metadata["hero_overlay"] = round(payload.hero_overlay, 2)
    if payload.hero_focal:
        metadata["hero_focal"] = payload.hero_focal
    else:
        metadata.pop("hero_focal", None)
    metadata["banner_mode"] = payload.banner_mode
    if payload.banner_accent:
        metadata["banner_accent"] = payload.banner_accent
    else:
        metadata.pop("banner_accent", None)
    metadata["banner_blur"] = round(payload.banner_blur, 1)
    metadata["banner_darkness"] = round(payload.banner_darkness, 1)
    metadata["banner_left_stat"] = payload.banner_left_stat
    metadata["banner_center_stat"] = payload.banner_center_stat
    metadata["banner_details"] = bool(payload.banner_details)
    if payload.banner_font:
        metadata["banner_font"] = payload.banner_font
    else:
        metadata.pop("banner_font", None)
    text = "---\n" + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False) + "---\n\n" + (body or "\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)
    return read_identity(vault_dir)


def create_identity_router(vault_dir: Path | str, *, read_only: bool = False, prefix: str = "/api") -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["identity"])

    @router.get("/identity")
    def identity_read():
        return read_identity(vault_dir)

    @router.put("/identity")
    def identity_write(payload: IdentityWrite):
        return write_identity(vault_dir, payload, read_only=read_only)

    # 「身份待确认」：dsh 从对话里提炼的候选先落队列，采纳才写进自我档案。
    # 逻辑在 identity_inbox，这里只做 HTTP 包装；函数内导入避免模块循环。
    @router.get("/identity/drafts")
    def identity_drafts():
        from .identity_inbox import list_drafts

        return {"items": list_drafts(vault_dir)}

    @router.post("/identity/drafts/{name}/accept")
    def identity_draft_accept(name: str):
        from .identity_inbox import accept_draft

        try:
            return accept_draft(vault_dir, name, read_only=read_only)
        except LookupError as error:
            raise HTTPException(404, str(error)) from error
        except VaultReadOnlyError as error:
            raise HTTPException(409, str(error)) from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @router.post("/identity/drafts/{name}/reject")
    def identity_draft_reject(name: str):
        from .identity_inbox import reject_draft

        try:
            return reject_draft(vault_dir, name)
        except LookupError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @router.get("/identity/hero")
    def identity_hero(i: int = Query(default=0, ge=0, le=11)):
        """工作台横幅图片：只服务 身份（identity）.md 里配置的那些，路径受保险库沙箱约束。"""
        from fastapi.responses import FileResponse

        heroes = read_identity(vault_dir).get("heroes") or []
        if i >= len(heroes):
            raise HTTPException(404, "还没有配置这张横幅图片")
        relative = heroes[i]
        if relative.lower().startswith(("http://", "https://")):
            raise HTTPException(404, "网络图片请直接使用链接")
        try:
            path = _hero_image_path(vault_dir, relative)
        except ValueError as error:
            raise HTTPException(404, "图片路径不在保险库内") from error
        if path.suffix.lower() not in HERO_SUFFIXES or not path.is_file():
            raise HTTPException(404, "横幅图片不存在")
        return FileResponse(path, headers={"Cache-Control": "no-store"})

    @router.put("/identity/banner")
    def identity_banner(payload: BannerWrite):
        return write_banner(vault_dir, payload, read_only=read_only)

    @router.post("/identity/banner/image")
    async def identity_banner_image(file: UploadFile = File(...)):
        """把用户选择的横幅图片复制到 `<个人数据库>/.torch-os/横幅背景/`。

        落点写死在后端：前端只传文件字节，返回可写进 身份（identity）.md 的库内相对路径。
        """
        try:
            ensure_vault_writable(read_only, "上传横幅图片")
        except VaultReadOnlyError as error:
            raise HTTPException(409, str(error)) from error
        raw = await file.read(MAX_BANNER_IMAGE_BYTES + 1)
        if not raw:
            raise HTTPException(400, "图片内容为空")
        if len(raw) > MAX_BANNER_IMAGE_BYTES:
            raise HTTPException(413, "横幅图片超过 10MB 上限")
        suffix = _image_suffix(raw)
        if not suffix:
            raise HTTPException(422, "仅支持 PNG / JPG / WebP / GIF 图片")
        relative_dir = f"{TORCH_DATA_DIR}/{BANNER_IMAGE_DIR}"
        try:
            destination = safe_note_path(
                Path(vault_dir), f"{relative_dir}/{_banner_filename(file.filename, suffix)}")
        except ValueError as error:
            raise HTTPException(400, "横幅图片目录不在个人数据库内") from error
        temporary = destination.with_name(destination.name + ".uploading")
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_bytes(raw)
            os.replace(temporary, destination)
        except OSError as error:
            raise HTTPException(500, f"保存横幅图片失败：{error}") from error
        finally:
            temporary.unlink(missing_ok=True)
        relative = destination.relative_to(Path(vault_dir).resolve()).as_posix()
        return {"path": relative, "name": destination.name, "size": len(raw)}

    return router


GREETING_BUCKETS = ((5, 11, "早上好"), (11, 13, "中午好"), (13, 18, "下午好"), (18, 5, "晚上好"))


def greeting_for(hour: int) -> str:
    """5–10 早上好, 11–12 中午好, 13–17 下午好, 其余（18–4）晚上好。"""
    if not 0 <= hour <= 23:
        raise ValueError("hour must be within 0..23")
    for start, end, text in GREETING_BUCKETS:
        if start < end:
            if start <= hour < end:
                return text
        elif hour >= start or hour < end:
            return text
    return "晚上好"
