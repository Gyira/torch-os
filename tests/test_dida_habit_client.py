"""滴答习惯客户端测试（httpx.MockTransport，不访问真实网络）。"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from backend.app.dida_client import DidaCredentials, DidaError, PrivateDidaProvider


def provider_with(handler, *, csrf: str = ""):
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    credentials = DidaCredentials(
        region="dida365", cookie="abc", csrf_token=csrf,
        device_id="dev-1", device_version=8000, timezone="Asia/Shanghai",
    )
    return PrivateDidaProvider(credentials, client=client)


def test_fetch_habits_parses_list_and_dict_envelopes():
    habit = {
        "id": "h" * 24, "name": "读书", "color": "#97e38b", "iconRes": "x",
        "status": 0, "goal": 1, "step": 0, "unit": "Count", "type": "Boolean",
        "repeatRule": "RRULE:FREQ=DAILY;INTERVAL=1", "reminders": ["20:00"],
        "targetDays": 21, "targetStartDate": 20261101, "etag": "e1",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/v2/habits"):
            return httpx.Response(200, json=[habit])
        return httpx.Response(404)

    provider = provider_with(handler)
    result = asyncio.run(provider.fetch_habits())
    assert len(result) == 1 and result[0].name == "读书"
    asyncio.run(provider.aclose())

    def wrapped(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"habits": [habit]})

    provider = provider_with(wrapped)
    assert len(asyncio.run(provider.fetch_habits())) == 1
    asyncio.run(provider.aclose())


def test_query_habit_checkins_works_without_csrf():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        seen["has_csrf"] = "x-csrftoken" in request.headers
        return httpx.Response(200, json={"checkins": {"h" * 24: [{
            "id": "c" * 24, "habitId": "h" * 24, "checkinStamp": 20261103,
            "value": 1, "goal": 1, "status": 2,
        }]}})

    provider = provider_with(handler)
    result = asyncio.run(provider.query_habit_checkins(["h" * 24]))
    assert seen["path"] == "/api/v2/habitCheckins/query"
    assert seen["body"] == {"habitIds": ["h" * 24], "afterStamp": 19700101}
    assert seen["has_csrf"] is False
    assert result["h" * 24][0].checkin_stamp == 20261103
    asyncio.run(provider.aclose())


def test_query_habit_checkins_403_has_dedicated_code():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"errorCode": "forbidden"})

    provider = provider_with(handler)
    with pytest.raises(DidaError) as captured:
        asyncio.run(provider.query_habit_checkins(["h" * 24]))
    assert captured.value.code == "checkins_read_forbidden"
    asyncio.run(provider.aclose())


def test_batch_habit_and_checkin_envelopes():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"id2etag": {}, "id2error": {}})

    provider = provider_with(handler, csrf="csrf")
    asyncio.run(provider.add_habits([{"id": "h" * 24}]))
    asyncio.run(provider.update_habits([{"id": "h" * 24}]))
    asyncio.run(provider.delete_habits(["h" * 24]))
    asyncio.run(provider.add_habit_checkins([{"id": "c" * 24}]))
    asyncio.run(provider.update_habit_checkins([{"id": "c" * 24}]))
    asyncio.run(provider.delete_habit_checkins(["c" * 24]))
    assert seen[0] == ("/api/v2/habits/batch", {"add": [{"id": "h" * 24}],
                                                 "update": [], "delete": []})
    assert seen[2] == ("/api/v2/habits/batch", {"add": [], "update": [],
                                                 "delete": ["h" * 24]})
    assert seen[5] == ("/api/v2/habitCheckins/batch", {"add": [], "update": [],
                                                        "delete": ["c" * 24]})
    asyncio.run(provider.aclose())


def test_test_connection_reports_habit_error_without_failing():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/v3/batch/check"):
            return httpx.Response(200, json={"checkPoint": 1, "syncTaskBean": {},
                                             "projectProfiles": []})
        if request.url.path.endswith("/api/v2/habits"):
            return httpx.Response(404, json={"errorCode": "not_found"})
        return httpx.Response(200, json=[])

    provider = provider_with(handler)
    result = asyncio.run(provider.test_connection())
    assert result["ok"] is True
    assert result["habit_count"] is None
    assert result["habit_error"] == "http_404"
    asyncio.run(provider.aclose())
