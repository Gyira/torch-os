"""Weather caching/concurrency and bounded upstream reads; no real network."""
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event

import httpx
import pytest

from backend.app import weather


def forecast():
    return {"current": {"temperature_2m": 21, "weather_code": 0}}


def test_auto_location_and_forecast_cache_expire_and_force_refresh():
    calls = []
    now = [1000.0]

    def fetch(url, **kwargs):
        calls.append(url)
        return {"city": "北京", "latitude": 39.9, "longitude": 116.41} if url == weather.GEO_URL else forecast()

    service = weather.WeatherService(fetcher=fetch, city_override="", clock=lambda: now[0])
    service.current()
    assert service.current()["cached"]
    assert calls == [weather.GEO_URL, weather.FORECAST_URL]
    assert service.current(city="北京")["source"] == "pick"
    assert service.current()["source"] == "ip"
    service.current(force=True)
    assert len(calls) == 4
    now[0] += weather.CACHE_SECONDS + 1
    service.current()
    assert len(calls) == 6


def test_cache_is_bounded_and_keeps_distinct_locations():
    service = weather.WeatherService(fetcher=lambda *a, **k: forecast(), city_override="")
    for number in range(weather.MAX_CACHE_ENTRIES + 4):
        service.current(city=f"城市{number}", latitude=number / 10, longitude=1)
    assert len(service.cache) == weather.MAX_CACHE_ENTRIES
    assert ("城市0", 0.0, 1.0) not in service.cache
    assert service.current(city="城市4", latitude=0.4, longitude=1)["cached"]


@pytest.mark.parametrize("fail", [False, True])
def test_concurrent_refresh_shares_result_and_can_retry_after_failure(monkeypatch, fail):
    entered, release, joined = Event(), Event(), Event()
    calls = []

    class ObservedFuture(Future):
        def result(self, timeout=None):
            joined.set()
            return super().result(timeout)

    monkeypatch.setattr(weather, "Future", ObservedFuture)

    def fetch(*args, **kwargs):
        calls.append(1)
        entered.set()
        assert release.wait(3)
        if fail and len(calls) == 1:
            raise weather.WeatherError("offline")
        return forecast()

    service = weather.WeatherService(fetcher=fetch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(service.current, True, "北京")
        try:
            assert entered.wait(3)
            second = pool.submit(service.current, True, "北京")
            assert joined.wait(3)
        finally:
            release.set()
        if fail:
            for result in (first, second):
                with pytest.raises(weather.WeatherError, match="offline"):
                    result.result(timeout=3)
        else:
            assert first.result(timeout=3) == second.result(timeout=3)
    assert len(calls) == 1
    assert not service._pending
    assert service.current(force=True, city="北京")["temperature"] == 21
    assert len(calls) == 2


def mock_transport(monkeypatch, response):
    original = httpx.Client
    monkeypatch.setattr(weather.httpx, "Client", lambda **kwargs: original(
        transport=httpx.MockTransport(lambda request: response), **kwargs))


def test_upstream_limit_stops_stream_early_and_closes_it(monkeypatch):
    class Stream(httpx.SyncByteStream):
        count = 0
        closed = False

        def __iter__(self):
            for _ in range(100):
                self.count += 1
                yield b"x" * 16384

        def close(self):
            self.closed = True

    stream = Stream()
    mock_transport(monkeypatch, httpx.Response(200, stream=stream))
    with pytest.raises(weather.WeatherError, match="大小上限"):
        weather.http_json(weather.FORECAST_URL)
    assert stream.count < 100
    assert stream.closed


@pytest.mark.parametrize("body", [b"[]", b"null", b"<html>error</html>", b"\xff"])
def test_bad_upstream_payload_has_controlled_error(monkeypatch, body):
    mock_transport(monkeypatch, httpx.Response(200, content=body))
    with pytest.raises(weather.WeatherError):
        weather.http_json(weather.FORECAST_URL)


def test_http_json_returns_object(monkeypatch):
    mock_transport(monkeypatch, httpx.Response(200, json=forecast()))
    assert weather.http_json(weather.FORECAST_URL) == forecast()
