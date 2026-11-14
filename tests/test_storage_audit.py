from backend.app.storage_audit import audit_references


def write(root, path, text):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def test_audit_resolves_relative_and_unique_wikilinks_without_changes(tmp_path):
    note = write(tmp_path, "03_知识库/笔记/今天.md", "[图](../图/a%20b.png)\n[[背景]]")
    write(tmp_path, "03_知识库/背景.md", "context")
    image = tmp_path / "03_知识库/图/a b.png"
    image.parent.mkdir()
    image.write_bytes(b"image")
    before = note.read_bytes()
    report = audit_references(tmp_path)
    assert len(report["references"]) == 2
    assert all(r["status"] == "resolved" for r in report["references"])
    assert note.read_bytes() == before and not report["safe_to_migrate"]


def test_duplicate_stems_remain_ambiguous_and_code_is_not_a_link(tmp_path):
    write(tmp_path, "03_知识库/今天.md", "[[重复]]\n`[[code]]`\n```md\n[[ignored]]\n```\n[外部](https://example.com)")
    write(tmp_path, "03_知识库/a/重复.md", "a")
    write(tmp_path, "03_知识库/b/重复.md", "b")
    result = audit_references(tmp_path)
    assert len(result["references"]) == 1
    assert result["references"][0]["status"] == "ambiguous"


def test_canvas_not_falsely_reported_as_fully_checked(tmp_path):
    write(tmp_path, "03_知识库/map.canvas", '{"nodes":[]}')
    result = audit_references(tmp_path)
    assert result["blocked"][0]["reason"] == "canvas_requires_separate_audit"
