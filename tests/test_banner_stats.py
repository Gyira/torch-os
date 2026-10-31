"""横幅统计：连续日记天数、今日完成度与本地计数。"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.banner_stats import journal_streak
from backend.app.db import Database
from backend.app.habits import HabitCreate, HabitService, HabitToggle

SHANGHAI = ZoneInfo("Asia/Shanghai")
TODAY = datetime.now(SHANGHAI).date().isoformat()


def make_client(tmp_path):
    app = create_app(
        Database(tmp_path / "stats.sqlite3"), tmp_path / "vault",
        security_environ={}, env_file=tmp_path / ".env")
    client = TestClient(app)
    client.get("/")
    return client


def write_note(tmp_path, day):
    path = tmp_path / "vault" / "00_人生路" / "01_日记" / f"{day}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ncreated: {day}T10:00:00+08:00\n---\n\n# {day}\n", encoding="utf-8")


def shift(day: str, offset: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=offset)).isoformat()


def test_streak_counts_consecutive_days_and_stops_at_gaps(tmp_path):
    vault = tmp_path / "vault"
    write_note(tmp_path, TODAY)
    write_note(tmp_path, shift(TODAY, -1))
    write_note(tmp_path, shift(TODAY, -2))
    write_note(tmp_path, shift(TODAY, -4))  # 断了三天
    assert journal_streak(vault, TODAY) == 3

    empty = tmp_path / "empty-vault"
    empty.mkdir()
    assert journal_streak(empty, TODAY) == 0


def test_router_payload_reports_our_own_numbers(tmp_path):
    client = make_client(tmp_path)
    write_note(tmp_path, TODAY)
    write_note(tmp_path, shift(TODAY, -1))
    (tmp_path / "vault" / "00-System").mkdir(parents=True, exist_ok=True)
    (tmp_path / "vault" / "00-System" / "待办（todos）.md").write_text(
        "---\ntype: todo\n---\n\n## 待办\n"
        f"- [ ] 还没做\n- [x] 做完了 ✅ {TODAY}\n\n## 每日必做\n- [ ] 喝水\n",
        encoding="utf-8")

    payload = client.get("/api/banner/stats").json()
    assert payload["day"] == TODAY
    assert payload["journal_streak"] == 2
    assert payload["open_todos"] == 1
    assert payload["daily_total"] == 1 and payload["daily_done"] == 0
    assert payload["habits_total"] == 0 and payload["habits_done"] == 0
    assert isinstance(payload["today_percent"], int)
    # 待办 2（今天完成 1）+ 每日必做 1（未勾）+ 日记文件存在 → 2/4 = 50%
    assert payload["today_percent"] == 50
    assert payload["memories"] == 0 and payload["notes"] == 0
    # 热力图原料：最近 56 天，今天写了两篇日记文件里的这一篇 → activity_today ≥ 1
    assert len(payload["activity"]) == 56
    assert payload["activity"][-1]["day"] == TODAY
    assert payload["activity_today"] >= 1
    assert all(set(item) == {"day", "count"} for item in payload["activity"])
    # 7 行热力图：人生路 / 知识库 / 自我档案 / 任务创建 / 任务完成 / 习惯 / 总热度。
    heatmap = payload["heatmap"]
    assert [row["key"] for row in heatmap["rows"]] == [
        "life", "knowledge", "self", "todo_created", "todo_done", "habits", "total"]
    assert heatmap["rows"][0]["label"] == "人生路"
    assert len(heatmap["rows"][0]["cells"]) == 56
    assert heatmap["rows"][-1]["cells"][-1]["today"] is True
    assert payload["heat_color"] == "#2f9e6a"  # 默认热力绿，不跟随横幅强调色
    assert payload["heatmap_storage"]["status"] in {"written", "unchanged"}
    assert (tmp_path / "vault" / ".obsidian" / "torch-os-heatmap" / "daily"
            / TODAY[:4] / f"{TODAY}.json").is_file()


def test_banner_counts_habit_checkins_and_refreshes_today(tmp_path):
    client = make_client(tmp_path)
    write_note(tmp_path, TODAY)
    database = Database(tmp_path / "stats.sqlite3")
    service = HabitService(database, tmp_path / "vault")
    habit = service.create(HabitCreate(title="阅读", start_date=TODAY))
    service.toggle_entry(habit["id"], HabitToggle(done=True))

    payload = client.get("/api/banner/stats").json()
    assert payload["habits_total"] == 1
    assert payload["habits_done"] == 1
    habits_cells = payload["heatmap"]["rows"][5]["cells"]
    assert habits_cells[-1]["level"] == 4
    assert "习惯 1/1" in habits_cells[-1]["title"]

    # 取消打卡后再取一次，不能还显示旧缓存。
    service.toggle_entry(habit["id"], HabitToggle(done=False, revision=1))
    refreshed = client.get("/api/banner/stats").json()
    assert refreshed["habits_done"] == 0
    assert refreshed["heatmap"]["rows"][5]["cells"][-1]["level"] == 0
