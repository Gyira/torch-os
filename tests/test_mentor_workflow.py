"""「与神对话」的三件套：规则随仓库走、归档只落四库章节、记忆内核读得懂身份文件。

诉求（2026-10-27）：换一个库就等于换一个人，但规则不能跟着库走；上下文有限，
所以每次开场要有一张几百字的「记忆内核」，每次结论要能靠归档回流。

这些用例钉住三件事：
1. `dsh/skills/torch-mentor/` 就是 dsh 的本地 skill 根（`$DSH_AGENTS_HOME/skills`），
   规则里出现的目录必须是 vault_layout 里真实存在的章节，不能漂移；
2. `scripts/archive_note.py` 只往四库章节里写、绝不覆盖、dry-run 不落盘；
3. `session_brief.py --memory` 读得到身份文件（含 `mottos` 列表），且不把空日记模板
   当成「今天写了」。
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.app import vault_layout

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "dsh" / "skills" / "torch-mentor"
SKILL = SKILL_DIR / "SKILL.md"
ARCHIVE = ROOT / "scripts" / "archive_note.py"
BRIEF = ROOT / "scripts" / "session_brief.py"
SHANGHAI = ZoneInfo("Asia/Shanghai")


def run(script: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(script), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    return vault


def read_note(path: Path) -> tuple[dict[str, str], str]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---"), text[:80]
    parts = text.split("---", 2)
    meta: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" in line and not line.startswith((" ", "-")):
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta, parts[2]


def test_skill_lives_in_the_shared_agents_home() -> None:
    """torch 把 DSH_AGENTS_HOME 指向 dsh/，dsh 会顺带扫 dsh/skills —— 规则跟着仓库走。"""
    assert SKILL.is_file(), SKILL
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---"), "缺 YAML frontmatter，dsh 会静默丢掉这个 skill"
    head = text.split("---", 2)[1]
    assert "name: torch-mentor" in head
    assert re.search(r"^description: .{10,}", head, re.M), "description 是 dsh 目录里唯一的发现线索"
    assert "whenToUse:" in head
    assert (SKILL_DIR / "references" / "note-templates.md").is_file()


def test_skill_directories_match_the_live_layout() -> None:
    """规则里写的目录必须真实存在；四库改版时这条会先响。"""
    text = SKILL.read_text(encoding="utf-8")
    known = [chapter[3] for chapter in vault_layout.CHAPTERS]
    known += list(vault_layout.PILLAR_DIRS.values())
    tokens = re.findall(r"`(0[0-3]_[^`]+)`", text)
    assert tokens, "规则里至少要给出几个真实目录"
    for token in tokens:
        assert any(token == item or token.startswith(item + "/") for item in known), f"规则里的路径不存在：{token}"


def test_skill_never_hardcodes_a_person() -> None:
    """规则随仓库发布，不能把某个库的个人信息写死在里面。"""
    text = SKILL.read_text(encoding="utf-8")
    assert "不属于数据库" in text
    for leaked in ("华东交通", "铁路", "考研", "南昌"):
        assert leaked not in text, f"规则里出现了个人事实：{leaked}"


def test_skill_routes_identity_facts_through_the_review_queue() -> None:
    """身份信息不能直接落档：规则里必须指向候选队列，而不是写身份文件。"""
    text = SKILL.read_text(encoding="utf-8")
    assert "identity_draft.py" in text
    assert "身份待确认" in text
    assert "--kind field" in text and "--kind memory" in text


def test_archive_writes_into_the_chapter(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    draft = tmp_path / "draft.md"
    draft.write_text("> 上班和备考撞车时先保收入\n\n## 结论\n\n2027-03 前不辞职。\n", encoding="utf-8")

    result = run(ARCHIVE, "--vault", str(vault), "--chapter", "reflection",
                 "--title", "2026-10-27 考研与上班的取舍", "--tags", "考研,取舍",
                 "--body-file", str(draft))
    assert result.returncode == 0, result.stderr
    assert "已归档" in result.stdout and "[[" in result.stdout

    written = vault / vault_layout.chapter_dir("reflection") / "2026-10-27_考研与上班的取舍.md"
    assert written.is_file(), result.stdout
    meta, body = read_note(written)
    assert meta["chapter"] == "reflection"
    assert meta["type"] == "reflection"
    assert meta["pillar"] == "life"
    assert meta["source"] == "dsh"
    assert "先保收入" in body


def test_archive_never_overwrites(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    draft = tmp_path / "draft.md"
    draft.write_text("> 第一版\n\n正文\n", encoding="utf-8")
    args = ("--vault", str(vault), "--chapter", "idea", "--title", "同名笔记",
            "--body-file", str(draft))
    assert run(ARCHIVE, *args).returncode == 0
    assert run(ARCHIVE, *args).returncode == 0
    directory = vault / vault_layout.chapter_dir("idea")
    assert (directory / "同名笔记.md").is_file()
    assert (directory / "同名笔记-2.md").is_file()


def test_archive_dry_run_writes_nothing(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    draft = tmp_path / "draft.md"
    draft.write_text("> 摘要\n\n正文\n", encoding="utf-8")
    result = run(ARCHIVE, "--vault", str(vault), "--chapter", "concept",
                 "--title", "概念卡样例", "--body-file", str(draft), "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "dry-run" in result.stdout
    assert "03_知识库/04_概念卡片/概念卡样例.md" in result.stdout
    assert not list(vault.rglob("*.md")), "dry-run 不能落盘"


def test_archive_rejects_unknown_chapter(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    result = run(ARCHIVE, "--vault", str(vault), "--chapter", "反思",
                 "--title", "随便", "--body", "正文")
    assert result.returncode == 2
    assert "--list-chapters" in result.stderr


def test_archive_warns_when_summary_is_missing(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    result = run(ARCHIVE, "--vault", str(vault), "--chapter", "reflection",
                 "--title", "没有摘要", "--body", "## 背景\n\n正文\n")
    assert result.returncode == 0
    assert "> 摘要" in result.stderr


def test_memory_card_reads_identity_and_mottos(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    identity = vault / "01_自我档案/01_内核与三观/身份（identity）.md"
    identity.parent.mkdir(parents=True)
    identity.write_text("---\ntype: identity\nname: 小林\nmottos:\n- '看更大的世界'\n---\n\n", encoding="utf-8")

    result = run(BRIEF, "--vault", str(vault), "--memory")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "记忆内核" in out
    assert "小林" in out
    assert "看更大的世界" in out, "座右铭存在 mottos 列表里，也要读得到"
    assert "还没有由 dsh 归档的结论" in out
    assert len(out) <= 2000, "内核卡是常驻上下文，必须够短"


def test_empty_diary_template_is_not_counted_as_content(tmp_path: Path) -> None:
    """日记只有模板（粗体小标题 + 空横线）时，不能说「今天写了」。"""
    vault = make_vault(tmp_path)
    day = datetime.now(SHANGHAI).date().isoformat()
    diary = vault / "00_人生路/01_日记" / f"{day}.md"
    diary.parent.mkdir(parents=True)
    diary.write_text("---\ntype: journal\n---\n\n## 今日回顾\n\n### 学习\n-\n\n"
                     "**今天取得了哪些进步？**\n-\n", encoding="utf-8")

    result = run(BRIEF, "--vault", str(vault), "--memory")
    assert result.returncode == 0, result.stderr
    assert "日记 0/7 天" in result.stdout


def test_full_brief_puts_the_memory_card_first(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    result = run(BRIEF, "--vault", str(vault), "--full")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert out.index("记忆内核") < out.index("四库简报")
    assert "关于我" not in out, "--full 已经带记忆内核，简报不再重复身份段"
