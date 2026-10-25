"""四库浏览、笔记读取与搜索（2026-10-26 起四库只读，写入在 Obsidian）。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app import pillar_notes
from backend.app.application import create_app
from backend.app.db import Database


def make_client(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir(parents=True)
    app = create_app(
        Database(tmp_path / "torch_os.sqlite3"), vault,
        security_environ={}, env_file=tmp_path / ".env")
    client = TestClient(app)
    client.get("/")
    return client, vault


def test_manifest_lists_four_pillars(tmp_path):
    client, _ = make_client(tmp_path)
    payload = client.get("/api/pillars").json()
    assert payload["layout_version"] == "pillars-v2"
    assert [item["key"] for item in payload["pillars"]] == [
        "life", "self", "ops", "knowledge",
    ]
    inbox = next(chapter for pillar in payload["pillars"] for chapter in pillar["chapters"]
                 if chapter["key"] == "inbox")
    assert inbox["path"] == "03_知识库/00_收件箱"
    life = next(pillar for pillar in payload["pillars"] if pillar["key"] == "life")
    assert [group["key"] for group in life["groups"]] == [
        "insight", "review", "goal", "project",
    ]
    reflection = next(chapter for chapter in life["chapters"] if chapter["key"] == "reflection")
    assert reflection["path"] == "00_人生路/02_反思复盘/01_反思"
    project = next(chapter for chapter in life["chapters"] if chapter["key"] == "project_plan")
    assert project["path"] == "00_人生路/05_项目/01_项目计划"
    assert all(pillar["groups"] == [] for pillar in payload["pillars"]
               if pillar["key"] != "life")


def test_inbox_note_is_listed_in_chapter_and_overview(tmp_path):
    client, vault = make_client(tmp_path)
    note = pillar_notes.create_note(
        vault, pillar="knowledge", chapter="inbox",
        title="一条灵感", body="值得记住的想法", tags=["灵感"])
    assert note["pillar"] == "knowledge"
    assert note["chapter"] == "inbox"
    assert note["status"] == "inbox"

    listed = client.get("/api/pillars/knowledge/notes?chapter=inbox").json()["items"]
    assert [item["title"] for item in listed] == ["一条灵感"]
    overview = client.get("/api/pillars/knowledge/overview").json()
    assert overview["inbox_count"] == 1


def test_read_and_search(tmp_path):
    client, vault = make_client(tmp_path)
    note = pillar_notes.create_note(vault, title="待提炼", body="原始内容")
    read = client.get("/api/notes", params={"path": note["path"]}).json()
    assert read["body"] == "原始内容"

    missing = client.get("/api/notes", params={"path": "03_知识库/00_收件箱/不存在.md"})
    assert missing.status_code == 404

    found = client.get("/api/search", params={"q": "提炼"}).json()["items"]
    assert any(item["path"] == note["path"] for item in found)


def test_create_note_does_not_overwrite_same_title(tmp_path):
    _, vault = make_client(tmp_path)
    first = pillar_notes.create_note(vault, title="同名", body="一")
    second = pillar_notes.create_note(vault, title="同名", body="二")
    assert first["path"] != second["path"]
    assert (vault / first["path"]).is_file() and (vault / second["path"]).is_file()


def test_steward_suggestions_are_deterministic_and_inbox_is_listed(tmp_path):
    from backend.app.steward_policy import suggest
    assert suggest("整理收件箱五步法", "第一步……第二步……")["chapter"] == "method_general"
    assert suggest("读书笔记：原则", "作者说……")["chapter"] == "reading"
    assert suggest("一个想法", "我觉得这件事很触动我")["chapter"] == "idea"
    assert suggest("随手记", "")["chapter"] == "inbox"

    client, vault = make_client(tmp_path)
    note = pillar_notes.create_note(
        vault, title="整理收件箱五步法", body="第一步：列清单；第二步：分类。")
    items = client.get("/api/steward/inbox-suggestions").json()["items"]
    assert items and items[0]["path"] == note["path"]
    assert items[0]["chapter"] == "method_general"


def test_life_overview_marks_the_last_seven_days(tmp_path):
    from backend.app.habits import shanghai_today

    client, vault = make_client(tmp_path)
    today = shanghai_today().isoformat()
    path = vault / "00_人生路" / "01_日记" / f"{today}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntype: journal\ndate: {today}\n---\n\n今天。\n", encoding="utf-8")

    overview = client.get("/api/pillars/life/overview").json()
    week = overview["today"]["week"]
    assert len(week) == 7
    assert week[-1]["date"] == today
    assert week[-1]["exists"] is True
    assert week[-1]["path"] == f"00_人生路/01_日记/{today}.md"
    assert week[0]["date"] < today


def test_note_location_comes_from_path_not_stale_frontmatter(tmp_path):
    client, vault = make_client(tmp_path)
    path = vault / "00_人生路" / "02_反思复盘" / "01_反思" / "错位.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\ntype: reflection\ntitle: 错位\npillar: self\nchapter: idea\n---\n\n正文。\n",
        encoding="utf-8")
    note = client.get(
        "/api/notes", params={"path": "00_人生路/02_反思复盘/01_反思/错位.md"}).json()
    assert note["pillar"] == "life"
    assert note["chapter"] == "reflection"


def test_backlinks_find_wiki_and_markdown_links(tmp_path):
    client, vault = make_client(tmp_path)
    target = pillar_notes.create_note(vault, title="目标概念", body="概念正文")
    source = pillar_notes.create_note(
        vault, title="引用来源", body=f"见 [[{target['path']}|目标概念]]")
    links = client.get("/api/notes/backlinks", params={"path": target["path"]}).json()["items"]
    assert [item["path"] for item in links] == [source["path"]]
