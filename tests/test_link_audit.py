import hashlib
import json
import stat
import zipfile
from pathlib import Path

import pytest

from backend.app import link_audit
from backend.app.link_audit import audit_wolai_links
from backend.app.wolai_import import WolaiImportError


def make_archive(tmp_path, entries, name="synthetic.zip"):
    path = tmp_path / name
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for member, body in entries.items():
            archive.writestr(member, body.encode("utf-8") if isinstance(body, str) else body)
    return path


def test_relative_encoded_nested_targets_titles_and_fragments(tmp_path):
    archive = make_archive(tmp_path, {
        "export/docs/source.md": (
            '# Synthetic\n[笔记](../notes/%E7%AC%94%E8%AE%B0.md#section)\n'
            '![图](<../images/a b(2).png> "Synthetic title")\n'
            "[括号](../notes/a(b(c)).md 'title')\n"
            '[编码井号](../notes/a%23b.md?mode=1#heading)\n'
            '[转义](../notes/a\\(b\\(c\\)\\).md)\n'
        ),
        "export/notes/笔记.md": "target body must not be read",
        "export/images/a b(2).png": b"not a real image",
        "export/notes/a(b(c)).md": "never read",
        "export/notes/a#b.md": "never read",
    })
    original = archive.read_bytes()
    report = audit_wolai_links(archive, ["export/docs/source.md"])
    assert [item["classification"] for item in report["items"]] == [
        "resolved_note", "resolved_attachment", "resolved_note", "resolved_note", "resolved_note",
    ]
    assert [item["line"] for item in report["items"]] == [2, 3, 4, 5, 6]
    assert report["items"][0]["resolved_path"] == "export/notes/笔记.md"
    assert report["items"][3]["resolved_path"] == "export/notes/a#b.md"
    assert report["items"][0]["warnings"]
    assert report["counts"]["total"] == 5
    assert report["counts"]["resolved_note"] == 4
    assert report["content_verified"] is report["attachments_verified"] is False
    assert archive.read_bytes() == original
    assert sorted(path.name for path in tmp_path.iterdir()) == ["synthetic.zip"]


def test_reference_links_images_collapsed_shortcut_case_and_first_definition(tmp_path):
    archive = make_archive(tmp_path, {
        "source.md": (
            '[First label][Ref ID]\n![Image][picture]\n[Shortcut][] and [shortcut]\n'
            '[Missing][undefined]\n\n[ref   id]: <a b.md> "Reference title"\n'
            "[picture]: image(1).png 'picture title'\n[Shortcut]: a%20b.md\n"
            '[REF ID]: absent.md\n'
        ),
        "a b.md": "body", "image(1).png": b"attachment",
    })
    report = audit_wolai_links(archive, ["source.md"])
    assert report["counts"]["resolved_note"] == 3
    assert report["counts"]["resolved_attachment"] == 1
    assert report["counts"]["manual_review"] == 1
    assert len(report["items"]) == 5
    assert report["items"][-1]["target"] is None
    assert report["items"][-1]["line"] == 4
    assert all("label" not in item for item in report["items"])


def test_fenced_inline_multiline_code_and_comments_are_not_links(tmp_path):
    body = (
        '```md\n[fake](fake.md)\n```\n~~~\n[also fake][ref]\n~~~~\n'
        '`[fake](fake.md)` and ``[fake ` tick](fake.md)``\n'
        '`spanning\n[fake](fake.md)\ncode`\n'
        '    [indented](fake.md)\n\t[indented](fake.md)\n'
        '<!-- [fake](fake.md) -->\n`<!--` [real](real.md)\n'
        '\\[escaped](fake.md)\n[ref]: fake.md\n'
    )
    archive = make_archive(tmp_path, {"source.md": body, "real.md": "body"})
    report = audit_wolai_links(archive, ["source.md"])
    assert report["counts"]["total"] == 1
    assert report["items"][0]["target"] == "real.md"
    assert report["items"][0]["line"] == 14


@pytest.mark.parametrize("target,expected,resolved", [
    ("../target.md", "resolved_note", "root/target.md"),
    ("./../target.md", "resolved_note", "root/target.md"),
    ("../../outer.md", "resolved_note", "outer.md"),
    ("../../../escape.md", "unsafe", None),
    ("%2E%2E/%2e%2e/%2E%2E/escape.md", "unsafe", None),
    ("%2Fetc%2Fpasswd", "unsafe", None),
    ("/root/target.md", "unsafe", None),
    ("C:/Windows/file.md", "unsafe", None),
    ("C:\\Windows\\file.md", "unsafe", None),
    ("C%3A/Windows/file.md", "unsafe", None),
    ("javascript:alert(1)", "unsafe", None),
    ("java%73cript:alert(1)", "unsafe", None),
    ("data:text/plain,content", "unsafe", None),
    ("file:///etc/passwd", "unsafe", None),
    ("ftp://example.invalid/private", "unsafe", None),
    ("//example.invalid/a.md", "external", None),
    ("https://www.wolai.com/opaque-page-id", "external", None),
    ("http://example.invalid/a.md", "external", None),
    ("mailto:someone@example.invalid", "external", None),
    ("#heading", "anchor_only", "root/docs/source.md"),
    ("?query=1#heading", "anchor_only", "root/docs/source.md"),
    ("", "anchor_only", "root/docs/source.md"),
    ("absent.md", "missing", "root/docs/absent.md"),
    ("../家庭/private.md", "restricted_target", "root/家庭/private.md"),
    ("../医疗/missing.md", "restricted_target", "root/医疗/missing.md"),
    ("../folder/", "manual_review", "root/folder"),
    ("../TARGET.md", "missing", "root/TARGET.md"),
    ("broken%ZZ.md", "manual_review", None),
    ("broken%FF.md", "manual_review", None),
    ("bad%00.md", "unsafe", None),
    ("bad%5Cfile.md", "unsafe", None),
])
def test_target_classification(tmp_path, target, expected, resolved):
    archive = make_archive(tmp_path, {
        "root/docs/source.md": f"[label]({target})",
        "root/target.md": "unread", "outer.md": "unread", "root/folder/": b"",
        "root/家庭/private.md": "sensitive unread",
    })
    report = audit_wolai_links(archive, ["root/docs/source.md"])
    assert report["counts"]["total"] == 1
    item = report["items"][0]
    assert item["classification"] == expected
    assert item["resolved_path"] == resolved


def test_only_selected_bodies_are_read_even_when_targets_exist(tmp_path, monkeypatch):
    archive = make_archive(tmp_path, {
        "source.md": '[Label sentinel](target.md) ![Other sentinel](image.bin) [restricted](家庭/private.md)',
        "target.md": "UNREAD-BODY-SENTINEL", "image.bin": b"UNREAD-ATTACHMENT-SENTINEL",
        "家庭/private.md": "UNREAD-SENSITIVE-SENTINEL",
    })
    calls = []
    original = zipfile.ZipFile.open

    def selected_only(self, name, *args, **kwargs):
        calls.append(name)
        assert name == "source.md"
        return original(self, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", selected_only)
    report = audit_wolai_links(archive, ["source.md"])
    assert calls == ["source.md"]
    assert "sentinel" not in json.dumps(report).casefold()
    assert report["counts"]["resolved_attachment"] == 1
    assert report["counts"]["restricted_target"] == 1


@pytest.mark.parametrize("suffix", ["", "#section", "?view=1", "?view=1#section"])
def test_wolai_root_relative_page_id_needs_mapping_not_path_warning(tmp_path, suffix):
    target = "/5N6d9cxjwTW3s6w8AdpkMd" + suffix
    archive = make_archive(tmp_path, {
        "source.md": f"[Platform page]({target})",
        # An identically named member must not cause a guessed mapping.
        "5N6d9cxjwTW3s6w8AdpkMd.md": "must remain unread",
    })
    report = audit_wolai_links(archive, ["source.md"])
    assert report["version"] == 3
    assert report["counts"]["manual_review"] == 1
    item = report["items"][0]
    assert item["target"] == target
    assert item["resolved_path"] is None
    assert "平台相对页面链接" in " ".join(item["warnings"])
    assert "映射" in " ".join(item["warnings"])


@pytest.mark.parametrize("target,expected", [
    ("/5N6d9cxjwTW3s6w8AdpkM", "unsafe"),
    ("/5N6d9cxjwTW3s6w8AdpkMdd", "unsafe"),
    ("/5N6d9cxjwTW3s6w8AdpkM_", "unsafe"),
    ("/5N6d9cxjwTW3s6w8AdpkMd/", "unsafe"),
    ("%2F5N6d9cxjwTW3s6w8AdpkMd", "unsafe"),
    ("/%35N6d9cxjwTW3s6w8AdpkMd", "unsafe"),
    ("C:/5N6d9cxjwTW3s6w8AdpkMd", "unsafe"),
    ("file:///5N6d9cxjwTW3s6w8AdpkMd", "unsafe"),
    ("custom:/5N6d9cxjwTW3s6w8AdpkMd", "unsafe"),
    ("//5N6d9cxjwTW3s6w8AdpkMd", "external"),
])
def test_page_id_recognition_does_not_relax_other_path_rules(tmp_path, target, expected):
    archive = make_archive(tmp_path, {"source.md": f"[address]({target})"})
    report = audit_wolai_links(archive, ["source.md"])
    assert report["items"][0]["classification"] == expected
    assert report["items"][0]["resolved_path"] is None


@pytest.mark.parametrize("selection", [[], "all", ["all"], ["*"], [None], ["source.md"] * 2, ["source.md"] * 21, ["missing.md"], ["image.png"], ["source.md", "家庭/private.md"]])
def test_rejected_selection_reads_no_bodies_and_writes_no_report(tmp_path, monkeypatch, selection):
    archive = make_archive(tmp_path, {
        "source.md": "ordinary", "image.png": b"not an image", "家庭/private.md": "private",
    })

    def no_read(*args, **kwargs):
        raise AssertionError("Rejected selection must not read any body")

    monkeypatch.setattr(zipfile.ZipFile, "open", no_read)
    with pytest.raises(WolaiImportError):
        audit_wolai_links(archive, selection, tmp_path / "reports")
    assert not (tmp_path / "reports").exists()


@pytest.mark.parametrize("malicious", ["../escape.md", "/absolute.md", "bad\\path.md", "C:/file.md"])
def test_entire_archive_safety_checked_before_read(tmp_path, monkeypatch, malicious):
    archive = make_archive(tmp_path, {"source.md": "ordinary", malicious: "bad"})
    if "\\" in malicious:
        # Windows zipfile normalizes a backslash when constructing ZipInfo;
        # modify both directory records to exercise an actual hostile archive.
        archive.write_bytes(archive.read_bytes().replace(malicious.replace("\\", "/").encode(), malicious.encode()))
    monkeypatch.setattr(zipfile.ZipFile, "open", lambda *a, **k: pytest.fail("No read before archive validation"))
    with pytest.raises(WolaiImportError):
        audit_wolai_links(archive, ["source.md"])


def test_archive_symlink_and_case_conflicts_are_rejected(tmp_path):
    path = tmp_path / "synthetic.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("source.md", "ordinary")
        info = zipfile.ZipInfo("link.md")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "source.md")
    with pytest.raises(WolaiImportError):
        audit_wolai_links(path, ["source.md"])
    other = make_archive(tmp_path, {"source.md": "ordinary", "SOURCE.md": "collision"}, "case.zip")
    with pytest.raises(WolaiImportError):
        audit_wolai_links(other, ["source.md"])


def test_report_is_stable_content_addressed_and_refuses_overwrite(tmp_path):
    archive = make_archive(tmp_path, {"a.md": "[link](b.md)", "b.md": "[link](missing.md)"})
    output = tmp_path / "reports"
    first = audit_wolai_links(archive, ["a.md"], output)
    report_path = Path(first["report_path"])
    raw = report_path.read_bytes()
    assert report_path.name == f"link-audit-{hashlib.sha256(raw).hexdigest()}.json"
    markdown_path = Path(first["markdown_path"])
    markdown = markdown_path.read_bytes()
    assert markdown_path.name == f"link-audit-{hashlib.sha256(markdown).hexdigest()}.md"
    assert markdown.decode("utf-8").startswith("# Wolai 链接核对报告（只读）")
    assert audit_wolai_links(archive, ["a.md"], output) == first
    other = audit_wolai_links(archive, ["b.md"], output)
    assert other["report_path"] != first["report_path"]
    assert other["markdown_path"] != first["markdown_path"]
    assert len(list(output.iterdir())) == 4
    stored = json.loads(raw)
    assert "report_path" not in stored
    assert "markdown_path" not in stored
    assert stored["items"] == first["items"]
    report_path.write_text("existing-unrelated-data", encoding="utf-8")
    with pytest.raises(WolaiImportError, match="未覆盖"):
        audit_wolai_links(archive, ["a.md"], output)
    assert report_path.read_text(encoding="utf-8") == "existing-unrelated-data"
    # A tampered Markdown companion must also stop the audit: the JSON is
    # identical, so the conflict is detected when publishing the report body.
    second_output = tmp_path / "reports-2"
    second = audit_wolai_links(archive, ["a.md"], second_output)
    tampered_markdown = Path(second["markdown_path"])
    tampered_markdown.write_text("existing-unrelated-markdown", encoding="utf-8")
    with pytest.raises(WolaiImportError, match="未覆盖"):
        audit_wolai_links(archive, ["a.md"], second_output)
    assert tampered_markdown.read_text(encoding="utf-8") == "existing-unrelated-markdown"


def test_attachment_and_sensitive_summary_with_human_readable_report(tmp_path):
    archive = make_archive(tmp_path, {
        "docs/source.md": (
            "![present](images/present.png)\n"
            "![gone](images/gone.png)\n"
            "[restricted photo](../家庭/photo.png)\n"
            "[restricted note](../家庭/secret.md)\n"
            "[missing note](absent.md)\n"
        ),
        "docs/images/present.png": b"present",
        "专家资料/家庭/photo.png": b"restricted",
    })
    output = tmp_path / "reports"
    report = audit_wolai_links(archive, ["docs/source.md"], output)
    counts = report["counts"]
    assert counts["resolved_attachment"] == 1
    assert counts["missing"] == 2
    assert counts["restricted_target"] == 2
    summary = report["summary"]
    assert (summary["notes"], summary["links"]) == (1, 5)
    assert summary["needs_review"] == 4 and summary["unresolved"] == 2
    attachments = report["attachments"]
    assert (attachments["referenced"], attachments["found"], attachments["missing"], attachments["restricted"]) == (3, 1, 1, 1)
    assert attachments["archive_count"] == 2 and attachments["truncated"] is False
    assert [item["classification"] for item in attachments["items"]] == [
        "resolved_attachment", "missing", "restricted_target",
    ]
    hints = report["sensitive_hints"]
    assert hints["archive_entries"] == 1 and hints["restricted_targets"] == 2
    markdown = Path(report["markdown_path"]).read_text(encoding="utf-8")
    assert "## 附件提示" in markdown and "引用的附件路径：3 个（存在 1 · 缺失 1 · 受限 1）" in markdown
    assert "## 缺失的附件" in markdown and "images/gone.png" in markdown
    assert "没有发现缺失的附件路径" not in markdown
    assert "## 需要人工核对" in markdown and "受限目标" in markdown
    assert "## 敏感提示" in markdown and "命中敏感目录名的条目：1 条" in markdown
    assert "未调用模型" in markdown


def test_malformed_link_not_claimed_resolved_and_output_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(link_audit, "MAX_LINKS_PER_NOTE", 2)
    archive = make_archive(tmp_path, {
        "source.md": '[link](target.md "unterminated)\n[second](target.md)\n[third](target.md)',
        "target.md": "unread",
    })
    report = audit_wolai_links(archive, ["source.md"])
    assert report["items"][0]["classification"] == "manual_review"
    assert report["items"][0]["resolved_path"] is None
    assert report["counts"]["total"] == 2
    assert report["sources"][0]["limit_reached"] is True
    assert report["sources"][0]["warnings"]


@pytest.mark.parametrize("body", [b"\xff", b"data\x00content"])
def test_non_utf8_or_binary_markdown_rejected(tmp_path, body):
    archive = make_archive(tmp_path, {"source.md": body})
    with pytest.raises(WolaiImportError):
        audit_wolai_links(archive, ["source.md"])


def test_bom_crlf_and_empty_file_line_numbers(tmp_path):
    archive = make_archive(tmp_path, {"source.md": '\ufeff# note\r\n\r\n[link](empty.md)\r\n', "empty.md": ""})
    report = audit_wolai_links(archive, ["source.md", "empty.md"])
    assert report["items"][0]["line"] == 3
    assert report["sources"][1]["links_found"] == 0
