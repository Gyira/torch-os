"""待确认中枢：三处积压（草稿 / 事实 / 分流）汇总在一处，动作只做转发。

诉求（2026-10-27）：`inbox_items` 里的 13 条定时草稿没有任何界面能打开，
记忆候选从来没有审核入口，身份候选又单独住在自我档案——三个地方各自为政。
这里钉住：计数对、结构统一、三个动作各自生效且互不串味。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import identity_inbox, pending
from backend.app.application import create_app
from backend.app.db import Database, utc_now
from backend.app.note_pipeline import write_candidate_note


def make_client(tmp_path: Path) -> tuple[TestClient, Database, Path]:
    database = Database(tmp_path / "pending.sqlite3")
    vault = tmp_path / "vault"
    app = create_app(database, vault, security_environ={}, env_file=tmp_path / ".env")
    client = TestClient(app)
    client.get("/")
    return client, database, vault


def add_draft(database: Database, *, kind: str = "evening", day: str = "2026-10-27",
              created_at: str | None = None) -> int:
    body = {"date": day, "completed_tasks": 2, "open_overdue_tasks": 1,
            "habits": {"done": 3, "skipped": 0, "unrecorded": 2},
            "journal": {"recorded": False, "next_action": ""}}
    with database.connect() as connection:
        cursor = connection.execute(
            "INSERT INTO inbox_items(kind,source_date,title,body_json,manual,catch_up,created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (kind, day, "今日复盘草稿", json.dumps(body, ensure_ascii=False), 0, 0,
             created_at or utc_now()),
        )
        return int(cursor.lastrowid)


def add_identity_draft(vault: Path, name: str = "20261027-220000-field-ab12cd34.md") -> Path:
    directory = vault / identity_inbox.INBOX_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("---\nkind: field\nfield: name\ntext: 称呼我小林\n"
                    "evidence: 我说：以后叫我小林\ncreated: 2026-10-27T22:00:00+08:00\n---\n\n",
                    encoding="utf-8", newline="\n")
    return path


def add_love_draft(vault: Path, *, day: str = "2026-10-20", draft_id: str = "d1",
                   status: str = "draft") -> Path:
    """恋爱系统自己那套库：中枢只读草稿，写回仍走恋爱系统的接口。"""
    directory = vault / ".torch-os" / "love"
    directory.mkdir(parents=True, exist_ok=True)
    db_path = directory / "love_analysis.db"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS memory_drafts (id TEXT PRIMARY KEY, project_id TEXT,"
            " day TEXT, status TEXT, content_json TEXT, markdown TEXT, usage_json TEXT,"
            " note_path TEXT, created_at TEXT, updated_at TEXT)")
        connection.execute(
            "INSERT OR REPLACE INTO memory_drafts VALUES (?,?,?,?,?,?,?,?,?,?)",
            (draft_id, "p1", day, status, "{}",
             f"# {day} 的记忆\n\n今天一起去看了海。\n", "{}", None, "", ""))
        connection.commit()
    finally:
        connection.close()
    return db_path


def test_the_orphaned_drafts_are_visible_again(tmp_path: Path) -> None:
    client, database, _vault = make_client(tmp_path)
    add_draft(database)

    summary = client.get("/api/pending/summary").json()

    assert summary["draft"] == 1
    assert summary["total"] >= 1
    assert summary["titles"]["draft"] == "草稿"


def test_draft_item_is_readable_without_opening_the_database(tmp_path: Path) -> None:
    client, database, _vault = make_client(tmp_path)
    add_draft(database)

    items = client.get("/api/pending/items?kind=draft").json()["items"]

    assert len(items) == 1
    item = items[0]
    assert item["kind"] == "draft"
    assert item["actions"] == ["read", "dismiss"]
    assert "完成 2 件" in item["preview"], item["preview"]
    assert "习惯 完成 3" in " ".join(item["lines"])


def test_mark_read_and_dismiss_both_clear_the_queue(tmp_path: Path) -> None:
    client, database, _vault = make_client(tmp_path)
    first = add_draft(database)
    second = add_draft(database, kind="morning")

    assert client.post("/api/pending/drafts", json={"id": first, "action": "read"}).status_code == 200
    assert client.get("/api/pending/summary").json()["draft"] == 1
    assert client.post("/api/pending/drafts", json={"id": second, "action": "dismiss"}).status_code == 200
    assert client.get("/api/pending/summary").json()["draft"] == 0


def test_identity_fact_goes_through_the_same_queue(tmp_path: Path) -> None:
    client, _database, vault = make_client(tmp_path)
    add_identity_draft(vault)

    summary = client.get("/api/pending/summary").json()
    assert summary["fact"] == 1

    item = client.get("/api/pending/items?kind=fact").json()["items"][0]
    assert item["id"].startswith("identity:")
    assert item["actions"] == ["accept", "reject"]

    result = client.post("/api/pending/facts", json={"id": item["id"], "action": "accept"})
    assert result.status_code == 200, result.text
    identity = (vault / "01_自我档案/01_内核与三观/身份（identity）.md").read_text(encoding="utf-8")
    assert "称呼我小林" in identity
    assert client.get("/api/pending/summary").json()["fact"] == 0


def test_rejecting_a_fact_leaves_the_vault_alone(tmp_path: Path) -> None:
    client, _database, vault = make_client(tmp_path)
    add_identity_draft(vault)

    result = client.post("/api/pending/facts", json={"id": "identity:20261027-220000-field-ab12cd34.md",
                                                    "action": "reject"})
    assert result.status_code == 200
    assert not (vault / "01_自我档案/01_内核与三观/身份（identity）.md").exists()
    assert client.get("/api/pending/summary").json()["fact"] == 0


def test_memory_candidates_share_the_fact_room(tmp_path: Path) -> None:
    client, database, vault = make_client(tmp_path)
    write_candidate_note(
        database, vault, source_key="test-1",
        relative_path="01_自我档案/02_记忆与自传/候选样例.md",
        title="候选样例", source_kind="test", created_at=utc_now(),
        sentences=[{"text": "我每天早上六点起床跑步"}],
    )

    items = client.get("/api/pending/items?kind=fact").json()["items"]
    memory_items = [item for item in items if item["id"].startswith("memory:")]
    assert memory_items, items

    result = client.post("/api/pending/facts", json={"id": memory_items[0]["id"], "action": "accept"})
    assert result.status_code == 200, result.text
    assert result.json()["result"]["status"] == "active"


def test_triage_lists_the_inbox_but_only_offers_a_jump(tmp_path: Path) -> None:
    client, _database, vault = make_client(tmp_path)
    inbox = vault / "03_知识库/00_收件箱"
    inbox.mkdir(parents=True)
    (inbox / "待分类.md").write_text("---\ntype: inbox\ntitle: 待分类的一条\n---\n\n随手记下的东西\n",
                                     encoding="utf-8")

    items = client.get("/api/pending/items?kind=triage").json()["items"]

    assert [item["title"] for item in items] == ["待分类的一条"]
    assert items[0]["actions"] == ["move"]
    assert items[0]["suggest"]["chapter"], "分流要带本地规则的建议章节"


def test_triage_offers_only_real_chapters(tmp_path: Path) -> None:
    client, _database, _vault = make_client(tmp_path)
    payload = client.get("/api/pending/items?kind=triage").json()
    keys = {chapter["key"] for chapter in payload["chapters"]}

    assert "inbox" in keys and "reflection" in keys
    assert not keys & pending.TRIAGE_BLOCKED, "任务/日记/习惯不该出现在搬家目标里"


def test_triage_move_really_moves_the_note(tmp_path: Path) -> None:
    client, _database, vault = make_client(tmp_path)
    inbox = vault / "03_知识库/00_收件箱"
    inbox.mkdir(parents=True)
    (inbox / "待分类.md").write_text("---\ntype: inbox\ntitle: 待分类的一条\n---\n\n随手记下的东西\n",
                                     encoding="utf-8")

    result = client.post("/api/pending/triage",
                         json={"path": "03_知识库/00_收件箱/待分类.md", "chapter": "insight_life"})

    assert result.status_code == 200, result.text
    moved = vault / "00_人生路/03_心得体会/01_生活心得/待分类.md"
    assert moved.is_file(), result.text
    assert not (inbox / "待分类.md").exists()
    assert "随手记下的东西" in moved.read_text(encoding="utf-8")
    assert client.get("/api/pending/summary").json()["triage"] == 0


def test_triage_cannot_move_into_managed_chapters(tmp_path: Path) -> None:
    client, _database, vault = make_client(tmp_path)
    inbox = vault / "03_知识库/00_收件箱"
    inbox.mkdir(parents=True)
    (inbox / "待分类.md").write_text("---\ntype: inbox\ntitle: x\n---\n\n正文\n", encoding="utf-8")

    blocked = client.post("/api/pending/triage",
                          json={"path": "03_知识库/00_收件箱/待分类.md", "chapter": "journal"})
    assert blocked.status_code == 409
    unknown = client.post("/api/pending/triage",
                          json={"path": "03_知识库/00_收件箱/待分类.md", "chapter": "nowhere"})
    assert unknown.status_code == 409


def test_love_memory_drafts_join_the_same_hub(tmp_path: Path) -> None:
    client, _database, vault = make_client(tmp_path)
    add_love_draft(vault)
    add_love_draft(vault, day="2026-10-19", draft_id="d2", status="written")

    payload = client.get("/api/pending/items?kind=love").json()
    items = payload["items"]

    assert [item["id"] for item in items] == ["d1"], "只列还没写入的草稿"
    assert items[0]["actions"] == ["write", "discard"]
    assert items[0]["title"] == "2026-10-20 · 每日记忆"
    assert "今天一起去看了海" in items[0]["preview"]
    assert client.get("/api/pending/summary").json()["love"] == 1


def test_month_without_a_love_database_is_empty_not_broken(tmp_path: Path) -> None:
    client, _database, _vault = make_client(tmp_path)
    assert client.get("/api/pending/items?kind=love").json()["items"] == []
    assert client.get("/api/pending/summary").json()["love"] == 0

def test_unknown_kind_and_bad_fact_id_are_rejected(tmp_path: Path) -> None:
    client, _database, _vault = make_client(tmp_path)
    assert client.get("/api/pending/items?kind=whatever").status_code == 422
    bad = client.post("/api/pending/facts", json={"id": "bogus:1", "action": "accept"})
    assert bad.status_code == 409


def test_summary_is_cheap_and_matches_items(tmp_path: Path) -> None:
    client, database, vault = make_client(tmp_path)
    add_draft(database)
    add_identity_draft(vault)

    summary = client.get("/api/pending/summary").json()
    merged = client.get("/api/pending/items").json()["items"]

    assert summary["draft"] + summary["fact"] + summary["triage"] + summary["love"] == summary["total"]
    assert len(merged) == summary["total"]
    assert {item["kind"] for item in merged} == {"draft", "fact"}


def test_module_level_helpers_are_pure_reads(tmp_path: Path) -> None:
    _client, database, vault = make_client(tmp_path)
    add_draft(database)
    service = pending.SchedulerService(database, vault)

    assert pending.summary(database, vault, service)["draft"] == 1
    assert pending.items(database, vault, service, "draft")[0]["kind"] == "draft"
    assert pending.triage(vault) == []


def test_mixed_timezones_still_sort_newest_first(tmp_path: Path) -> None:
    """草稿是 UTC，身份候选是 +08:00；按字符串排会把今天的事实排到昨天的草稿后面。"""
    client, database, vault = make_client(tmp_path)
    # 草稿记 UTC、身份候选记 +08:00：21:00+08 的草稿必须排在 22:00+08 的候选后面。
    add_draft(database, created_at="2026-10-27T13:00:00+00:00")
    add_identity_draft(vault)  # created: 2026-10-27T22:00:00+08:00

    merged = client.get("/api/pending/items").json()["items"]

    assert merged[0]["kind"] == "fact", merged
    # 同一个瞬间的两种写法必须算出同一个时间戳，否则跨来源排序就是碰运气。
    assert (pending._timestamp("2026-10-27T22:00:00+08:00")
            == pending._timestamp("2026-10-27T14:00:00+00:00"))


def test_empty_morning_draft_explains_itself(tmp_path: Path) -> None:
    client, database, _vault = make_client(tmp_path)
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO inbox_items(kind,source_date,title,body_json,manual,catch_up,created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            ("morning", "2026-10-27", "今日规划草稿",
             json.dumps({"date": "2026-10-27", "goals": [], "open_tasks": [],
                         "journal_yesterday_recorded": True}, ensure_ascii=False), 0, 0, utc_now()),
        )

    item = client.get("/api/pending/items?kind=draft").json()["items"][0]

    assert "没有内容" not in item["preview"]
    assert "没有到期任务" in item["preview"]
