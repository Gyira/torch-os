"""Restore/purge whole notebook batches without trusting manifest paths."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from . import vault_layout as layout
from .memory_store import operation_lock
from .library_management import restore_notebook_title
from .storage_layout import checked_path, workspace_path, workspace_relative

TRASH = ".torch-os/本子回收站"


def _batch(vault: Path, relative: str) -> Path:
    parts = str(relative).replace("\\", "/").split("/")
    if len(parts) != 3 or "/".join(parts[:2]) != TRASH or parts[-1] in {"", ".", ".."}:
        raise ValueError("本子回收批次路径无效")
    root = checked_path(workspace_path(vault, TRASH))
    batch = checked_path(workspace_path(vault, relative))
    if batch.parent != root or not batch.is_dir():
        raise ValueError("本子回收批次已经不存在")
    return batch


def _tree_size(root: Path) -> tuple[int, int]:
    size = count = 0
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = checked_path(Path(parent) / name)
            if path.is_file():
                size += path.stat().st_size
                if path.suffix.lower() == ".md": count += 1
    return size, count


def _manifest(batch: Path) -> dict[str, Any]:
    path = checked_path(batch / "manifest.json")
    if path.is_file():
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict): raise ValueError("本子恢复清单无效")
        return value
    # Older deletion swallowed manifest write failures. Recover from its fixed layout.
    containers = list((batch / "notebooks").glob("*/*"))
    if len(containers) != 1: raise ValueError("缺少可识别的本子恢复清单")
    target = checked_path(containers[0])
    pillar, name = target.parent.name, target.name
    builtin = name in layout.BUILTIN_NOTEBOOKS.get(pillar, {})
    key = name if builtin else layout.custom_notebook_key(name, pillar)
    return {"pillar": pillar, "key": key, "builtin": builtin,
            "name": layout.builtin_notebook_title(pillar, key) if builtin else name,
            "inferred_manifest": True}


def _mappings(vault: Path, batch: Path, manifest: dict) -> list[tuple[Path, Path]]:
    pillar, key = str(manifest.get("pillar") or ""), str(manifest.get("key") or "")
    builtin = bool(manifest.get("builtin"))
    if layout.is_protected_notebook(pillar, key): raise ValueError("受保护的默认本子不能在此操作")
    if builtin:
        chapters = layout.builtin_notebook_chapters(pillar, key)
        if not chapters: raise ValueError("本子恢复清单中的章节无效")
        allowed = [layout.chapter_dir(chapter) for chapter in chapters]
        common = os.path.commonpath(allowed).replace("\\", "/")
        if common != layout.PILLAR_DIRS.get(pillar) and common not in allowed:
            allowed.insert(0, common)
        base = batch / "notebooks" / pillar / key
        pairs = []
        # A newer batch moves the entire shared folder. Older batches hold individual chapters.
        originals = manifest.get("original_paths")
        if not isinstance(originals, list):
            # The shared directory also holds non-note attachments in both legacy/new batches.
            common_source = base / Path(*common.split("/")[1:])
            originals = [common] if common in allowed and common_source.is_dir() else [layout.chapter_dir(chapter) for chapter in chapters]
        for original in originals:
            if original not in allowed: raise ValueError("恢复目标不属于这个本子")
            source = base / Path(*original.split("/")[1:])
            if manifest.get("inferred_manifest") and not source.is_dir(): continue
            pairs.append((source, workspace_path(vault, original)))
        if manifest.get("inferred_manifest") and not pairs: raise ValueError("找不到本子的原始文件夹")
    else:
        sub = layout.sub_notebook_parts(key)
        if sub:
            chapter, name = sub
            if not manifest.get("sub") or layout.CHAPTER_BY_KEY[chapter][1] != pillar:
                raise ValueError("子本子恢复位置无效")
            original = layout.sub_notebook_path(name, chapter)
            if manifest.get("original_path", original) != original:
                raise ValueError("子本子原路径校验失败")
            pairs = [(
                batch / "notebooks" / pillar / f"sub-{chapter}" / name,
                workspace_path(vault, original),
            )]
        else:
            name = layout.custom_notebook_name(key)
            if not name or layout.custom_notebook_pillar(key) != pillar: raise ValueError("自建本子恢复位置无效")
            original = layout.custom_notebook_path(name, pillar)
            if manifest.get("original_path", original) != original: raise ValueError("本子原路径校验失败")
            pairs = [(batch / "notebooks" / pillar / name, workspace_path(vault, original))]
    for source, target in pairs:
        if not checked_path(source).is_relative_to(batch): raise ValueError("回收目录越界")
        if not checked_path(target).is_relative_to(vault): raise ValueError("恢复目录越界")
        if not source.is_dir(): raise ValueError("部分本子文件夹缺失，请检查回收批次")
    return pairs


def list_notebook_recycle(vault: Path) -> list[dict[str, Any]]:
    root = workspace_path(vault, TRASH)
    if not root.is_dir(): return []
    result = []
    for child in sorted(root.iterdir()):
        try:
            relative = f"{TRASH}/{child.name}"
            batch = _batch(vault, relative)
            manifest = _manifest(batch)
            size, count = _tree_size(batch)
            reason = ""
            try: pairs = _mappings(vault, batch, manifest)
            except ValueError as error: pairs, reason = [], str(error)
            paths = [workspace_relative(vault, target).as_posix() for _, target in pairs]
            result.append({"source": "notebooks", "kind": "notebook", "title": manifest.get("display_title") or manifest.get("name") or child.name,
                           "pillar": manifest.get("pillar"), "chapter": "", "key": manifest.get("key"),
                           "trash_path": relative, "batch_path": relative, "original_path": "、".join(paths),
                           "original_paths": paths, "deleted_at": manifest.get("deleted_at") or child.name,
                           "bytes": size, "count": count, "restorable": not reason,
                           "excerpt": reason or ("已从旧目录结构找回，可整体恢复" if manifest.get("inferred_manifest") else "整本恢复会连同目录、笔记和附件一起恢复")})
        except (OSError, ValueError):
            # Still surface an ordinary orphan batch; do not make missing metadata invisible.
            try:
                batch = _batch(vault, f"{TRASH}/{child.name}")
                size, count = _tree_size(batch)
                result.append({"source": "notebooks", "kind": "notebook", "title": f"待检查本子 · {child.name}",
                               "pillar": "other", "trash_path": f"{TRASH}/{child.name}", "batch_path": f"{TRASH}/{child.name}",
                               "restorable": False, "bytes": size, "count": count,
                               "excerpt": "恢复清单缺失或损坏，原文件仍保留在这个回收批次中。"})
            except (OSError, ValueError): pass
    return result


def restore_notebook(vault: Path, relative: str) -> None:
    with operation_lock(f"notebook-recycle|{vault}"):
        batch = _batch(vault, relative)
        manifest = _manifest(batch)
        pairs = _mappings(vault, batch, manifest)
        _tree_size(batch)  # Reject nested links before any move.
        for source, target in pairs:
            if target.exists(): raise ValueError("原位置已有同名文件夹，未覆盖；请先在文件管理中处理重名")
        moved = []
        created = []
        try:
            for source, target in pairs:
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, target)
                moved.append((source, target))
            if manifest.get("sub"):
                layout.register_notebook(vault, str(manifest.get("key") or ""))
            restore_notebook_title(vault, manifest)
            if manifest.get("builtin"):
                if not pairs:
                    for chapter in layout.builtin_notebook_chapters(manifest["pillar"], manifest["key"]):
                        target = checked_path(workspace_path(vault, layout.chapter_dir(chapter)))
                        if not target.exists():
                            target.mkdir(parents=True)
                            created.append(target)
                layout.mark_notebook_deleted(vault, manifest["pillar"], manifest["key"], deleted=False)
        except (OSError, ValueError):
            for target in reversed(created):
                try: target.rmdir()
                except OSError: pass
            for source, target in reversed(moved):
                source.parent.mkdir(parents=True, exist_ok=True)
                os.replace(target, source)
            raise
        # Only empty containers + manifest may remain after a successful restore.
        (batch / "manifest.json").unlink(missing_ok=True)
        for parent, _dirs, _files in os.walk(batch, topdown=False):
            try: Path(parent).rmdir()
            except OSError: pass


def purge_notebook(vault: Path, relative: str) -> None:
    with operation_lock(f"notebook-recycle|{vault}"):
        batch = _batch(vault, relative)
        _tree_size(batch)
        try:
            manifest = _manifest(batch)
            pairs = _mappings(vault, batch, manifest)
        except ValueError:
            manifest, pairs = {}, []
        # Verified immediate child of the configured notebook trash root, without reparse points.
        shutil.rmtree(batch)
        # 彻底删除后清掉本子登记（含整夹删除后残留的下层登记）。
        try:
            key = str(manifest.get("key") or "")
            if key:
                layout.unregister_notebook(vault, key, include_descendants=True)
        except OSError:
            pass
        # Old releases moved chapters but left empty notebook parent directories.
        # Remove only empty ancestors, never a live file or the pillar root.
        # 子本子最多清理到所属章节目录为止，不把固定章节本身删掉。
        chapter = str(manifest.get("chapter") or "")
        if manifest.get("sub") and chapter in layout.SUB_NOTEBOOK_CHAPTERS:
            pillar_root = vault / layout.chapter_dir(chapter)
        else:
            pillar_root = vault / layout.PILLAR_DIRS.get(manifest.get("pillar"), "")
        for _source, target in pairs:
            parent = target.parent
            while parent != pillar_root and parent.is_relative_to(pillar_root):
                try: checked_path(parent).rmdir()
                except (OSError, ValueError): break
                parent = parent.parent
