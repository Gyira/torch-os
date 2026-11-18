from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import train_tracker as tracker


def train(code):
    return {"train_code": code, "stops": [
        {"station": "甲站", "depart": "08:00"},
        {"station": "乙站", "arrive": "09:00"},
    ]}


def test_new_duty_train_can_resolve_without_known_stations(monkeypatch):
    requests = []
    def response(request):
        requests.append(request)
        if str(request.url).startswith(tracker.SEARCH_URL):
            return httpx.Response(200, json={"data": [{"station_train_code": "G1234", "train_no": "1234"}]})
        return httpx.Response(200, json={"data": {"data": [
            {"station_name": "甲站", "start_time": "08:00", "arrive_time": "----"},
            {"station_name": "乙站", "start_time": "----", "arrive_time": "09:00"},
        ]}})
    monkeypatch.setattr(tracker, "_client", lambda: httpx.Client(transport=httpx.MockTransport(response)))
    result = tracker.fetch_12306_train("G1234", "", "", "2026-11-18")
    assert result["from_station"] == "甲站" and result["to_station"] == "乙站"
    assert len(requests) == 2


def test_ambiguous_train_does_not_pick_arbitrary_result():
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"data": [
        {"station_train_code": "G1", "train_no": "one"},
        {"station_train_code": "G1", "train_no": "two"},
    ]})))
    with client, pytest.raises(tracker.TrainSyncError, match="多个匹配"):
        tracker._search_train_no(client, "G1", "2026-11-18", "", "")


def test_concurrent_sync_preserves_both_trains_and_service_date(tmp_path):
    barrier = Barrier(2)
    def fetch(code, *_):
        barrier.wait(timeout=10)
        return train(code)
    app = FastAPI()
    app.include_router(tracker.create_train_tracker_router(tmp_path, fetcher=fetch))
    def sync(code):
        with TestClient(app) as client:
            return client.post('/api/train-tracker/sync', json={"code": code, "date": "2026-11-18"})
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(sync, ['G1', 'G2']))
    assert [r.status_code for r in responses] == [200, 200]
    saved = tracker.load_timetable(tmp_path)
    assert set(saved) == {'G1', 'G2'}
    assert all(row['timetable_date'] == '2026-11-18' for row in saved.values())
