"""横幅统计：连续日记天数、今日完成度与本地计数。"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.banner_stats import journal_streak
from backend.app.db import Database

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
    path = tmp_path / "vault" / "02-Life" / "journal" / f"{day}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {day}\n", encoding="utf-8")


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
    # 热力图原料：最近 14 天，今天写了两篇日记文件里的这一篇 → activity_today ≥ 1
    assert len(payload["activity"]) == 14
    assert payload["activity"][-1]["day"] == TODAY
    assert payload["activity_today"] >= 1
    assert all(set(item) == {"day", "count"} for item in payload["activity"])
