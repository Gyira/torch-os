"""天气：Open-Meteo 实况 + 5 天日级预报（无 Key）+ IP 粗略定位；只做白名单出站与内存短缓存。

- 出站域名只有两个：`api.open-meteo.com`（实况与日级预报，一次请求一起取）与 `ipwho.is`（按 IP 猜城市）；
  参照 `share_import.py` 的白名单纪律：https、固定 host、超时、大小上限、无重试；
- 城市来源优先级：**界面上选的城市**（`?city=`，只认内置城市表，不做在线 geocoding）→ `WEATHER_CITY`
  （.env，需重启生效）→ IP 定位 → 内置城市表兜底；
- 5 天预报的日级字段照用户要求：体感、湿度、降水可能性、风向、风速、气压、日出、日落（WMO 码翻中文、
  风向按度数翻成八方位中文）；
- 结果缓存 30 分钟（注入式闭包缓存，仿 scheduler_state 模式），手动刷新可强制重取；
- 纯读：不写保险库、不发审计、不碰模型 Key。
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, HTTPException, Query

SHANGHAI = ZoneInfo("Asia/Shanghai")
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEO_URL = "https://ipwho.is/"
ALLOWED_HOSTS = ("api.open-meteo.com", "ipwho.is")
TIMEOUT_SECONDS = 8.0
MAX_RESPONSE_BYTES = 256_000
CACHE_SECONDS = 30 * 60
DEFAULT_CITY = ("北京", 39.90, 116.41)   # IP 定位不可用时回退到这里（可在界面上换城市，选择存本机）

# 一次拿够 5 天：日级变量都是 Open-Meteo 现成的（实测 2026-10-06 全部可用），
# 不用把逐小时数据拉回来自己聚合。
DAILY_FIELDS = (
    "weather_code", "temperature_2m_max", "temperature_2m_min",
    "apparent_temperature_max", "apparent_temperature_min",
    "precipitation_probability_max", "relative_humidity_2m_mean",
    "wind_speed_10m_max", "wind_direction_10m_dominant",
    "surface_pressure_mean", "sunrise", "sunset",
)
FORECAST_DAYS = 5
WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
# 风向取八方位（气象习惯：说的是风从哪儿来，与 Open-Meteo 的 dominant direction 一致）。
WIND_LABELS = ("北风", "东北风", "东风", "东南风", "南风", "西南风", "西风", "西北风")

# 小内置城市表：中文城市名 → 经纬度（未知城市回退 IP 定位，不做在线 geocoding）。
CITY_DB: dict[str, tuple[float, float]] = {
    "赣州": (25.83, 114.93), "南昌": (28.68, 115.86), "北京": (39.90, 116.41),
    "上海": (31.23, 121.47), "广州": (23.13, 113.26), "深圳": (22.54, 114.06),
    "杭州": (30.27, 120.16), "武汉": (30.59, 114.31), "成都": (30.57, 104.07),
    "重庆": (29.56, 106.55), "西安": (34.34, 108.94), "南京": (32.06, 118.80),
    "长沙": (28.23, 112.94), "合肥": (31.82, 117.23), "福州": (26.07, 119.30),
    "厦门": (24.48, 118.09), "郑州": (34.75, 113.63), "济南": (36.65, 117.12),
    "天津": (39.13, 117.20), "沈阳": (41.81, 123.43), "哈尔滨": (45.80, 126.53),
    "昆明": (25.04, 102.71), "贵阳": (26.65, 106.63), "南宁": (22.82, 108.37),
    "兰州": (36.06, 103.83), "乌鲁木齐": (43.83, 87.62), "拉萨": (29.65, 91.14),
    "海口": (20.04, 110.32), "三亚": (18.25, 109.51), "石家庄": (38.04, 114.51),
    "太原": (37.87, 112.55), "苏州": (31.30, 120.58), "青岛": (36.07, 120.38),
}

# WMO weather code → (中文描述, 图标)。
WMO_LABELS: dict[int, tuple[str, str]] = {
    0: ("晴", "☀️"), 1: ("基本晴", "🌤️"), 2: ("多云", "⛅"), 3: ("阴", "☁️"),
    45: ("雾", "🌫️"), 48: ("雾凇", "🌫️"),
    51: ("毛毛雨", "🌦️"), 53: ("毛毛雨", "🌦️"), 55: ("毛毛雨", "🌦️"),
    56: ("冻毛毛雨", "🌧️"), 57: ("冻毛毛雨", "🌧️"),
    61: ("小雨", "🌧️"), 63: ("中雨", "🌧️"), 65: ("大雨", "🌧️"),
    66: ("冻雨", "🌧️"), 67: ("冻雨", "🌧️"),
    71: ("小雪", "🌨️"), 73: ("中雪", "🌨️"), 75: ("大雪", "🌨️"), 77: ("霰", "🌨️"),
    80: ("阵雨", "🌦️"), 81: ("阵雨", "🌦️"), 82: ("强阵雨", "🌧️"),
    85: ("阵雪", "🌨️"), 86: ("阵雪", "🌨️"),
    95: ("雷阵雨", "⛈️"), 96: ("雷阵雨夹冰雹", "⛈️"), 99: ("雷阵雨夹冰雹", "⛈️"),
}


class WeatherError(Exception):
    """出站失败或响应不可用；路由层统一转 502。"""


def _has_cjk(text: str) -> bool:
    return any("一" <= char <= "鿿" for char in text)


def describe_weather(code: Any) -> dict[str, str]:
    label, icon = WMO_LABELS.get(int(code) if code is not None else -1, ("未知", "🌡️"))
    return {"weather": label, "icon": icon}


def wind_label(degrees: Any) -> str:
    """风向度数 → 八方位中文；拿不到就给空串（前端显示 —）。"""
    try:
        angle = float(degrees) % 360
    except (TypeError, ValueError):
        return ""
    return WIND_LABELS[int((angle + 22.5) % 360 // 45)]


def _day_number(value: Any, digits: int = 1) -> float | None:
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _clock_time(value: Any) -> str:
    """'2026-10-06T06:12' → '06:12'（日出日落只留时刻）。"""
    text = str(value or "")
    return text[11:16] if len(text) >= 16 else ""


def _nth(values: Any, index: int) -> Any:
    return values[index] if isinstance(values, list) and index < len(values) else None


def daily_forecast(payload: dict[str, Any] | None, limit: int = FORECAST_DAYS) -> list[dict[str, Any]]:
    """Open-Meteo 的日级并行数组 → 逐日字典（缺字段给 None，长度不足就少几天）。

    第 0 天就是今天（Open-Meteo 固定从本地今天开始给）。
    """
    daily = (payload or {}).get("daily") or {}
    days: list[dict[str, Any]] = []
    for index, day in enumerate((daily.get("time") or [])[:limit]):
        code = _nth(daily.get("weather_code"), index)
        humidity = _day_number(_nth(daily.get("relative_humidity_2m_mean"), index), 0)
        precipitation = _day_number(_nth(daily.get("precipitation_probability_max"), index), 0)
        pressure = _day_number(_nth(daily.get("surface_pressure_mean"), index), 0)
        days.append({
            "day": str(day),
            "weekday": weekday_label(str(day)),
            "high": _day_number(_nth(daily.get("temperature_2m_max"), index)),
            "low": _day_number(_nth(daily.get("temperature_2m_min"), index)),
            "apparent_high": _day_number(_nth(daily.get("apparent_temperature_max"), index)),
            "apparent_low": _day_number(_nth(daily.get("apparent_temperature_min"), index)),
            "humidity": None if humidity is None else int(humidity),
            "precipitation": None if precipitation is None else int(precipitation),
            "wind_direction": wind_label(_nth(daily.get("wind_direction_10m_dominant"), index)),
            "wind_speed": _day_number(_nth(daily.get("wind_speed_10m_max"), index)),
            "pressure": None if pressure is None else int(pressure),
            "sunrise": _clock_time(_nth(daily.get("sunrise"), index)),
            "sunset": _clock_time(_nth(daily.get("sunset"), index)),
            **describe_weather(code),
        })
    return days


def weekday_label(day: str) -> str:
    try:
        return WEEKDAYS[datetime.strptime(day, "%Y-%m-%d").weekday()]
    except ValueError:
        return ""


def http_json(url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """One allow-listed GET: https, fixed hosts, timeout, byte cap, no retry."""
    host = httpx.URL(url).host or ""
    if httpx.URL(url).scheme != "https" or host not in ALLOWED_HOSTS:
        raise WeatherError(f"出站域名不在白名单：{host}")
    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS, trust_env=False) as client:
            response = client.get(url, params=params or {})
            if response.status_code != 200:
                raise WeatherError(f"天气服务返回 {response.status_code}，请稍后再试")
            body = response.content
    except (httpx.TimeoutException, httpx.HTTPError) as exc:
        raise WeatherError("无法连接天气服务，请检查网络后重试") from exc
    if len(body) > MAX_RESPONSE_BYTES:
        raise WeatherError("天气服务返回内容超过大小上限，已停止抓取")
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise WeatherError("天气服务返回的内容不是 JSON") from exc


class WeatherService:
    def __init__(self, fetcher: Callable[..., dict[str, Any]] | None = None,
                 city_override: str | None = None, clock: Callable[[], float] | None = None):
        self.fetcher = fetcher or http_json
        self.city_override = (city_override if city_override is not None
                              else os.getenv("WEATHER_CITY", "")).strip()
        self.clock = clock or (lambda: datetime.now().timestamp())
        self.cache: dict[str, dict[str, Any]] = {}  # 城市 → {fetched_at, payload}

    def locate(self, pick: str = "") -> dict[str, Any]:
        """定位：`pick`（用户在界面上选的城市）优先，其次 `WEATHER_CITY`，再 IP，最后内置兜底。

        `pick` 只认内置城市表——不做在线 geocoding，所以选了就直接有经纬度，连 IP 都不用问。
        """
        picked = (pick or "").strip()
        if picked and picked not in CITY_DB:
            raise WeatherError(f"内置城市表里没有“{picked}”")
        if picked:
            latitude, longitude = CITY_DB[picked]
            return {"city": picked, "latitude": latitude, "longitude": longitude, "source": "pick"}
        if self.city_override:
            if self.city_override in CITY_DB:
                latitude, longitude = CITY_DB[self.city_override]
                return {"city": self.city_override, "latitude": latitude,
                        "longitude": longitude, "source": "env"}
            # 非内置城市：留在 IP 定位上，并在说明里点出来，不上线 geocoding。
            note = f"城市“{self.city_override}”不在内置表里，暂按 IP 定位"
        else:
            note = ""
        try:
            payload = self.fetcher(GEO_URL)
            if payload.get("success") is not False and payload.get("latitude") is not None:
                latitude, longitude = float(payload["latitude"]), float(payload["longitude"])
                name = str(payload.get("city") or "未知")
                if not _has_cjk(name):
                    # ipwho.is 对国内城市常给英文名：就近映射回内置城市表的中文名。
                    nearest, (near_lat, near_lon) = min(
                        CITY_DB.items(),
                        key=lambda item: (item[1][0] - latitude) ** 2 + (item[1][1] - longitude) ** 2)
                    if (near_lat - latitude) ** 2 + (near_lon - longitude) ** 2 < 0.8:
                        name = nearest
                return {"city": name, "latitude": latitude,
                        "longitude": longitude, "source": "ip", "note": note}
        except WeatherError:
            pass
        city, latitude, longitude = DEFAULT_CITY
        return {"city": city, "latitude": latitude, "longitude": longitude,
                "source": "default", "note": note or "IP 定位不可用，已回退到默认城市"}

    def current(self, force: bool = False, city: str = "") -> dict[str, Any]:
        place = self.locate(city)
        now = self.clock()
        # 缓存按城市分开：来回切城市不互相冲掉，切回来仍然命中 30 分钟缓存。
        entry = self.cache.get(place["city"])
        if entry and not force and now - float(entry.get("fetched_at") or 0) < CACHE_SECONDS:
            return {**entry["payload"], "cached": True}
        forecast = self.fetcher(FORECAST_URL, params={
            "latitude": place["latitude"], "longitude": place["longitude"],
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
            "daily": ",".join(DAILY_FIELDS),
            "forecast_days": FORECAST_DAYS,
            "timezone": "auto",
        })
        current = forecast.get("current") or {}
        if current.get("temperature_2m") is None:
            raise WeatherError("天气服务没有返回实况数据")
        payload = {
            "city": place["city"], "source": place["source"],
            "note": place.get("note", ""),
            "temperature": current.get("temperature_2m"),
            "apparent_temperature": current.get("apparent_temperature"),
            "relative_humidity": current.get("relative_humidity_2m"),
            "wind_speed": current.get("wind_speed_10m"),
            "weather_code": current.get("weather_code"),
            **describe_weather(current.get("weather_code")),
            "observed_at": current.get("time"),
            "forecast": daily_forecast(forecast),
            # 内置城市表随回调一起带上：前端的下拉直接用这一份，不再有第二份清单。
            "cities": list(CITY_DB),
            "fetched_at": datetime.fromtimestamp(now, SHANGHAI).isoformat(timespec="seconds"),
            "cached": False,
        }
        self.cache[place["city"]] = {"fetched_at": now, "payload": payload}
        return payload


def create_weather_router(prefix: str = "/api", service: WeatherService | None = None) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["weather"])
    weather = service or WeatherService()

    @router.get("/weather")
    def get_weather(refresh: int = Query(default=0, ge=0, le=1),
                    city: str = Query(default="", max_length=30)):
        picked = city.strip()
        if picked and picked not in CITY_DB:
            raise HTTPException(422, f"内置城市表里没有“{picked}”，请在列表里选")
        try:
            return weather.current(force=bool(refresh), city=picked)
        except WeatherError as error:
            raise HTTPException(502, str(error)) from error

    return router
