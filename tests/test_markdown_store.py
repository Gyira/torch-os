"""标准 Markdown 存储层的自测：读取、修订号、冲突写回、行级定位。

任务/待办/身份都靠这一层保证「只改目标行、别人改过就 409、CRLF 原样保留、不留 .tmp」。
这里直接测协议本身，业务模块的测试继续钉各自的响应形状。
"""

from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.app.markdown_store import guarded_write, heading_line, load_note, revision, section_bounds


def test_missing_file_is_a_normal_state(tmp_path: Path):
    loaded = load_note(tmp_path / "missing.md")
    assert loaded["exists"] is False
    assert loaded["revision"] == ""
    assert loaded["lines"] == []


def test_load_strips_bom_and_preserves_crlf(tmp_path: Path):
    path = tmp_path / "note.md"
    path.write_bytes(b"\xef\xbb\xbf---\r\ntype: x\r\n---\r\n\r\n## A\r\n- [ ] x\r\n")
    loaded = load_note(path)
    assert loaded["exists"] is True
    assert loaded["newline"] == "\r\n"
    assert loaded["body_start"] == 3
    assert loaded["revision"] == revision(path.read_bytes())
    assert "- [ ] x" in loaded["lines"]


def test_oversize_and_non_utf8_are_rejected(tmp_path: Path):
    oversized = tmp_path / "big.md"
    oversized.write_bytes(b"a" * (1024 * 1024 + 1))
    with pytest.raises(HTTPException) as error:
        load_note(oversized)
    assert error.value.status_code == 413

    invalid = tmp_path / "bad.md"
    invalid.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(HTTPException) as error:
        load_note(invalid)
    assert error.value.status_code == 409


def test_guarded_write_refuses_when_the_file_changed_underneath(tmp_path: Path):
    path = tmp_path / "note.md"
    path.write_text("a\n", encoding="utf-8")
    loaded = load_note(path)
    path.write_text("b\n", encoding="utf-8")
    with pytest.raises(HTTPException) as error:
        guarded_write(loaded, ["c", ""])
    assert error.value.status_code == 409
    assert not path.with_suffix(path.suffix + ".tmp").exists()


def test_guarded_write_keeps_crlf_and_leaves_no_tmp(tmp_path: Path):
    path = tmp_path / "note.md"
    path.write_bytes(b"a\r\nb\r\n")
    loaded = load_note(path)
    written = guarded_write(loaded, ["a", "c", ""])
    assert path.read_bytes() == "a\r\nc\r\n".encode("utf-8")
    assert written == path.read_bytes()
    assert not path.with_suffix(path.suffix + ".tmp").exists()


def test_heading_helpers_skip_fences_and_comments():
    lines = [
        "# 标题", "```", "## 不算", "```", "<!--", "## 也不算", "-->",
        "## 真的", "- [ ] x", "## 下一节",
    ]
    assert heading_line(lines, 0, "真的") == 7
    assert heading_line(lines, 0, "不算") == -1
    assert heading_line(lines, 0, "也不算") == -1
    assert section_bounds(lines, 0, 7) == (8, 9)
