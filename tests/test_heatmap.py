"""热力图与数据看板：五档、七行、任务/笔记分桶、.obsidian 存档与创建日清单。"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from backend.app import heatmap
from backend.app.application import create_app
from backend.app.db import Database

SHANGHAI = ZoneInfo("Asia/Shanghai")
TODAY = datetime.now(SHANGHAI).date()


def day(offset: int) -> str:
    return (TODAY + timedelta(days=offset)).isoformat()


def write_doc(vault: Path, relative: str, created: str, body: str = "内容") -> Path:
    path = vault / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ncreated: {created}T10:00:00+08:00\n---\n\n{body}\n",
        encoding="utf-8")
    return path


def write_task(vault: Path, relative: str, *, created: str = "",
               done_on: str = "") -> Path:
    path = vault / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", "type: task"]
    if created:
        lines.append(f"创建: {created}")
    if done_on:
        lines += ["完成: true", f"完成于: {done_on}"]
    lines += ["---", "", f"# {path.stem}", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def make_database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "heatmap.sqlite3")
    database.initialize()
    return database


def make_client(tmp_path: Path) -> TestClient:
    app = create_app(
        Database(tmp_path / "heatmap.sqlite3"), tmp_path / "vault",
        security_environ={}, env_file=tmp_path / ".env")
    client = TestClient(app)
    client.get("/")
    return client


def test_levels_use_the_five_shades_0_25_50_75_100():
    assert [heatmap.level_from_score(value) for value in
            (0, 0.01, 0.25, 0.5, 0.75, 0.99, 1)] == [0, 1, 1, 2, 3, 3, 4]
    assert heatmap.level_from_score(None) == 0
    assert heatmap.level_from_score(2) == 4  # 钳位


def test_docs_and_tasks_are_bucketed_by_created_and_completed_day(tmp_path):
    vault = tmp_path / "vault"
    write_doc(vault, "00_人生路/01_日记/a.md", day(-2))
    write_doc(vault, "00_人生路/01_日记/b.md", day(0))
    write_doc(vault, "00_人生路/09_随笔/c.md", day(0))
    write_doc(vault, "03_知识库/00_收件箱/d.md", day(-1))
    write_doc(vault, "01_自我档案/02_记忆与自传/e.md", day(-2))
    write_task(vault, "02_生活运营/00_待办事项/收集箱/t1.md", created=day(0))
    write_task(vault, "02_生活运营/00_待办事项/已完成/t2.md",
               created=day(-5), done_on=day(-1))
    # 回收站与 _系统 不计入。
    write_task(vault, "02_生活运营/00_待办事项/回收站/t3.md", created=day(0))
    write_task(vault, "02_生活运营/00_待办事项/_系统/任务（tasks）.md", created=day(0))

    ledger = heatmap.load_ledger(vault)
    raw = heatmap.build_heatmap(
        make_database(tmp_path), vault,
        TODAY - timedelta(days=2), TODAY, include_documents=True, ledger=ledger)
    by_day = {item["date"]: item["rows"] for item in raw["days"]}
    assert by_day[day(-2)]["life"]["count"] == 1
    assert by_day[day(-2)]["self"]["count"] == 1
    assert by_day[day(-1)]["knowledge"]["count"] == 1
    assert by_day[day(-1)]["todo_done"]["count"] == 1
    assert by_day[day(0)]["life"]["count"] == 2
    assert by_day[day(0)]["life"]["level"] == 2
    assert by_day[day(0)]["todo_created"]["count"] == 1
    assert by_day[day(0)]["todo_created"]["level"] == 1
    assert by_day[day(0)]["life"]["documents"] == [
        "00_人生路/01_日记/b.md", "00_人生路/09_随笔/c.md"]
    assert raw["task_present"] is True
    assert ledger["00_人生路/01_日记/b.md"] == day(0)


def test_journal_filename_is_the_creation_day_without_frontmatter(tmp_path):
    vault = tmp_path / "vault"
    path = vault / "00_人生路" / "01_日记" / f"{day(-1)}.md"
    path.parent.mkdir(parents=True)
    path.write_text("# 昨天\n\n正文\n", encoding="utf-8")  # 没有 created frontmatter
    raw = heatmap.build_heatmap(
        make_database(tmp_path), vault, TODAY - timedelta(days=1), TODAY)
    counts = {item["date"]: item["rows"]["life"]["count"] for item in raw["days"]}
    assert counts[day(-1)] == 1
    assert counts[day(0)] == 0


def test_task_created_and_completed_same_day_counts_in_both_rows(tmp_path):
    vault = tmp_path / "vault"
    write_task(vault, "02_生活运营/00_待办事项/已完成/立刻做完.md",
               created=day(0), done_on=day(0))
    raw = heatmap.build_heatmap(make_database(tmp_path), vault, TODAY, TODAY)
    rows = raw["days"][0]["rows"]
    assert rows["todo_created"]["count"] == 1
    assert rows["todo_done"]["count"] == 1


def test_total_is_an_equal_weight_average_not_a_single_row_sum(tmp_path):
    vault = tmp_path / "vault"
    # 没有任务：总热度只看三个库，三库全满 = 100%。
    for relative in (
        "00_人生路/a.md", "00_人生路/b.md", "00_人生路/c.md", "00_人生路/d.md",
        "03_知识库/a.md", "03_知识库/b.md", "03_知识库/c.md", "03_知识库/d.md",
        "01_自我档案/a.md", "01_自我档案/b.md", "01_自我档案/c.md", "01_自我档案/d.md",
    ):
        write_doc(vault, relative, day(0))
    raw = heatmap.build_heatmap(make_database(tmp_path), vault, TODAY, TODAY)
    today = raw["days"][0]["rows"]
    assert raw["task_present"] is False
    assert today["total"]["score"] == 1
    assert today["total"]["level"] == 4

    # 有任务时，任务两行全满、三个文档库为空：总热度 2/5 = 40%，不会被任务单独顶满。
    task_vault = tmp_path / "task-vault"
    for index in range(4):
        write_task(task_vault, f"02_生活运营/00_待办事项/清单/t{index}.md",
                   created=day(0))
        write_task(task_vault, f"02_生活运营/00_待办事项/已完成/d{index}.md",
                   created=day(-1), done_on=day(0))
    task_raw = heatmap.build_heatmap(
        make_database(tmp_path / "tasks"), task_vault, TODAY, TODAY)
    rows = task_raw["days"][0]["rows"]
    assert task_raw["task_present"] is True
    assert rows["todo_created"]["score"] == 1
    assert rows["todo_done"]["score"] == 1
    assert rows["total"]["score"] == 0.4
    assert rows["total"]["level"] == 1


def test_ledger_keeps_creation_day_after_an_atomic_rewrite(tmp_path):
    vault = tmp_path / "vault"
    path = write_doc(vault, "00_人生路/01_日记/a.md", day(-1))
    database = make_database(tmp_path)
    ledger = heatmap.load_ledger(vault)
    first = heatmap.build_heatmap(database, vault, TODAY - timedelta(days=1), TODAY,
                                  ledger=ledger)
    assert first["ledger_changed"] is True
    heatmap.save_ledger(vault, ledger)
    assert ledger["00_人生路/01_日记/a.md"] == day(-1)

    # 模拟 markdown_store 的临时文件 + os.replace：系统创建时间会变成今天。
    temporary = path.with_suffix(".md.tmp")
    temporary.write_text(
        f"---\ncreated: {day(-1)}T10:00:00+08:00\n---\n\n改过\n", encoding="utf-8")
    os.replace(temporary, path)
    again = heatmap.build_heatmap(database, vault, TODAY - timedelta(days=1), TODAY,
                                  ledger=heatmap.load_ledger(vault))
    counts = {item["date"]: item["rows"]["life"]["count"] for item in again["days"]}
    assert counts[day(-1)] == 1
    assert counts[day(0)] == 0
    assert again["ledger_changed"] is False


def test_board_endpoint_returns_four_views_and_writes_the_obsidian_archive(tmp_path):
    vault = tmp_path / "vault"
    write_doc(vault, "00_人生路/01_日记/a.md", day(0), "今天的日记")
    write_doc(vault, "03_知识库/00_收件箱/b.md", day(0), "今天的资料")
    client = make_client(tmp_path)

    payload = client.get("/api/heatmap/board", params={"view": "day", "day": day(0)}).json()
    assert payload["view"] == "day"
    assert payload["anchor"] == day(0)
    assert payload["detail"]["date"] == day(0)
    assert [row["key"] for row in payload["rows"]] == [
        "life", "knowledge", "self", "todo_created", "todo_done", "habits", "total"]
    life = payload["detail"]["rows"]["life"]
    assert life["count"] == 1
    assert life["documents"] == ["00_人生路/01_日记/a.md"]
    assert payload["summary"]["rows"]["total"]["level"] >= 0

    root = vault / ".obsidian" / "torch-os-heatmap"
    daily = root / "daily" / day(0)[:4] / f"{day(0)}.json"
    assert daily.is_file()
    archived = json.loads(daily.read_text(encoding="utf-8"))
    assert archived["kind"] == "daily" and archived["date"] == day(0)
    assert archived["rows"]["life"]["documents"] == ["00_人生路/01_日记/a.md"]
    ledger = json.loads((root / "file-created.json").read_text(encoding="utf-8"))
    assert ledger["files"]["00_人生路/01_日记/a.md"] == day(0)

    week = client.get("/api/heatmap/board", params={"view": "week", "day": day(0)}).json()
    month = client.get("/api/heatmap/board", params={"view": "month", "day": day(0)}).json()
    year = client.get("/api/heatmap/board", params={"view": "year", "day": day(0)}).json()
    assert week["period"]["kind"] == "week" and len(week["days"]) == 7
    assert month["period"]["kind"] == "month"
    year_days = (date(TODAY.year, 12, 31) - date(TODAY.year, 1, 1)).days + 1
    assert year["period"]["kind"] == "year" and len(year["days"]) == year_days
    for kind, folder in (("weekly", "weekly"), ("monthly", "monthly"), ("yearly", "yearly")):
        assert any((root / folder).rglob("*.json")), kind
    assert (root / "yearly" / f"{TODAY.year}.json").is_file()
    assert week["storage"]["root"] == ".obsidian/torch-os-heatmap"

    future = client.get("/api/heatmap/board", params={
        "view": "day", "day": (TODAY + timedelta(days=30)).isoformat()}).json()
    assert future["anchor"] == day(0)  # 未来日期钳回今天
    assert future["detail"]["date"] == day(0)


def test_read_only_still_returns_data_but_skips_the_archive(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    write_doc(vault, "00_人生路/a.md", day(0))
    database = Database(tmp_path / "readonly.sqlite3")
    app = create_app(database, vault, security_environ={"VAULT_READ_ONLY": "1"},
                     env_file=tmp_path / ".env")
    client = TestClient(app)
    client.get("/")
    payload = client.get("/api/heatmap/board", params={"view": "day"}).json()
    assert payload["detail"]["rows"]["life"]["count"] == 1
    assert payload["storage"]["status"] == "skipped"
    assert not (vault / ".obsidian" / "torch-os-heatmap").exists()
