"""列车运行追踪小组件：独立于恋爱系统。

数据放在当前个人数据库的 `.torch-os/train-tracker/`：
- `timetable.json`：本地车次时刻表（可在组件里导入，也可按车次手动从 12306 同步）。
- `train-list.js` / `station-name.js`：12306 的辅助缓存，只在需要时下载。

只做用户主动点击的「从 12306 同步」，不做后台轮询，也不接 12306 实时位置；
同步失败时保留本地时刻表。12306 官方接口可能变动或限流，失败会以可读错误返回。
"""
from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from .vault import safe_note_path
from .vault_connection import VaultReadOnlyError, ensure_vault_writable

TRACKER_RELATIVE = ".torch-os/train-tracker"
TIMETABLE_NAME = "timetable.json"
MAX_TIMETABLE_BYTES = 5 * 1024 * 1024
MAX_HTTP_BYTES = 2 * 1024 * 1024
TRAIN_CODE_PATTERN = re.compile(r"^[A-Z]\d{1,5}$")
TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TZ = ZoneInfo("Asia/Shanghai")

KYFW_HOST = "kyfw.12306.cn"
KYFW_BASE = f"https://{KYFW_HOST}"
INIT_URL = f"{KYFW_BASE}/otn/leftTicket/init?linktypeid=dc"
QUERY_URL = f"{KYFW_BASE}/otn/leftTicket/queryZ"
STOPS_URL = f"{KYFW_BASE}/otn/czxx/queryByTrainNo"
STATIONS_URL = f"{KYFW_BASE}/otn/resources/js/framework/station_name.js"
TRAIN_LIST_URL = f"{KYFW_BASE}/otn/resources/js/query/train_list.js?scriptVersion=1.0"
KYFW_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": f"{KYFW_BASE}/otn/leftTicket/init?linktypeid=dc",
}

class TrainSyncError(Exception):
    """12306 同步失败；消息会直接显示在组件里。"""


# ── 本地文件 ─────────────────────────────────────────────────────────

def _timetable_path(vault_dir: Path | str) -> Path:
    return safe_note_path(Path(vault_dir), f"{TRACKER_RELATIVE}/{TIMETABLE_NAME}")


def _normalize_code(value: Any) -> str:
    return str(value or "").strip().upper()


def valid_train_code(value: Any) -> bool:
    return bool(TRAIN_CODE_PATTERN.fullmatch(_normalize_code(value)))


def _clean_time(value: Any) -> str:
    text = str(value or "").strip()
    return text if TIME_PATTERN.fullmatch(text) else ""


def _clean_stop(raw: Any) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    station = str(raw.get("station") or raw.get("station_name") or "").strip()
    if not station:
        return None
    arrive = raw.get("arrive") or raw.get("arrive_time")
    depart = raw.get("depart") or raw.get("start_time")
    return {
        "station": station[:60],
        "arrive": _clean_time(arrive),
        "depart": _clean_time(depart),
        "stopover": str(raw.get("stopover") or raw.get("stopover_time") or "").strip()[:20],
    }


def normalize_train(code: Any, raw: Any) -> dict[str, Any] | None:
    """把导入/同步来的车次整理成统一结构；没有有效经停站就返回 None。"""
    train_code = _normalize_code(code) or _normalize_code(raw.get("train_code") if isinstance(raw, dict) else "")
    if not valid_train_code(train_code) or not isinstance(raw, dict):
        return None
    stops: list[dict[str, str]] = []
    raw_stops = raw.get("stops")
    if isinstance(raw_stops, list):
        for item in raw_stops:
            stop = _clean_stop(item)
            if stop:
                stops.append(stop)
    if not stops:
        return None
    first, last = stops[0], stops[-1]
    depart_time = _clean_time(raw.get("depart_time")) or first["depart"] or first["arrive"]
    arrive_time = _clean_time(raw.get("arrive_time")) or last["arrive"] or last["depart"]
    duration = raw.get("duration_minutes")
    if not isinstance(duration, int) or duration < 0:
        duration = _duration_minutes(depart_time, arrive_time)
    return {
        "train_code": train_code,
        "train_no": str(raw.get("train_no") or "")[:40],
        "from_station": str(raw.get("from_station") or first["station"])[:60],
        "to_station": str(raw.get("to_station") or last["station"])[:60],
        "depart_time": depart_time,
        "arrive_time": arrive_time,
        "duration_minutes": duration,
        "stops": stops,
        "source": str(raw.get("source") or "local")[:20],
        "synced_at": str(raw.get("synced_at") or "")[:40],
    }


def _duration_minutes(depart: str, arrive: str) -> int:
    start, end = _hm(depart), _hm(arrive)
    if start is None or end is None:
        return 0
    if end < start:
        end += 24 * 60
    return max(0, end - start)


def load_timetable(vault_dir: Path | str) -> dict[str, dict[str, Any]]:
    path = _timetable_path(vault_dir)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for key, value in data.items():
        code = _normalize_code(key)
        train = normalize_train(code, value)
        if train:
            result[code] = train
    return result


def save_timetable(vault_dir: Path | str, timetable: dict[str, dict[str, Any]],
                   *, read_only: bool = False) -> None:
    try:
        ensure_vault_writable(read_only, "保存列车时刻表")
    except VaultReadOnlyError as error:
        raise HTTPException(409, str(error)) from error
    path = _timetable_path(vault_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(timetable, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def migrate_legacy_timetable(vault_dir: Path | str, candidates: list[Path] | tuple[Path, ...],
                             *, read_only: bool = False) -> str:
    """把旧位置的 train_timetable.json 复制到新目录；只复制、不删除。"""
    path = _timetable_path(vault_dir)
    if path.is_file() or read_only:
        return ""
    for candidate in candidates:
        try:
            source = Path(candidate)
        except TypeError:
            continue
        if not source.is_file():
            continue
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, path)
        except OSError:
            continue
        return str(source)
    return ""


# ── 当前运行状态（本地时刻模拟）─────────────────────────────────────

def _hm(value: Any) -> int | None:
    text = str(value or "").strip()
    if not TIME_PATTERN.fullmatch(text):
        return None
    hour, minute = text.split(":")
    return int(hour) * 60 + int(minute)


def _timeline(stops: list[dict[str, str]]) -> list[dict[str, Any]]:
    """把 HH:MM 转成分钟；跨零点的车次按时间顺序补 24 小时。"""
    points: list[dict[str, Any]] = []
    offset = 0
    previous: int | None = None
    for stop in stops:
        arrive = _hm(stop.get("arrive"))
        depart = _hm(stop.get("depart"))
        if arrive is not None:
            if previous is not None and arrive + offset < previous - 12 * 60:
                offset += 24 * 60
            arrive += offset
            previous = arrive if previous is None else max(previous, arrive)
        if depart is not None:
            if previous is not None and depart + offset < previous - 12 * 60:
                offset += 24 * 60
            depart += offset
            previous = depart if previous is None else max(previous, depart)
        points.append({**stop, "arrive_min": arrive, "depart_min": depart})
    return points


def train_status(train: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    stops = list(train.get("stops") or [])
    if not stops:
        return {
            "status": "unknown", "status_text": "缺少时刻表",
            "message": "这趟车还没有本地时刻表；导入时刻表或从 12306 同步后再看运行区间。",
            "current_station": None, "next_station": None,
            "countdown_minutes": None, "progress_pct": 0, "segment_progress_pct": None,
        }
    points = _timeline(stops)
    current = now or datetime.now(TZ)
    now_min = current.hour * 60 + current.minute
    # 跨零点：如果第一站发车在下午/晚上，而现在是凌晨，视作次日凌晨。
    first_depart = next((p["depart_min"] for p in points if p["depart_min"] is not None), None)
    last_arrive = next((p["arrive_min"] for p in reversed(points) if p["arrive_min"] is not None), None)
    if first_depart is None:
        first_depart = next((p["arrive_min"] for p in points if p["arrive_min"] is not None), now_min)
    if last_arrive is None:
        last_arrive = first_depart
    # 只有真正跨零点的车次，且现在落在“凌晨到达窗口”里，才把现在算作次日；
    # 否则凌晨/上午都是等今天这趟车，不能整天显示“已到达”。
    if last_arrive > 24 * 60:
        arrival_window_end = (last_arrive - 24 * 60) + 30
        if now_min < first_depart and now_min <= arrival_window_end:
            now_min += 24 * 60
    span = max(1, last_arrive - first_depart)
    progress = max(0.0, min(100.0, round((now_min - first_depart) / span * 100, 1)))

    if now_min < first_depart:
        first = points[0]
        second = points[1] if len(points) > 1 else None
        return {
            "status": "before_depart", "status_text": "待发车",
            "message": f"{train.get('train_code')} 次 {first.get('depart') or first.get('arrive') or ''} 从 {first['station']} 发车",
            "current_station": first["station"],
            "next_station": second["station"] if second else None,
            "next_depart_time": first.get("depart") or first.get("arrive"),
            "countdown_minutes": int(first_depart - now_min),
            "progress_pct": 0, "segment_progress_pct": None,
        }
    if now_min >= last_arrive:
        return {
            "status": "arrived", "status_text": "已到达",
            "message": f"{train.get('train_code')} 次已到达 {points[-1]['station']}",
            "current_station": points[-1]["station"], "next_station": None,
            "countdown_minutes": 0, "progress_pct": 100, "segment_progress_pct": None,
        }
    for index, point in enumerate(points):
        if (point["arrive_min"] is not None and point["depart_min"] is not None
                and point["arrive_min"] <= now_min <= point["depart_min"]
                and index not in (0, len(points) - 1)):
            next_point = points[index + 1] if index + 1 < len(points) else None
            return {
                "status": "at_station", "status_text": "停站中",
                "message": f"{train.get('train_code')} 次停靠 {point['station']}，"
                           f"{max(0, int(point['depart_min'] - now_min))} 分钟后发车",
                "current_station": point["station"],
                "next_station": next_point["station"] if next_point else None,
                "next_depart_time": point.get("depart"),
                "countdown_minutes": int(point["depart_min"] - now_min),
                "progress_pct": progress, "segment_progress_pct": None,
            }
        if point["depart_min"] is not None and index + 1 < len(points):
            next_point = points[index + 1]
            if next_point["arrive_min"] is not None and point["depart_min"] <= now_min < next_point["arrive_min"]:
                segment = max(1, next_point["arrive_min"] - point["depart_min"])
                segment_pct = max(0.0, min(100.0, round((now_min - point["depart_min"]) / segment * 100, 1)))
                return {
                    "status": "running", "status_text": "运行中",
                    "message": f"{train.get('train_code')} 次，{point['station']} → {next_point['station']}，"
                               f"预计 {next_point.get('arrive') or ''} 到达",
                    "current_station": point["station"],
                    "next_station": next_point["station"],
                    "next_arrive_time": next_point.get("arrive"),
                    "countdown_minutes": int(next_point["arrive_min"] - now_min),
                    "progress_pct": progress, "segment_progress_pct": segment_pct,
                }
    return {
        "status": "unknown", "status_text": "状态未知",
        "message": "时刻表里没有可用的到发时间。",
        "current_station": None, "next_station": None,
        "countdown_minutes": None, "progress_pct": progress, "segment_progress_pct": None,
    }


# ── 12306 手动同步（官方接口，尽力而为）─────────────────────────────

def _check_host(url: str) -> None:
    parsed = httpx.URL(url)
    if parsed.scheme != "https" or parsed.host != KYFW_HOST:
        raise TrainSyncError("只允许访问 12306 官方 HTTPS 接口。")


def _client() -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(15.0), follow_redirects=False, trust_env=False,
        headers=KYFW_HEADERS,
    )


def _get(client: httpx.Client, url: str, *, params: dict[str, Any] | None = None,
         max_bytes: int = MAX_HTTP_BYTES) -> httpx.Response:
    _check_host(url)
    try:
        response = client.get(url, params=params)
    except httpx.HTTPError as error:
        raise TrainSyncError(f"连接 12306 失败：{error}") from error
    if len(response.content) > max_bytes:
        raise TrainSyncError("12306 返回内容超过大小上限。")
    return response


def _bootstrap(client: httpx.Client) -> None:
    _get(client, INIT_URL)
    if not client.cookies.get("JSESSIONID"):
        raise TrainSyncError("12306 没有返回会话 Cookie，稍后再试。")


def _station_map(client: httpx.Client) -> dict[str, str]:
    response = _get(client, STATIONS_URL, max_bytes=MAX_HTTP_BYTES)
    text = response.text
    stations: dict[str, str] = {}
    for segment in text.split("@"):
        parts = segment.split("|")
        if len(parts) >= 3 and parts[1].strip() and parts[2].strip():
            stations[parts[1].strip()] = parts[2].strip()
    if not stations:
        raise TrainSyncError("12306 车站表为空，暂时无法同步。")
    return stations


def _query_train_no(client: httpx.Client, code: str, date_text: str,
                    from_station: str, to_station: str,
                    stations: dict[str, str]) -> str:
    from_code = stations.get(from_station) or stations.get(from_station.removesuffix("站"))
    to_code = stations.get(to_station) or stations.get(to_station.removesuffix("站"))
    if not from_code or not to_code:
        missing = from_station if not from_code else to_station
        raise TrainSyncError(f"12306 车站表里找不到「{missing}」，请检查站名。")
    params = {
        "leftTicketDTO.train_date": date_text,
        "leftTicketDTO.from_station": from_code,
        "leftTicketDTO.to_station": to_code,
        "purpose_codes": "ADULT",
    }
    url = QUERY_URL
    for _attempt in range(4):
        response = _get(client, url, params=params, max_bytes=MAX_HTTP_BYTES)
        try:
            payload = response.json()
        except ValueError as error:
            raise TrainSyncError("12306 余票接口返回了非 JSON 内容。") from error
        rotated = payload.get("c_url")
        if isinstance(rotated, str) and rotated.startswith("leftTicket/"):
            url = f"{KYFW_BASE}/otn/{rotated}"
            continue
        rows = (payload.get("data") or {}).get("result") or []
        for row in rows:
            fields = str(row).split("|")
            if len(fields) > 3 and fields[3].upper() == code:
                train_no = fields[2].strip()
                if train_no:
                    return train_no
        raise TrainSyncError(
            f"12306 在 {date_text} 的「{from_station} → {to_station}」里没有找到 {code}；"
            "可能当天不开行，或起止站填错了。"
        )
    raise TrainSyncError("12306 查询入口一直在切换，稍后再试。")


def _query_stops(client: httpx.Client, train_no: str, date_text: str) -> list[dict[str, Any]]:
    response = _get(client, STOPS_URL, params={
        "train_no": train_no,
        "from_station_telecode": "BBB",
        "to_station_telecode": "BBB",
        "depart_date": date_text,
    }, max_bytes=MAX_HTTP_BYTES)
    try:
        payload = response.json()
    except ValueError as error:
        raise TrainSyncError("12306 经停站接口返回了非 JSON 内容。") from error
    stops = (payload.get("data") or {}).get("data") or []
    if not isinstance(stops, list) or not stops:
        raise TrainSyncError("12306 没有返回这趟车的经停站。")
    return stops


def fetch_12306_train(code: str, from_station: str, to_station: str,
                      date_text: str) -> dict[str, Any]:
    """从 12306 官方接口取一趟车的经停站；失败抛 TrainSyncError。"""
    code = _normalize_code(code)
    if not valid_train_code(code):
        raise TrainSyncError("车次号格式不正确。")
    if not DATE_PATTERN.fullmatch(date_text):
        raise TrainSyncError("日期格式应为 YYYY-MM-DD。")
    if not from_station.strip() or not to_station.strip():
        raise TrainSyncError("本地没有这趟车的起止站；请在同步表单里填写出发站和到达站。")
    client = _client()
    try:
        _bootstrap(client)
        stations = _station_map(client)
        train_no = _query_train_no(
            client, code, date_text, from_station.strip(), to_station.strip(), stations)
        raw_stops = _query_stops(client, train_no, date_text)
    finally:
        client.close()
    stops = []
    for raw in raw_stops:
        stop = _clean_stop(raw)
        if stop:
            stops.append(stop)
    if not stops:
        raise TrainSyncError("12306 返回的经停站为空。")
    first, last = stops[0], stops[-1]
    return normalize_train(code, {
        "train_code": code,
        "train_no": train_no,
        "from_station": first["station"],
        "to_station": last["station"],
        "depart_time": first["depart"] or first["arrive"],
        "arrive_time": last["arrive"] or last["depart"],
        "stops": stops,
        "source": "12306",
        "synced_at": datetime.now(TZ).isoformat(timespec="seconds"),
    }) or {}


# ── API ──────────────────────────────────────────────────────────────

class TrainSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    code: str = Field(min_length=1, max_length=12)
    from_station: str = Field(default="", max_length=60)
    to_station: str = Field(default="", max_length=60)
    date: str = Field(default="", max_length=10)


def _today_text() -> str:
    return datetime.now(TZ).date().isoformat()


def create_train_tracker_router(vault_dir: Path | str, *, read_only: bool = False,
                                legacy_paths: list[Path] | tuple[Path, ...] = (),
                                sync_enabled: bool = True,
                                fetcher: Callable[[str, str, str, str], dict[str, Any]] | None = None,
                                prefix: str = "/api") -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["train-tracker"])
    fetch = fetcher or fetch_12306_train

    def prepare() -> str:
        return migrate_legacy_timetable(vault_dir, legacy_paths, read_only=read_only)

    @router.get("/train-tracker/status")
    def tracker_status():
        migrated = prepare()
        timetable = load_timetable(vault_dir)
        return {
            "data_dir": TRACKER_RELATIVE,
            "timetable_path": f"{TRACKER_RELATIVE}/{TIMETABLE_NAME}",
            "timetable_exists": _timetable_path(vault_dir).is_file(),
            "trains": sorted(timetable),
            "count": len(timetable),
            "legacy_migrated": bool(migrated),
            "sync_enabled": sync_enabled,
        }

    @router.get("/train-tracker/train")
    def tracker_train(code: str = Query(pattern=r"^[A-Za-z]\d{1,5}$")):
        prepare()
        normalized = _normalize_code(code)
        timetable = load_timetable(vault_dir)
        train = timetable.get(normalized)
        if not train:
            return {
                "found": False, "code": normalized, "timetable_exists": _timetable_path(vault_dir).is_file(),
                "error": "本地时刻表里还没有这趟车。",
            }
        return {
            "found": True, "code": normalized, "train": train,
            "realtime": train_status(train), "timetable_exists": True,
        }

    @router.post("/train-tracker/timetable")
    async def tracker_import(file: UploadFile = File(...)):
        try:
            ensure_vault_writable(read_only, "导入列车时刻表")
        except VaultReadOnlyError as error:
            raise HTTPException(409, str(error)) from error
        raw = await file.read(MAX_TIMETABLE_BYTES + 1)
        if len(raw) > MAX_TIMETABLE_BYTES:
            raise HTTPException(413, "时刻表超过 5MB 上限")
        try:
            data = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise HTTPException(422, f"时刻表不是有效的 UTF-8 JSON：{error}") from error
        if not isinstance(data, dict) or not data:
            raise HTTPException(422, "时刻表应为 {车次: 信息} 的对象")
        timetable: dict[str, dict[str, Any]] = {}
        for key, value in data.items():
            train = normalize_train(key, value)
            if not train:
                raise HTTPException(422, f"车次 {key} 的结构不正确（需要 stops 经停站）")
            timetable[train["train_code"]] = train
        save_timetable(vault_dir, timetable, read_only=read_only)
        return {"success": True, "count": len(timetable), "codes": sorted(timetable),
                "message": f"时刻表已更新，共 {len(timetable)} 趟车。"}

    @router.post("/train-tracker/sync")
    def tracker_sync(payload: TrainSyncRequest):
        if not sync_enabled:
            raise HTTPException(409, "当前运行模式不启用 12306 同步")
        try:
            ensure_vault_writable(read_only, "同步列车时刻表")
        except VaultReadOnlyError as error:
            raise HTTPException(409, str(error)) from error
        code = _normalize_code(payload.code)
        if not valid_train_code(code):
            raise HTTPException(422, "车次号格式不正确")
        date_text = payload.date or _today_text()
        if not DATE_PATTERN.fullmatch(date_text):
            raise HTTPException(422, "日期格式应为 YYYY-MM-DD")
        timetable = load_timetable(vault_dir)
        local = timetable.get(code) or {}
        from_station = payload.from_station or str(local.get("from_station") or "")
        to_station = payload.to_station or str(local.get("to_station") or "")
        try:
            raw_train = fetch(code, from_station, to_station, date_text)
        except TrainSyncError as error:
            raise HTTPException(422, str(error)) from error
        train = normalize_train(code, raw_train)
        if not train:
            raise HTTPException(422, "12306 没有返回可用的车次信息")
        timetable[code] = train
        save_timetable(vault_dir, timetable, read_only=read_only)
        return {"success": True, "code": code, "train": train,
                "message": f"{code} 已从 12306 同步。"}

    return router
