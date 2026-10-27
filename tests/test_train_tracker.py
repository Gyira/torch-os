"""列车追踪：本地时刻表、运行状态、导入、旧数据迁移与 12306 手动同步。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import train_tracker
from backend.app.train_tracker import create_train_tracker_router

TZ = ZoneInfo("Asia/Shanghai")
TRAIN = {
    "train_code": "G1",
    "from_station": "北京南",
    "to_station": "上海虹桥",
    "depart_time": "10:00",
    "arrive_time": "11:00",
    "duration_minutes": 60,
    "stops": [
        {"station": "北京南", "arrive": "", "depart": "10:00"},
        {"station": "济南西", "arrive": "10:30", "depart": "10:35"},
        {"station": "上海虹桥", "arrive": "11:00", "depart": ""},
    ],
}


def _client(tmp_path: Path, *, read_only: bool = False, legacy=(), fetcher=None,
            sync_enabled: bool = True) -> TestClient:
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    app = FastAPI()
    app.include_router(create_train_tracker_router(
        vault, read_only=read_only, legacy_paths=legacy,
        sync_enabled=sync_enabled, fetcher=fetcher))
    return TestClient(app)


def _timetable(tmp_path: Path) -> Path:
    return tmp_path / "vault" / ".torch-os" / "train-tracker" / "timetable.json"


def _write_timetable(tmp_path: Path, data=None) -> Path:
    path = _timetable(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data or {"G1": TRAIN}, ensure_ascii=False), encoding="utf-8")
    return path


def test_status_and_train_round_trip(tmp_path: Path) -> None:
    _write_timetable(tmp_path)
    client = _client(tmp_path)
    status = client.get("/api/train-tracker/status").json()
    assert status["count"] == 1 and status["trains"] == ["G1"]
    assert status["timetable_exists"] is True
    assert status["data_dir"] == ".torch-os/train-tracker"

    payload = client.get("/api/train-tracker/train?code=g1").json()
    assert payload["found"] is True
    assert payload["code"] == "G1"
    assert payload["train"]["from_station"] == "北京南"
    assert payload["realtime"]["status"] in {
        "before_depart", "at_station", "running", "arrived",
    }

    missing = client.get("/api/train-tracker/train?code=G999").json()
    assert missing["found"] is False
    assert "时刻表" in missing["error"]


def test_import_accepts_old_shape_and_rejects_broken_json(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.post(
        "/api/train-tracker/timetable",
        files={"file": ("timetable.json", json.dumps({"G1": TRAIN}, ensure_ascii=False).encode(), "application/json")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 1
    assert _timetable(tmp_path).is_file()

    bad = client.post(
        "/api/train-tracker/timetable",
        files={"file": ("bad.json", b"{not json", "application/json")},
    )
    assert bad.status_code == 422
    no_stops = client.post(
        "/api/train-tracker/timetable",
        files={"file": ("empty.json", json.dumps({"G1": {"stops": []}}).encode(), "application/json")},
    )
    assert no_stops.status_code == 422


def test_sync_merges_with_the_injected_12306_fetcher(tmp_path: Path) -> None:
    _write_timetable(tmp_path)
    seen = {}

    def fake_fetch(code, from_station, to_station, date_text):
        seen.update(code=code, from_station=from_station, to_station=to_station, date=date_text)
        return {**TRAIN, "source": "12306", "synced_at": "2026-10-27T12:00:00+08:00"}

    client = _client(tmp_path, fetcher=fake_fetch)
    response = client.post("/api/train-tracker/sync", json={
        "code": "g1", "date": "2026-10-28",
    })
    assert response.status_code == 200, response.text
    assert seen == {
        "code": "G1", "from_station": "北京南",
        "to_station": "上海虹桥", "date": "2026-10-28",
    }
    saved = json.loads(_timetable(tmp_path).read_text(encoding="utf-8"))
    assert saved["G1"]["source"] == "12306"
    assert saved["G1"]["synced_at"] == "2026-10-27T12:00:00+08:00"


def test_sync_can_be_disabled_and_respects_read_only(tmp_path: Path) -> None:
    _write_timetable(tmp_path)
    disabled = _client(tmp_path, sync_enabled=False, fetcher=lambda *args: TRAIN)
    assert disabled.post("/api/train-tracker/sync", json={"code": "G1"}).status_code == 409

    read_only = _client(tmp_path, read_only=True, fetcher=lambda *args: TRAIN)
    assert read_only.post("/api/train-tracker/sync", json={"code": "G1"}).status_code == 409
    blocked_import = read_only.post(
        "/api/train-tracker/timetable",
        files={"file": ("t.json", json.dumps({"G1": TRAIN}).encode(), "application/json")},
    )
    assert blocked_import.status_code == 409


def test_legacy_timetable_is_copied_and_kept(tmp_path: Path) -> None:
    legacy = tmp_path / "vault" / ".torch-os" / "love" / "train_timetable.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(json.dumps({"G1": TRAIN}, ensure_ascii=False), encoding="utf-8")
    client = _client(tmp_path, legacy=[legacy])
    status = client.get("/api/train-tracker/status").json()
    assert status["legacy_migrated"] is True
    assert _timetable(tmp_path).is_file()
    assert legacy.is_file(), "迁移只复制，不删除旧文件"


def test_train_status_before_running_at_station_and_arrived() -> None:
    def at(hour: int, minute: int) -> datetime:
        return datetime(2026, 5, 20, hour, minute, tzinfo=TZ)

    before = train_tracker.train_status(TRAIN, at(9, 0))
    assert before["status"] == "before_depart"
    assert before["current_station"] == "北京南"
    assert before["next_station"] == "济南西"
    early = train_tracker.train_status(TRAIN, at(2, 0))
    assert early["status"] == "before_depart"  # 白班车凌晨不会误判为昨天的“已到达”

    at_station = train_tracker.train_status(TRAIN, at(10, 32))
    assert at_station["status"] == "at_station"
    assert at_station["current_station"] == "济南西"
    assert at_station["countdown_minutes"] == 3

    running = train_tracker.train_status(TRAIN, at(10, 45))
    assert running["status"] == "running"
    assert running["current_station"] == "济南西"
    assert running["next_station"] == "上海虹桥"
    assert 0 <= running["segment_progress_pct"] <= 100

    arrived = train_tracker.train_status(TRAIN, at(11, 30))
    assert arrived["status"] == "arrived"
    assert arrived["progress_pct"] == 100

    missing = train_tracker.train_status({**TRAIN, "stops": []})
    assert missing["status"] == "unknown"
    assert "时刻表" in missing["message"]


def test_12306_left_ticket_rotation_and_stop_parsing(monkeypatch) -> None:
    row = ("secret|预订|24000000G10L|G1|VNP|AOH|VNP|AOH|06:30|11:24|04:54|"
           "Y|abc|20261028|3|P3|01|13|1|0|有")
    responses = [
        {"c_url": "leftTicket/queryG"},
        {"data": {"result": [row]}},
    ]

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    calls = []

    def fake_get(client, url, *, params=None, max_bytes=0):
        calls.append((url, params))
        return FakeResponse(responses[len(calls) - 1])

    monkeypatch.setattr(train_tracker, "_get", fake_get)
    train_no = train_tracker._query_train_no(
        object(), "G1", "2026-10-28", "北京南", "上海虹桥",
        {"北京南": "VNP", "上海虹桥": "AOH"},
    )
    assert train_no == "24000000G10L"
    assert calls[0][0].endswith("/leftTicket/queryZ")
    assert calls[1][0].endswith("/leftTicket/queryG")

    stops = train_tracker.normalize_train("G1", {
        "train_code": "G1",
        "from_station": "北京南",
        "to_station": "上海虹桥",
        "stops": [
            {"station": "北京南", "arrive": "----", "depart": "06:30"},
            {"station": "沧州西", "arrive": "07:07", "depart": "07:09"},
            {"station": "上海虹桥", "arrive": "11:24", "depart": "----"},
        ],
    })
    assert stops["depart_time"] == "06:30"
    assert stops["arrive_time"] == "11:24"
    assert stops["duration_minutes"] == 294
    assert stops["stops"][0]["arrive"] == ""
    assert stops["stops"][1]["station"] == "沧州西"


def test_overnight_timetable_wraps_past_midnight() -> None:
    train = {
        "train_code": "D9",
        "from_station": "北京南",
        "to_station": "上海虹桥",
        "depart_time": "23:00",
        "arrive_time": "00:40",
        "stops": [
            {"station": "北京南", "arrive": "", "depart": "23:00"},
            {"station": "济南西", "arrive": "00:10", "depart": "00:15"},
            {"station": "上海虹桥", "arrive": "00:40", "depart": ""},
        ],
    }
    running = train_tracker.train_status(train, datetime(2026, 5, 20, 23, 30, tzinfo=TZ))
    assert running["status"] == "running"
    assert running["next_station"] == "济南西"
    at_station = train_tracker.train_status(train, datetime(2026, 5, 21, 0, 12, tzinfo=TZ))
    assert at_station["status"] == "at_station"
    assert at_station["current_station"] == "济南西"
    later = train_tracker.train_status(train, datetime(2026, 5, 21, 0, 20, tzinfo=TZ))
    assert later["status"] == "running"
    assert later["next_station"] == "上海虹桥"
    morning = train_tracker.train_status(train, datetime(2026, 5, 21, 2, 0, tzinfo=TZ))
    assert morning["status"] == "before_depart"  # 凌晨到达窗口结束后，等今天 23:00 的车
