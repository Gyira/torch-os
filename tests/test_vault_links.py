"""迁移链接改写：wiki link、相对链接、代码块保护。"""

from __future__ import annotations

from backend.app.vault_links import rewrite_links, scan_links

MAPPING = {
    "00-System/任务/考研规划文档.md": "00_人生路/05_项目/01_项目计划/考研规划文档.md",
    "02-Life/journal/2026-10-20.md": "00_人生路/01_日记/2026-10-20.md",
}


def test_rewrite_wiki_link_keeps_alias_and_heading():
    text = "见 [[00-System/任务/考研规划文档.md#目标|规划]] 和 ![[00-System/任务/考研规划文档.md]]"
    rewritten, changes = rewrite_links(text, MAPPING)
    assert rewritten == (
        "见 [[00_人生路/05_项目/01_项目计划/考研规划文档.md#目标|规划]] "
        "和 ![[00_人生路/05_项目/01_项目计划/考研规划文档.md]]"
    )
    assert changes


def test_rewrite_markdown_relative_link_from_same_directory():
    text = "[日记](journal/2026-10-20.md)"
    rewritten, _ = rewrite_links(
        text, MAPPING,
        source_old="02-Life/note.md", source_new="02-Life/note.md",
    )
    assert rewritten == "[日记](../00_人生路/01_日记/2026-10-20.md)"


def test_code_fences_and_inline_code_are_not_rewritten():
    text = (
        "`[[00-System/任务/考研规划文档.md]]`\n"
        "```md\n[[00-System/任务/考研规划文档.md]]\n```\n"
        "正文 [[00-System/任务/考研规划文档.md]]\n"
    )
    rewritten, _ = rewrite_links(text, MAPPING)
    assert rewritten.count("00-System/任务/考研规划文档.md") == 2
    assert "00_人生路/05_项目/01_项目计划/考研规划文档.md" in rewritten


def test_external_and_anchor_links_are_left_alone():
    text = "[网站](https://example.com) [锚点](#标题) ![图](image.png)"
    rewritten, changes = rewrite_links(text, MAPPING)
    assert rewritten == text
    assert changes == []


def test_scan_links_lists_internal_targets():
    found = scan_links(
        "[[A/B.md|别名]] [相对](C/D.md) [外链](https://x.test)",
        "00_人生路/01_日记/当天.md",
    )
    assert {item["kind"] for item in found} == {"wiki", "markdown"}
    targets = {item["target"] for item in found}
    assert "A/B.md" in targets
    assert "00_人生路/01_日记/C/D.md" in targets
