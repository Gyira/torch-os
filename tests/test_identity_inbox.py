"""身份候选队列：dsh 只能投递，进不进四库由我在「自我档案」点头。

诉求（2026-10-27）：对话里经常直接暴露「我是谁」，但这些话不能自动进档案——
一句听错就会把错误的三观写进内核。所以三段式：

    dsh 投卡片 → 我看「身份待确认」→ 采纳写进身份字段/记忆与自传，丢弃只挪走卡片

这些用例钉住：只在四库章节内写、绝不覆盖已有字段、路径不能逃出队列目录、
采纳后卡片归档留痕、丢弃时四库一个字不动。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from backend.app import identity_inbox, vault_layout

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "identity_draft.py"


def make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    return vault


def write_draft(vault: Path, name: str, *, kind: str = "field", field: str = "name",
                text: str = "称呼我小林", evidence: str = "我说：以后叫我小林",
                created: str = "2026-10-27T22:00:00+08:00") -> Path:
    directory = vault / identity_inbox.INBOX_DIR
    directory.mkdir(parents=True, exist_ok=True)
    body = ["---", f"kind: {kind}"]
    if kind == "field":
        body.append(f"field: {field}")
    body += [f"text: {text}", f"evidence: {evidence}", f"created: {created}",
             "source: dsh", "---", ""]
    path = directory / name
    path.write_text("\n".join(body), encoding="utf-8", newline="\n")
    return path


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CLI), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def test_list_skips_empty_text_and_sorts_newest_first(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    write_draft(vault, "old.md", text="旧事实", created="2026-10-20T10:00:00+08:00")
    write_draft(vault, "new.md", text="新事实", created="2026-10-27T10:00:00+08:00")
    write_draft(vault, "blank.md", text="")
    items = identity_inbox.list_drafts(vault)
    assert [item["text"] for item in items] == ["新事实", "旧事实"]
    assert items[0]["kind"] == "field" and items[0]["field"] == "name"
    assert items[0]["field_label"] == "称呼"


def test_accept_field_writes_a_memory_note_not_identity(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    identity = vault / "01_自我档案/01_内核与三观/身份（identity）.md"
    identity.parent.mkdir(parents=True)
    identity.write_text("---\ntype: identity\nmottos:\n- '看更大的世界'\n---\n\n原来的笔记\n",
                        encoding="utf-8")
    write_draft(vault, "draft.md")

    result = identity_inbox.accept_draft(vault, "draft.md")

    assert result["target"] == "memory" and result["field"] == "name"
    note = vault / result["path"]
    body = note.read_text(encoding="utf-8")
    assert "称呼我小林" in body and "候选字段" in body
    assert result["path"].startswith(vault_layout.chapter_dir("memory"))
    text = identity.read_text(encoding="utf-8")
    assert "name:" not in text, "候选不再直接写身份"
    assert "看更大的世界" in text and "原来的笔记" in text
    assert not (vault / identity_inbox.INBOX_DIR / "draft.md").exists()
    assert (vault / identity_inbox.ACCEPTED_DIR / "draft.md").is_file()


def test_accept_never_overwrites_a_filled_identity_field(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    identity = vault / "01_自我档案/01_内核与三观/身份（identity）.md"
    identity.parent.mkdir(parents=True)
    identity.write_text("---\ntype: identity\nname: 原来的名字\n---\n\n", encoding="utf-8")
    write_draft(vault, "draft.md")

    result = identity_inbox.accept_draft(vault, "draft.md")
    assert result["target"] == "memory"
    assert "原来的名字" in identity.read_text(encoding="utf-8")
    assert not (vault / identity_inbox.INBOX_DIR / "draft.md").exists()


def test_accept_memory_writes_a_note_in_the_memory_chapter(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    write_draft(vault, "draft.md", kind="memory", field="",
                text="我是铁路线路工，想考交通运输的研究生", evidence="我说：我在工务段当线路工")

    result = identity_inbox.accept_draft(vault, "draft.md")

    assert result["target"] == "memory"
    relative = result["path"]
    assert relative.startswith(vault_layout.chapter_dir("memory") + "/"), relative
    body = (vault / relative).read_text(encoding="utf-8")
    assert "> 我是铁路线路工" in body
    assert "来自对话" in body


def four_library(vault: Path) -> list[str]:
    """四库里的 Markdown（跳过 .torch-os 这类系统目录）。"""
    return sorted(path.relative_to(vault).as_posix() for path in vault.rglob("*.md")
                  if not vault_layout.is_skipped(path.relative_to(vault).as_posix()))


def test_reject_only_moves_the_card(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    write_draft(vault, "draft.md")
    before = four_library(vault)

    result = identity_inbox.reject_draft(vault, "draft.md")

    assert result["rejected"] is True
    assert (vault / identity_inbox.REJECTED_DIR / "draft.md").is_file()
    assert four_library(vault) == before, "丢弃不能动四库"


def test_names_cannot_escape_the_queue(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    for name in ("../identity.md", "sub/dir.md", "", "not-markdown.txt"):
        with pytest.raises(ValueError):
            identity_inbox.reject_draft(vault, name)


def test_cli_adds_one_draft_and_dedupes(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    first = run("add", "--vault", str(vault), "--kind", "field", "--field", "name",
                "--text", "称呼我小林", "--evidence", "我说：以后叫我小林")
    assert first.returncode == 0, first.stderr
    again = run("add", "--vault", str(vault), "--kind", "field", "--field", "name",
                "--text", "称呼我小林")
    assert again.returncode == 0, again.stderr
    assert "没有重复投递" in again.stdout
    assert len(list((vault / identity_inbox.INBOX_DIR).glob("*.md"))) == 1


def test_cli_rejects_bad_field_and_long_text(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    bad_field = run("add", "--vault", str(vault), "--kind", "field", "--field", "nickname",
                    "--text", "随便")
    assert bad_field.returncode == 2 and "--field" in bad_field.stderr
    too_long = run("add", "--vault", str(vault), "--kind", "memory", "--text", "长" * 201)
    assert too_long.returncode == 2
