"""经期关怀：纯预测函数、日历/统计/关怀，以及本机 API 的读写边界。"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import love_cycle
from backend.app.love_cycle import (
    DailyLog,
    PeriodRecord,
    Profile,
    analyze,
    build_calendar,
    build_care,
    build_overview,
    build_stats,
    build_upcoming,
    create_cycle_router,
)

TODAY = date(2026, 10, 31)
NOW = "2026-01-01T00:00:00+08:00"


def period(period_id: int, start: str, end: str | None = "2026-09-30",
           *, flow: str = "medium", symptoms: tuple[str, ...] = (),
           moods: tuple[str, ...] = (), notes: str = "") -> PeriodRecord:
    return PeriodRecord(
        id=period_id,
        start=date.fromisoformat(start),
        end=date.fromisoformat(end) if end else None,
        flow=flow,
        symptoms=symptoms,
        moods=moods,
        notes=notes,
        created_at=NOW,
        updated_at=NOW,
    )


def log(day: str, **kwargs) -> DailyLog:
    return DailyLog(
        day=date.fromisoformat(day),
        mood=kwargs.get("mood"),
        pain=kwargs.get("pain"),
        sleep_hours=kwargs.get("sleep_hours"),
        water_ml=kwargs.get("water_ml"),
        energy=kwargs.get("energy"),
        symptoms=tuple(kwargs.get("symptoms", ())),
        notes=kwargs.get("notes", ""),
        updated_at=NOW,
    )


def three_cycles() -> list[PeriodRecord]:
    return [
        period(1, "2026-08-01", "2026-08-05"),
        period(2, "2026-08-29", "2026-09-02"),
        period(3, "2026-09-26", "2026-09-30"),
    ]


def make_client(db_path: Path, *, read_only: bool = False) -> TestClient:
    app = FastAPI()
    app.include_router(create_cycle_router(db_path, read_only=read_only))
    return TestClient(app)


# ── 纯函数 ───────────────────────────────────────────────────────────

def test_no_data_has_no_prediction() -> None:
    analysis = analyze([], Profile(), TODAY)
    assert analysis["has_data"] is False
    assert analysis["phase"] == "unknown"
    assert analysis["next_expected"] is None
    assert analysis["days_until"] is None
    assert analysis["avg_cycle"] == 28
    assert analysis["confidence"] == "none"

    overview = build_overview(Profile(), [], [], TODAY, "2026-10")
    assert overview["calendar"]["month"] == "2026-10"
    assert len(overview["calendar"]["days"]) == 31
    assert any(day["is_today"] for day in overview["calendar"]["days"])
    assert not any(day["is_predicted"] for day in overview["calendar"]["days"])
    assert overview["care"]["alerts"][0]["kind"] == "no_data"
    assert overview["stats"]["total_periods"] == 0
    assert "不是诊断" in overview["care"]["disclaimer"]


def test_cycle_average_late_and_phases() -> None:
    periods = three_cycles()
    analysis = analyze(periods, Profile(), TODAY)
    assert analysis["has_data"] is True
    assert analysis["avg_cycle"] == 28
    assert analysis["avg_period"] == 5
    assert analysis["latest_start"] == "2026-09-26"
    assert analysis["next_expected"] == "2026-10-24"
    assert analysis["days_until"] == -7
    assert analysis["late_days"] == 7
    assert analysis["phase"] == "late"
    assert analysis["ovulation_date"] == "2026-10-10"
    assert analysis["fertile_start"] == "2026-10-05"
    assert analysis["fertile_end"] == "2026-10-11"
    assert analysis["cycle_day"] == 36
    assert analysis["observed_cycles"] == 2
    assert analysis["confidence"] == "medium"

    fertile = analyze(periods, Profile(), date(2026, 10, 8))
    assert fertile["phase"] == "fertile"

    luteal = analyze(periods, Profile(), date(2026, 10, 15))
    assert luteal["phase"] == "luteal"

    follicular = analyze(periods, Profile(), date(2026, 10, 1))
    assert follicular["phase"] == "follicular"

    in_period = analyze(periods, Profile(), date(2026, 9, 28))
    assert in_period["phase"] == "period"
    assert in_period["in_period"] is True
    assert in_period["current_period_id"] == 3


def test_manual_overrides_take_priority() -> None:
    profile = Profile(cycle_length=30, period_length=6, luteal_phase=13, reminder_days=5)
    analysis = analyze([period(1, "2026-09-26", "2026-09-30")], profile, date(2026, 10, 20))
    assert analysis["avg_cycle"] == 30
    assert analysis["avg_period"] == 6
    assert analysis["cycle_source"] == "manual"
    assert analysis["period_source"] == "manual"
    assert analysis["next_expected"] == "2026-10-26"
    assert analysis["ovulation_date"] == "2026-10-13"
    assert analysis["fertile_start"] == "2026-10-08"
    assert analysis["fertile_end"] == "2026-10-14"
    assert analysis["confidence"] == "manual"
    assert analysis["confidence_label"] == "手动周期"


def test_confidence_and_regularity_thresholds() -> None:
    regular = [
        period(index, (date(2026, 4, 1) + date.resolution * 28 * index).isoformat())
        for index in range(7)
    ]
    analysis = analyze(regular, Profile(), date(2026, 10, 31))
    assert analysis["observed_cycles"] == 6
    assert analysis["confidence"] == "high"
    assert analysis["regularity"] == "regular"
    assert analysis["regularity_sd"] == 0.0

    irregular = [
        period(1, "2026-06-01"),
        period(2, "2026-06-23"),
        period(3, "2026-08-02"),
        period(4, "2026-08-27"),
    ]
    analysis = analyze(irregular, Profile(), date(2026, 9, 1))
    assert analysis["regularity"] == "irregular"
    assert analysis["regularity_sd"] is not None and analysis["regularity_sd"] > 4


def test_calendar_marks_actual_predicted_fertile_and_ovulation() -> None:
    periods = three_cycles()
    analysis = analyze(periods, Profile(), TODAY)
    calendar = build_calendar("2026-10", periods, analysis, [], TODAY)
    by_day = {day["date"]: day for day in calendar["days"]}

    assert by_day["2026-10-05"]["is_fertile"] is True
    assert by_day["2026-10-10"]["is_ovulation"] is True
    assert by_day["2026-10-10"]["is_fertile"] is False
    assert by_day["2026-10-24"]["is_predicted"] is True
    assert by_day["2026-10-28"]["is_predicted"] is True
    assert by_day["2026-10-29"]["is_predicted"] is False
    assert by_day["2026-10-31"]["is_today"] is True

    with_actual = periods + [period(4, "2026-10-02", "2026-10-04")]
    actual_analysis = analyze(with_actual, Profile(), TODAY)
    actual_calendar = build_calendar("2026-10", with_actual, actual_analysis, [], TODAY)
    actual_by_day = {day["date"]: day for day in actual_calendar["days"]}
    assert actual_by_day["2026-10-02"]["is_period"] is True
    assert actual_by_day["2026-10-02"]["period_id"] == 4
    assert actual_by_day["2026-10-02"]["is_fertile"] is False


def test_open_period_is_capped_and_alerts() -> None:
    open_period = period(1, "2026-10-01", None)
    analysis = analyze([open_period], Profile(), TODAY)
    assert analysis["in_period"] is False
    assert analysis["avg_period"] == 5

    calendar = build_calendar("2026-10", [open_period], analysis, [], TODAY)
    by_day = {day["date"]: day for day in calendar["days"]}
    assert by_day["2026-10-10"]["is_period"] is True
    assert by_day["2026-10-10"]["is_ongoing"] is True
    assert by_day["2026-10-11"]["is_period"] is False

    care = build_care(analysis, [open_period], TODAY)
    kinds = {alert["kind"] for alert in care["alerts"]}
    assert "open_period" in kinds


def test_reminder_and_upcoming() -> None:
    periods = [period(1, "2026-10-03", "2026-10-07")]
    analysis = analyze(periods, Profile(reminder_days=3), TODAY)
    assert analysis["days_until"] == 0
    care = build_care(analysis, periods, TODAY)
    assert any(alert["kind"] == "reminder" for alert in care["alerts"])

    upcoming = build_upcoming(analysis, TODAY)
    assert len(upcoming) == 3
    assert upcoming[0]["start"] == "2026-10-31"
    assert upcoming[0]["days_until"] == 0
    assert upcoming[1]["start"] == "2026-11-28"


def test_stats_counts_and_averages() -> None:
    periods = [
        period(1, "2026-09-01", "2026-09-05", symptoms=("cramps", "headache"), moods=("tired",)),
        period(2, "2026-09-29", "2026-10-03", symptoms=("cramps",), moods=("calm",)),
    ]
    logs = [
        log("2026-10-30", mood="calm", pain=2, sleep_hours=7.5, water_ml=1200,
            energy=3, symptoms=("headache",)),
        log("2026-10-31", mood="calm", pain=1, sleep_hours=8.0, water_ml=1800,
            energy=4, symptoms=("none",)),
    ]
    analysis = analyze(periods, Profile(), TODAY)
    stats = build_stats(periods, logs, analysis)
    assert stats["total_periods"] == 2
    assert stats["completed_periods"] == 2
    assert stats["logged_days"] == 2
    assert stats["top_symptoms"][0] == {"value": "cramps", "label": "痛经", "count": 2}
    assert stats["top_moods"][0] == {"value": "calm", "label": "平静", "count": 3}
    assert stats["pain_average"] == 1.5
    assert stats["sleep_average"] == 7.8
    assert stats["water_average"] == 1500
    assert stats["energy_average"] == 3.5


def test_month_validation() -> None:
    with pytest.raises(ValueError):
        love_cycle._normalize_month("2026-13")
    with pytest.raises(ValueError):
        love_cycle._normalize_month("2026-1")


# ── API ──────────────────────────────────────────────────────────────

def test_empty_overview_does_not_create_database(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(love_cycle, "_today", lambda: TODAY)
    db_path = tmp_path / "cycle.db"
    client = make_client(db_path)
    response = client.get("/api/love/cycle/overview")
    assert response.status_code == 200
    payload = response.json()
    assert payload["analysis"]["has_data"] is False
    assert payload["read_only"] is False
    assert payload["today"] == TODAY.isoformat()
    assert (db_path.exists()) is False


def test_profile_validation_and_persistence(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(love_cycle, "_today", lambda: TODAY)
    db_path = tmp_path / "cycle.db"
    client = make_client(db_path)

    saved = client.put("/api/love/cycle/profile", json={
        "partner_name": "她", "cycle_length": 30, "period_length": 6,
        "luteal_phase": 13, "reminder_days": 5,
    })
    assert saved.status_code == 200, saved.text
    assert saved.json()["profile"]["partner_name"] == "她"
    assert db_path.is_file()

    overview = client.get("/api/love/cycle/overview").json()
    assert overview["profile"]["cycle_length"] == 30
    assert overview["catalogs"]["flows"][0] == {"value": "spotting", "label": "点滴"}

    for payload in (
        {"cycle_length": 20},
        {"period_length": 1},
        {"luteal_phase": 9},
        {"reminder_days": 8},
        {"partner_name": "x" * 41},
    ):
        assert client.put("/api/love/cycle/profile", json=payload).status_code == 422


def test_period_crud_overlap_and_adjacent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(love_cycle, "_today", lambda: TODAY)
    client = make_client(tmp_path / "cycle.db")

    first = client.post("/api/love/cycle/periods", json={
        "start_date": "2026-10-01", "end_date": "2026-10-03",
        "flow": "light", "symptoms": ["cramps"], "moods": ["tired"], "notes": "第一天",
    })
    assert first.status_code == 200, first.text
    first_id = first.json()["period"]["id"]
    assert first.json()["period"]["duration_days"] == 3
    assert first.json()["period"]["flow_label"] == "少量"

    overlap = client.post("/api/love/cycle/periods", json={
        "start_date": "2026-10-03", "end_date": "2026-10-05",
    })
    assert overlap.status_code == 409

    adjacent = client.post("/api/love/cycle/periods", json={
        "start_date": "2026-10-04", "end_date": "2026-10-06",
    })
    assert adjacent.status_code == 200, adjacent.text
    second_id = adjacent.json()["period"]["id"]

    updated = client.put(f"/api/love/cycle/periods/{first_id}", json={
        "start_date": "2026-10-01", "end_date": "2026-10-02",
    })
    assert updated.status_code == 200, updated.text
    assert updated.json()["period"]["duration_days"] == 2

    conflict = client.put(f"/api/love/cycle/periods/{first_id}", json={
        "start_date": "2026-10-05", "end_date": "2026-10-06",
    })
    assert conflict.status_code == 409

    deleted = client.delete(f"/api/love/cycle/periods/{second_id}")
    assert deleted.status_code == 200
    assert client.delete(f"/api/love/cycle/periods/{second_id}").status_code == 404


def test_period_validation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(love_cycle, "_today", lambda: TODAY)
    client = make_client(tmp_path / "cycle.db")
    cases = [
        {"start_date": "2026-11-01"},
        {"start_date": "2026-10-01", "end_date": "2026-11-01"},
        {"start_date": "2026-10-05", "end_date": "2026-10-01"},
        {"start_date": "2026-10-32"},
        {"start_date": "2026-10-01", "flow": "weird"},
        {"start_date": "2026-10-01", "symptoms": ["unknown"]},
        {"start_date": "2026-10-01", "moods": ["unknown"]},
        {"start_date": "2026-10-01", "notes": "x" * 501},
        {"start_date": "2026-10-01", "extra": True},
    ]
    for payload in cases:
        response = client.post("/api/love/cycle/periods", json=payload)
        assert response.status_code == 422, (payload, response.text)


def test_daily_upsert_clear_and_calendar_logs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(love_cycle, "_today", lambda: TODAY)
    client = make_client(tmp_path / "cycle.db")

    saved = client.put("/api/love/cycle/daily/2026-10-31", json={
        "mood": "calm", "pain": 2, "sleep_hours": 7.5, "water_ml": 1200,
        "energy": 3, "symptoms": ["headache"], "notes": "状态一般",
    })
    assert saved.status_code == 200, saved.text
    assert saved.json()["log"]["pain"] == 2
    assert saved.json()["log"]["pain_label"] == "明显"

    overview = client.get("/api/love/cycle/overview").json()
    assert overview["today_log"]["water_ml"] == 1200
    assert overview["calendar"]["logs"]["2026-10-31"]["mood"] == "calm"
    assert next(
        day for day in overview["calendar"]["days"] if day["date"] == "2026-10-31"
    )["has_log"] is True

    cleared = client.put("/api/love/cycle/daily/2026-10-31", json={})
    assert cleared.status_code == 200
    assert cleared.json()["deleted"] is True
    assert cleared.json()["log"] is None
    assert client.get("/api/love/cycle/overview").json()["today_log"] is None

    assert client.delete("/api/love/cycle/daily/2026-10-31").status_code == 200
    assert client.delete("/api/love/cycle/daily/2026-10-31").status_code == 200
    assert client.put("/api/love/cycle/daily/2026-11-01", json={"pain": 1}).status_code == 422


def test_month_navigation_and_invalid_month(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(love_cycle, "_today", lambda: TODAY)
    client = make_client(tmp_path / "cycle.db")
    for start, end in (
        ("2026-08-01", "2026-08-05"),
        ("2026-08-29", "2026-09-02"),
        ("2026-09-26", "2026-09-30"),
    ):
        assert client.post("/api/love/cycle/periods", json={
            "start_date": start, "end_date": end,
        }).status_code == 200

    november = client.get("/api/love/cycle/overview?month=2026-11")
    assert november.status_code == 200, november.text
    payload = november.json()
    assert payload["month"] == "2026-11"
    assert any(day["is_predicted"] for day in payload["calendar"]["days"])
    assert client.get("/api/love/cycle/overview?month=2026-13").status_code == 422
    assert client.get("/api/love/cycle/overview?month=bad").status_code == 422


def test_read_only_mode_rejects_writes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(love_cycle, "_today", lambda: TODAY)
    db_path = tmp_path / "cycle.db"
    writable = make_client(db_path)
    assert writable.put("/api/love/cycle/profile", json={"partner_name": "她"}).status_code == 200
    assert db_path.is_file()

    readonly = make_client(db_path, read_only=True)
    overview = readonly.get("/api/love/cycle/overview")
    assert overview.status_code == 200
    assert overview.json()["read_only"] is True
    assert overview.json()["profile"]["partner_name"] == "她"

    for response in (
        readonly.put("/api/love/cycle/profile", json={"partner_name": "不能写"}),
        readonly.post("/api/love/cycle/periods", json={"start_date": "2026-10-01"}),
        readonly.put("/api/love/cycle/daily/2026-10-31", json={"pain": 1}),
        readonly.delete("/api/love/cycle/daily/2026-10-31"),
    ):
        assert response.status_code == 409


def test_read_only_missing_database_stays_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(love_cycle, "_today", lambda: TODAY)
    db_path = tmp_path / "nested" / "cycle.db"
    client = make_client(db_path, read_only=True)
    response = client.get("/api/love/cycle/overview")
    assert response.status_code == 200
    assert response.json()["read_only"] is True
    assert db_path.exists() is False
    assert client.put("/api/love/cycle/profile", json={}).status_code == 409
    assert db_path.exists() is False
