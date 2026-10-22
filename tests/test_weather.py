"""天气：白名单出站、城市定位优先级、WMO 映射与 30 分钟缓存。"""

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.weather import (
    WeatherError, WeatherService, create_weather_router, daily_forecast, describe_weather,
    http_json, wind_label,
)

FORECAST = {
    "current": {
        "time": "2026-10-05T21:30",
        "temperature_2m": 24.3,
        "apparent_temperature": 26.1,
        "relative_humidity_2m": 62,
        "weather_code": 1,
        "wind_speed_10m": 8.4,
    }
}
# 5 天日级：体感 / 湿度 / 降水可能性 / 风向 / 风速 / 气压 / 日出 / 日落（Open-Meteo 并行数组）。
DAILY = {
    "daily": {
        "time": ["2026-10-06", "2026-10-07", "2026-10-08"],
        "weather_code": [1, 61, 0],
        "temperature_2m_max": [26.4, 21.2, 25.0],
        "temperature_2m_min": [17.1, 16.5, 18.0],
        "apparent_temperature_max": [28.0, 22.1, 26.2],
        "apparent_temperature_min": [16.2, 15.0, 17.4],
        "precipitation_probability_max": [10, 80, 0],
        "relative_humidity_2m_mean": [60.4, 85.2, 55.0],
        "wind_speed_10m_max": [12.4, 20.1, 8.0],
        "wind_direction_10m_dominant": [45, 200, 350],
        "surface_pressure_mean": [1020.6, 1015.2, 1018.0],
        "sunrise": ["2026-10-06T06:12", "2026-10-07T06:13", "2026-10-08T06:13"],
        "sunset": ["2026-10-06T17:52", "2026-10-07T17:51", "2026-10-08T17:50"],
    }
}
GEO = {"success": True, "city": "赣州", "latitude": 25.83, "longitude": 114.93}


class FakeFetcher:
    def __init__(self, geo=GEO, forecast=FORECAST, geo_error=False):
        self.calls = []
        self.geo, self.forecast, self.geo_error = geo, forecast, geo_error

    def __call__(self, url, params=None):
        self.calls.append((url, params))
        if "ipwho.is" in url:
            if self.geo_error:
                raise WeatherError("IP 定位不可用")
            return self.geo
        return self.forecast


def test_maps_open_meteo_payload_and_ip_location():
    fetcher = FakeFetcher()
    service = WeatherService(fetcher=fetcher, city_override="", clock=lambda: 1000.0)
    payload = service.current()
    assert payload["city"] == "赣州" and payload["source"] == "ip"
    assert payload["temperature"] == 24.3 and payload["apparent_temperature"] == 26.1
    assert payload["relative_humidity"] == 62 and payload["wind_speed"] == 8.4
    assert payload["weather"] == "基本晴" and payload["icon"] == "🌤️"
    assert payload["cached"] is False
    assert fetcher.calls[1][1]["longitude"] == 114.93


def test_env_city_skips_ip_lookup():
    fetcher = FakeFetcher()
    service = WeatherService(fetcher=fetcher, city_override="南昌", clock=lambda: 1000.0)
    payload = service.current()
    assert payload["city"] == "南昌" and payload["source"] == "env"
    assert all("ipwho.is" not in url for url, _ in fetcher.calls)


def test_english_ip_city_maps_to_the_nearest_chinese_name():
    fetcher = FakeFetcher(geo={"success": True, "city": "Nanchang", "latitude": 28.68, "longitude": 115.86})
    service = WeatherService(fetcher=fetcher, city_override="", clock=lambda: 1000.0)
    assert service.current()["city"] == "南昌"


def test_unknown_city_override_falls_back_to_ip_with_note():
    fetcher = FakeFetcher()
    service = WeatherService(fetcher=fetcher, city_override="火星", clock=lambda: 1000.0)
    payload = service.current()
    assert payload["source"] == "ip" and "不在内置表" in payload["note"]


def test_ip_failure_falls_back_to_default_city():
    fetcher = FakeFetcher(geo_error=True)
    service = WeatherService(fetcher=fetcher, city_override="", clock=lambda: 1000.0)
    payload = service.current()
    assert payload["city"] == "北京" and payload["source"] == "default"
    assert "回退" in payload["note"]


def test_cache_holds_thirty_minutes_and_refresh_forces():
    fetcher = FakeFetcher()
    clock = {"now": 1000.0}
    service = WeatherService(fetcher=fetcher, city_override="赣州", clock=lambda: clock["now"])

    first = service.current()
    assert first["cached"] is False
    calls_after_first = len(fetcher.calls)

    second = service.current()
    assert second["cached"] is True and len(fetcher.calls) == calls_after_first

    clock["now"] += 29 * 60
    assert service.current()["cached"] is True
    clock["now"] += 2 * 60  # 31 分钟：过期重取
    assert service.current()["cached"] is False
    assert len(fetcher.calls) > calls_after_first

    forced = service.current(force=True)
    assert forced["cached"] is False


def test_picking_a_builtin_city_skips_ip_and_uses_its_coordinates():
    """界面选城市：直接查内置表，不问 IP（省一次出站），并带上整份城市清单给前端下拉。"""
    fetcher = FakeFetcher()
    service = WeatherService(fetcher=fetcher, city_override="赣州", clock=lambda: 1000.0)
    payload = service.current(city="上海")
    assert payload["city"] == "上海" and payload["source"] == "pick"
    assert all("ipwho.is" not in url for url, _ in fetcher.calls)
    assert fetcher.calls[-1][1]["latitude"] == 31.23
    assert fetcher.calls[-1][1]["longitude"] == 121.47
    assert "上海" in payload["cities"] and "赣州" in payload["cities"]


def test_switching_cities_keeps_one_cache_slot_per_city():
    fetcher = FakeFetcher()
    clock = {"now": 1000.0}
    service = WeatherService(fetcher=fetcher, city_override="赣州", clock=lambda: clock["now"])

    service.current(city="上海")
    calls = len(fetcher.calls)
    assert service.current(city="上海")["cached"] is True  # 同城命中缓存
    assert len(fetcher.calls) == calls

    assert service.current(city="北京")["cached"] is False  # 换城市要取新数据
    assert len(fetcher.calls) == calls + 1
    assert service.current(city="上海")["cached"] is True   # 切回来仍是缓存，不重复出站
    assert len(fetcher.calls) == calls + 1

    clock["now"] += 31 * 60
    assert service.current(city="上海")["cached"] is False  # 过期照旧重取


def test_unknown_city_is_rejected():
    service = WeatherService(fetcher=FakeFetcher(), city_override="")
    with pytest.raises(WeatherError, match="内置城市表"):
        service.current(city="火星")

    app = FastAPI()
    app.include_router(create_weather_router(service=WeatherService(
        fetcher=FakeFetcher(), city_override="赣州")))
    response = TestClient(app).get("/api/weather", params={"city": "火星"})
    assert response.status_code == 422
    assert "内置城市表" in response.json()["detail"]


def test_five_day_forecast_carries_every_requested_field():
    """用户要的 8 项：体感、湿度、降水可能性、风向、风速、气压、日出、日落。"""
    fetcher = FakeFetcher(forecast={**FORECAST, **DAILY})
    service = WeatherService(fetcher=fetcher, city_override="南昌", clock=lambda: 1000.0)
    days = service.current()["forecast"]
    assert len(days) == 3

    today = days[0]
    assert today["day"] == "2026-10-06" and today["weekday"] == "周二"
    assert (today["weather"], today["icon"]) == ("基本晴", "🌤️")
    assert (today["high"], today["low"]) == (26.4, 17.1)
    assert (today["apparent_high"], today["apparent_low"]) == (28.0, 16.2)  # 体感
    assert today["humidity"] == 60 and today["precipitation"] == 10
    assert today["wind_direction"] == "东北风" and today["wind_speed"] == 12.4
    assert today["pressure"] == 1021
    assert (today["sunrise"], today["sunset"]) == ("06:12", "17:52")

    # 风向按八方位：200° 偏南、350° 偏北。
    assert days[1]["wind_direction"] == "南风"
    assert days[2]["wind_direction"] == "北风"
    assert days[1]["weather"] == "小雨"


def test_forecast_request_asks_for_five_days_of_daily_fields():
    fetcher = FakeFetcher()
    service = WeatherService(fetcher=fetcher, city_override="南昌", clock=lambda: 1000.0)
    service.current()
    params = fetcher.calls[-1][1]
    assert params["forecast_days"] == 5
    for field in ("apparent_temperature_max", "apparent_temperature_min", "precipitation_probability_max",
                  "relative_humidity_2m_mean", "wind_speed_10m_max", "wind_direction_10m_dominant",
                  "surface_pressure_mean", "sunrise", "sunset"):
        assert field in params["daily"], field


def test_forecast_is_empty_when_the_service_omits_daily():
    fetcher = FakeFetcher()  # 老响应/降级响应里没有 daily
    service = WeatherService(fetcher=fetcher, city_override="南昌", clock=lambda: 1000.0)
    payload = service.current()
    assert payload["forecast"] == []
    assert payload["temperature"] == 24.3  # 实况照常


def test_daily_forecast_survives_short_and_null_arrays():
    days = daily_forecast({"daily": {
        "time": ["2026-10-06"],
        "temperature_2m_max": [20.0],
        "weather_code": [None],
        "relative_humidity_2m_mean": [None],
    }})
    assert len(days) == 1
    day = days[0]
    assert day["high"] == 20.0 and day["low"] is None and day["humidity"] is None
    assert day["precipitation"] is None and day["pressure"] is None
    assert day["weather"] == "未知" and day["sunrise"] == "" and day["wind_direction"] == ""
    assert days[0]["weekday"] == "周二"
    assert daily_forecast(None) == []


def test_wind_label_covers_eight_directions():
    assert [wind_label(angle) for angle in (0, 45, 90, 135, 180, 225, 270, 315)] == [
        "北风", "东北风", "东风", "东南风", "南风", "西南风", "西风", "西北风"]
    assert wind_label(359) == "北风" and wind_label(None) == "" and wind_label("x") == ""


def test_describe_weather_covers_unknown_codes():
    assert describe_weather(61) == {"weather": "小雨", "icon": "🌧️"}
    assert describe_weather(None)["weather"] == "未知"


def test_http_json_refuses_hosts_outside_the_allowlist():
    with pytest.raises(WeatherError, match="白名单"):
        http_json("https://evil.example/data")
    with pytest.raises(WeatherError, match="白名单"):
        http_json("http://api.open-meteo.com/v1/forecast")  # 必须 https


def test_router_returns_502_when_service_fails():
    def boom(url, params=None):
        raise WeatherError("无法连接天气服务")

    app = FastAPI()
    app.include_router(create_weather_router(service=WeatherService(fetcher=boom)))
    response = TestClient(app).get("/api/weather")
    assert response.status_code == 502
    assert "无法连接天气服务" in response.json()["detail"]
