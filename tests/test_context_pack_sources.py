from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.app import context_pack


def test_source_walk_prunes_excluded_trees_before_descent(tmp_path, monkeypatch):
    pillar = tmp_path / "03_知识库"
    for name in ["笔记", "微信聊天", "backup", "_系统", ".obsidian", "冲突副本", "_Torch"]:
        folder = pillar / name / "deep" / "deeper"
        folder.mkdir(parents=True)
        (folder / "note.md").write_text("test", encoding="utf-8")
    (pillar / "visible.md").write_text("hello", encoding="utf-8")
    (pillar / "secret-key.md").write_text("excluded", encoding="utf-8")
    visited = []
    walk = context_pack.os.walk

    def observed(*args, **kwargs):
        for directory, folders, files in walk(*args, **kwargs):
            visited.append(Path(directory).relative_to(tmp_path).as_posix())
            yield directory, folders, files

    monkeypatch.setattr(context_pack.os, "walk", observed)
    found = sorted(path.relative_to(tmp_path).as_posix() for path in context_pack.source_candidates(tmp_path))
    assert found == ["03_知识库/visible.md", "03_知识库/笔记/deep/deeper/note.md"]
    assert len(visited) == 4  # The six excluded deep trees are never entered.
    for path in found:
        assert context_pack.source_path(tmp_path, path).is_file()


def test_source_walk_skips_linked_pillar_and_nested_junction(tmp_path, monkeypatch):
    for relative in ["03_知识库/link/private.md", "02_生活运营/private.md", "03_知识库/public.md"]:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("test", encoding="utf-8")
    monkeypatch.setattr(Path, "is_junction", lambda path: path.name in {"link", "02_生活运营"})
    assert [path.name for path in context_pack.source_candidates(tmp_path)] == ["public.md"]


def test_source_walk_reports_unreadable_directory(tmp_path, monkeypatch):
    (tmp_path / "03_知识库").mkdir()

    def denied(*args, **kwargs):
        kwargs["onerror"](PermissionError("denied"))
        yield  # This test double must be an iterator, like os.walk.

    monkeypatch.setattr(context_pack.os, "walk", denied)
    with pytest.raises(HTTPException) as error:
        list(context_pack.source_candidates(tmp_path))
    assert error.value.status_code == 503
    assert "访问权限" in error.value.detail
