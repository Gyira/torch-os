"""恋爱系统 · 查看排班表（原生实现）。

原项目把交路表 Excel、车次时刻表和天气 API 拼成一块「今天排班在哪」的看板。
这里不启动旧的 Electron/FastAPI，也不做 12306 抓取；直接读本机交路表与时刻表，
天气走薪火OS 已允许的出站域名（Open-Meteo），查不到就安静地留空。
排班对象姓名只从本机配置读取（LOVE_TARGET_NAME），不写进仓库，也不在接口里回显。
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse

TARGET_SHEET_HINTS = ("高1", "14")
MAX_EXCEL_BYTES = 20 * 1024 * 1024
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"

STATION_TO_CITY: dict[str, str] = {
    "南昌西": "Nanchang", "南昌": "Nanchang",
    "庐山": "Jiujiang", "九江": "Jiujiang",
    "武汉": "Wuhan",
    "郑州东": "Zhengzhou", "郑州": "Zhengzhou",
    "邯郸东": "Handan", "邯郸": "Handan",
    "石家庄": "Shijiazhuang",
    "北京西": "Beijing", "北京": "Beijing", "北京大兴": "Beijing", "大兴机场": "Beijing",
    "雄安": "Baoding",
    "固安东": "Langfang", "廊坊": "Langfang",
    "黄石北": "Huangshi", "黄石": "Huangshi",
}

CITY_COORDS: dict[str, tuple[float, float, str]] = {
    "Nanchang": (28.68, 115.86, "南昌"),
    "Jiujiang": (29.71, 116.00, "九江"),
    "Wuhan": (30.59, 114.31, "武汉"),
    "Zhengzhou": (34.75, 113.63, "郑州"),
    "Handan": (36.63, 114.54, "邯郸"),
    "Shijiazhuang": (38.04, 114.51, "石家庄"),
    "Beijing": (39.90, 116.41, "北京"),
    "Baoding": (38.87, 115.46, "保定"),
    "Langfang": (39.54, 116.68, "廊坊"),
    "Huangshi": (30.20, 115.04, "黄石"),
}

WMO = {
    0: "晴", 1: "基本晴", 2: "多云", 3: "阴", 45: "雾", 48: "雾凇",
    51: "毛毛雨", 53: "毛毛雨", 55: "毛毛雨", 56: "冻毛毛雨", 57: "冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "霰",
    80: "阵雨", 81: "阵雨", 82: "强阵雨", 85: "阵雪", 86: "阵雪",
    95: "雷阵雨", 96: "雷阵雨夹冰雹", 99: "雷阵雨夹冰雹",
}

WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _find_latest_excel(base_dir: Path) -> Path | None:
    base_dir = Path(base_dir)
    if not base_dir.is_dir():
        return None
    files = [path for path in base_dir.glob("*.xlsx") if path.is_file()]
    if not files:
        return None
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    for path in files:
        if "交路" in path.name:
            return path
    return files[0]


def _hm_to_minutes(value: Any) -> int | None:
    try:
        hour, minute = str(value).strip().split(":")
        return int(hour) * 60 + int(minute)
    except (ValueError, AttributeError):
        return None


def _now_minutes() -> int:
    now = datetime.now()
    return now.hour * 60 + now.minute


def _parse_train_list(cell_value: Any) -> list[str]:
    if cell_value is None:
        return []
    text = str(cell_value).strip()
    if not text or text in ("休", "公休") or text.startswith("段抽"):
        return []
    result: list[str] = []
    for part in re.split(r"[-/]", text):
        part = part.strip()
        if re.match(r"^[GCD]\d+$", part):
            result.append(part)
    return result


def _is_rest_cell(cell_value: Any) -> bool:
    if cell_value is None:
        return True
    text = str(cell_value).strip()
    return not text or text in ("休", "公休") or text.startswith("段抽")


def _load_timetable(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


class ScheduleWorkbook:
    """交路表读取：日期列按「日号递推」而不是直接相信序列号（沿用原项目踩坑后的算法）。"""

    def __init__(self, path: Path | None, target_name: str = ""):
        self.path = Path(path) if path else None
        self.target_name = str(target_name or "").strip()
        self.workbook = None
        self.sheet = None
        self.date_columns: dict[date, str] = {}
        if self.path:
            self._load()

    def _load(self) -> None:
        import openpyxl

        self.workbook = openpyxl.load_workbook(str(self.path), data_only=True)
        for name in self.workbook.sheetnames:
            if all(hint in name for hint in TARGET_SHEET_HINTS):
                self.sheet = self.workbook[name]
                break
        else:
            self.sheet = self.workbook.worksheets[0]
        self._build_dates()

    def _build_dates(self) -> None:
        import calendar

        import openpyxl

        if self.sheet is None:
            return
        rows: list[tuple[int, int, int]] = []
        for column in range(10, 48):
            day_number = self.sheet.cell(row=7, column=column).value
            serial = self.sheet.cell(row=8, column=column).value
            if day_number is None or serial is None:
                continue
            try:
                rows.append((column, int(day_number), int(serial)))
            except (TypeError, ValueError):
                continue
        if not rows:
            return
        anchor = next(((col, day, serial) for col, day, serial in rows if day == 1), None)
        if anchor is None:
            for col, _day, serial in rows:
                try:
                    self.date_columns[_excel_serial_date(serial)] = openpyxl.utils.get_column_letter(col)
                except (TypeError, ValueError):
                    continue
            return
        anchor_col, _anchor_day, anchor_serial = anchor
        try:
            anchor_date = _excel_serial_date(anchor_serial)
        except (TypeError, ValueError):
            return
        previous_month = anchor_date.month - 1
        previous_year = anchor_date.year
        if previous_month == 0:
            previous_month = 12
            previous_year -= 1
        days_in_previous = calendar.monthrange(previous_year, previous_month)[1]
        current_month_start = anchor_date - timedelta(days=days_in_previous)
        previous_day = 0
        for col, day_number, _serial in rows:
            if previous_day > 0 and day_number < previous_day:
                days_in_month = calendar.monthrange(
                    current_month_start.year, current_month_start.month
                )[1]
                current_month_start = current_month_start + timedelta(days=days_in_month)
            previous_day = day_number
            self.date_columns[current_month_start + timedelta(days=day_number - 1)] = (
                openpyxl.utils.get_column_letter(col)
            )

    def person_rows(self) -> list[tuple[int, str]]:
        """交路表 E 列里的姓名行；只在本机内存里用于定位，不回给前端。"""
        if self.sheet is None:
            return []
        rows: list[tuple[int, str]] = []
        for row in range(7, self.sheet.max_row + 1):
            value = self.sheet.cell(row=row, column=5).value
            name = str(value).strip() if value is not None else ""
            if name:
                rows.append((row, name))
        return rows

    def find_person_row(self) -> int | None:
        rows = self.person_rows()
        if not rows:
            return None
        if self.target_name:
            for row, name in rows:
                if name == self.target_name:
                    return row
            return None
        # 没配置姓名时只在恰好只有一个姓名时自动识别，避免猜错排班对象。
        return rows[0][0] if len(rows) == 1 else None

    def date_column(self, target: date) -> tuple[str | None, str]:
        if target in self.date_columns:
            return self.date_columns[target], ""
        # 交路表只覆盖某半个多月时，同日回退应取**最新**的那份月份，
        # 而不是 dict 里先插入的月份（8 月表常同时含 7 月末，取早的会错回 7 月）。
        matches = [(day, column) for day, column in self.date_columns.items()
                   if day.day == target.day]
        if matches:
            day, column = max(matches, key=lambda item: item[0])
            return column, f"（当前交路表为 {day.strftime('%m月')} 份，显示同日交路供参考）"
        return None, ""

    def date_range(self) -> tuple[date, date] | None:
        if not self.date_columns:
            return None
        dates = sorted(self.date_columns)
        return dates[0], dates[-1]

    def group_info(self, row: int) -> str:
        if self.sheet is None:
            return ""
        for candidate in range(row, 6, -1):
            value = self.sheet.cell(row=candidate, column=3).value
            if value and "高" in str(value) and "组" in str(value):
                return str(value).strip().replace("\n", "")
        return ""

    def cell_for(self, row: int, column_letter: str) -> Any:
        import openpyxl

        return self.sheet.cell(row=row, column=openpyxl.utils.column_index_from_string(column_letter)).value


def _excel_serial_date(serial: int) -> date:
    return date(1899, 12, 30) + timedelta(days=int(serial))


def _train_details(codes: list[str], timetable: dict[str, Any]) -> tuple[list[dict[str, Any]], set[str]]:
    details: list[dict[str, Any]] = []
    cities: set[str] = set()
    for code in codes:
        train = timetable.get(code.upper())
        if not isinstance(train, dict):
            details.append({
                "train_code": code, "from_station": "未知", "to_station": "未知",
                "depart_time": "未知", "arrive_time": "未知",
                "duration_minutes": 0, "stops": [],
            })
            continue
        stops = []
        for stop in train.get("stops") or []:
            station = str(stop.get("station") or "")
            city = STATION_TO_CITY.get(station)
            stops.append({**stop, "city_en": city or ""})
            if city:
                cities.add(city)
        details.append({
            "train_code": train.get("train_code") or code,
            "from_station": train.get("from_station") or "未知",
            "to_station": train.get("to_station") or "未知",
            "depart_time": train.get("depart_time") or "未知",
            "arrive_time": train.get("arrive_time") or "未知",
            "duration_minutes": train.get("duration_minutes") or 0,
            "stops": stops,
        })
    return details, cities


def _realtime(trains: list[dict[str, Any]]) -> dict[str, Any]:
    now = _now_minutes()
    if not trains:
        return {
            "status": "rest", "status_text": "今日休息 💤", "current_train": None,
            "current_station": None, "next_station": None, "progress_pct": 0,
            "message": "今天没有值乘任务，好好休息吧~",
        }
    stops: list[dict[str, Any]] = []
    for train_index, train in enumerate(trains):
        for stop_index, stop in enumerate(train.get("stops") or []):
            stops.append({
                "train_idx": train_index, "stop_idx": stop_index,
                "station": stop.get("station") or "",
                "arrive": _hm_to_minutes(stop.get("arrive")),
                "depart": _hm_to_minutes(stop.get("depart")),
                "first": stop_index == 0,
                "last": stop_index == len(train.get("stops") or []) - 1,
            })
    if not stops:
        codes = "、".join(train.get("train_code") or "?" for train in trains)
        return {
            "status": "unknown", "status_text": "时刻表待更新",
            "current_train": None, "current_station": None, "next_station": None,
            "progress_pct": 0,
            "message": f"交路表显示今天有 {len(trains)} 趟车（{codes}），"
                       "但本机时刻表里没有这些车次的经停信息。更新 train_timetable.json 后实时位置才会显示。",
        }
    first_depart = stops[0]["depart"]
    last_arrive = stops[-1]["arrive"] or ((first_depart or now) + 60)
    if first_depart is None:
        first_depart = now
    span = max(last_arrive - first_depart, 1)
    if now < first_depart - 30:
        first = trains[0]
        return {
            "status": "before_depart", "status_text": "待出发 🌅",
            "current_train": first,
            "current_station": (first.get("stops") or [{}])[0].get("station"),
            "next_station": (first.get("stops") or [{}] * 2)[1].get("station")
            if len(first.get("stops") or []) > 1 else None,
            "next_depart_time": first.get("depart_time"),
            "countdown_minutes": first_depart - now,
            "progress_pct": 0,
            "message": f"距首发 {first.get('train_code')} 发车还有 {first_depart - now} 分钟",
        }
    if now > last_arrive + 30:
        return {
            "status": "finished", "status_text": "已收车 🌙",
            "current_train": None,
            "current_station": (stops[-1]["station"] if stops else None),
            "next_station": None, "progress_pct": 100,
            "message": "今日值乘任务已全部完成，辛苦了~",
        }
    for index in range(len(trains) - 1):
        previous_arrive = _hm_to_minutes(trains[index].get("arrive_time"))
        next_depart = _hm_to_minutes(trains[index + 1].get("depart_time"))
        if previous_arrive is not None and next_depart is not None and previous_arrive <= now <= next_depart:
            return {
                "status": "laying_over", "status_text": "换乘休息 ☕",
                "current_train": None,
                "current_station": trains[index].get("to_station"),
                "next_train": trains[index + 1].get("train_code"),
                "next_depart_time": trains[index + 1].get("depart_time"),
                "countdown_minutes": next_depart - now,
                "progress_pct": round((now - first_depart) / span * 100, 1),
                "message": f"在 {trains[index].get('to_station')} 休息中，下趟 "
                           f"{trains[index + 1].get('train_code')} {trains[index + 1].get('depart_time')} 发车",
            }
    for index, stop in enumerate(stops):
        if stop["first"] or stop["last"]:
            continue
        if stop["arrive"] is not None and stop["depart"] is not None and stop["arrive"] <= now <= stop["depart"]:
            train = trains[stop["train_idx"]]
            return {
                "status": "at_station", "status_text": "停站中 🚉",
                "current_train": train, "current_station": stop["station"],
                "next_station": stops[index + 1]["station"] if index + 1 < len(stops) else None,
                "countdown_minutes": stop["depart"] - now,
                "progress_pct": round((now - first_depart) / span * 100, 1),
                "message": f"{train.get('train_code')} 次停靠 {stop['station']}，还有 "
                           f"{stop['depart'] - now} 分钟发车",
            }
    for index in range(1, len(stops)):
        target = stops[index]
        arrive = target["arrive"]
        if arrive is None or now > arrive:
            continue
        for previous_index in range(index - 1, -1, -1):
            previous = stops[previous_index]
            if previous["depart"] is not None and now >= previous["depart"]:
                train = trains[target["train_idx"]]
                segment = arrive - previous["depart"]
                segment_pct = round((now - previous["depart"]) / segment * 100, 1) if segment > 0 else 50
                return {
                    "status": "running", "status_text": "运行中 🚄",
                    "current_train": train, "current_station": previous["station"],
                    "next_station": target["station"], "next_arrive_time": target["arrive"],
                    "countdown_minutes": arrive - now,
                    "progress_pct": round((now - first_depart) / span * 100, 1),
                    "segment_progress_pct": segment_pct,
                    "message": f"{train.get('train_code')} 次，{previous['station']} → "
                               f"{target['station']}，预计 {arrive // 60:02d}:{arrive % 60:02d} 到达",
                }
            break
    return {
        "status": "unknown", "status_text": "运行中", "current_train": None,
        "current_station": None, "next_station": None, "progress_pct": 50,
        "message": "正在获取位置信息...",
    }


def _weather(cities: set[str]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}

    def fetch(city: str) -> tuple[str, dict[str, Any]]:
        coords = CITY_COORDS.get(city)
        if not coords:
            return city, {}
        lat, lon, _name = coords
        try:
            with httpx.Client(timeout=httpx.Timeout(5.0), trust_env=False) as client:
                response = client.get(WEATHER_URL, params={
                    "latitude": lat, "longitude": lon,
                    "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code",
                    "daily": "temperature_2m_max,temperature_2m_min",
                    "timezone": "Asia/Shanghai", "forecast_days": 1,
                })
            if response.status_code != 200 or len(response.content) > 256_000:
                return city, {}
            data = response.json()
            current = data.get("current") or {}
            daily = data.get("daily") or {}
            code = current.get("weather_code")
            return city, {
                "city_en": city,
                "city": coords[2],
                "temp_c": current.get("temperature_2m", "--"),
                "feels_like_c": current.get("apparent_temperature", "--"),
                "humidity": current.get("relative_humidity_2m", "--"),
                "weather_desc": WMO.get(int(code), "") if code is not None else "",
                "max_temp_c": (daily.get("temperature_2m_max") or ["--"])[0],
                "min_temp_c": (daily.get("temperature_2m_min") or ["--"])[0],
            }
        except Exception:
            return city, {}

    if not cities:
        return result
    with ThreadPoolExecutor(max_workers=min(len(cities), 8)) as executor:
        futures = {executor.submit(fetch, city): city for city in cities}
        for future in as_completed(futures, timeout=10):
            try:
                city, data = future.result()
                if data:
                    result[city] = data
            except Exception:
                continue
    return result


def _schedule(target: date, excel_dir: Path, timetable_path: Path,
              target_name: str = "") -> dict[str, Any]:
    try:
        latest = _find_latest_excel(excel_dir)
        workbook = ScheduleWorkbook(latest, target_name)
    except Exception as exc:
        return {"found": False, "error": f"交路表读取失败：{exc}"}
    if workbook.sheet is None:
        return {
            "found": False,
            "error": f"找不到交路表，请把 .xlsx 放进 {excel_dir}",
        }
    row = workbook.find_person_row()
    if row is None:
        if not workbook.target_name:
            count = len(workbook.person_rows())
            error = ("交路表里有多个姓名，请在本机 .env 设置 LOVE_TARGET_NAME 后重启服务。"
                     if count > 1 else "交路表里没有找到可识别的排班对象姓名。")
        else:
            error = "交路表里没有找到配置的排班对象，请检查 LOVE_TARGET_NAME。"
        return {"found": False, "error": error, "excel_file": latest.name}
    column, fallback_note = workbook.date_column(target)
    if column is None:
        span = workbook.date_range()
        span_text = f"{span[0].strftime('%m/%d')} - {span[1].strftime('%m/%d')}" if span else "未知"
        return {
            "found": False,
            "error": f"{target.strftime('%Y年%m月%d日')} 不在当前交路表日期范围内（{span_text}）",
            "available_date_range": span_text,
            "tip": "请替换为当月最新交路表",
            "excel_file": latest.name,
        }
    cell = workbook.cell_for(row, column)
    codes = _parse_train_list(cell)
    trains, cities = _train_details(codes, _load_timetable(timetable_path))
    # 回退到「同一天、其他月份」的旧交路表时不做实时位置：那不是今天的车。
    is_today = target == date.today() and not fallback_note
    return {
        "found": True,
        "is_rest": _is_rest_cell(cell),
        "date": target.isoformat(),
        "date_display": target.strftime("%Y年%m月%d日"),
        "weekday": WEEKDAYS[target.weekday()],
        "group": workbook.group_info(row),
        "trains": trains,
        "total_segments": len(trains),
        "realtime": _realtime(trains) if is_today else None,
        "weather": _weather(cities),
        "excel_file": latest.name,
        "fallback_note": fallback_note,
    }


def create_schedule_router(excel_dir: Path, timetable_path: Path,
                           target_name: str = "") -> APIRouter:
    excel_dir = Path(excel_dir)
    timetable_path = Path(timetable_path)
    target_name = str(target_name or "").strip()
    router = APIRouter(prefix="/api/love/schedule", tags=["love-schedule"])

    @router.get("/status")
    def status():
        latest = None
        span = None
        error = ""
        try:
            latest = _find_latest_excel(excel_dir)
            workbook = ScheduleWorkbook(latest, target_name)
            span = workbook.date_range()
        except Exception as exc:
            error = f"交路表读取失败：{exc}"
        return {
            "excel_exists": latest is not None,
            "excel_file": latest.name if latest else None,
            "excel_dir": str(excel_dir),
            "date_range": (
                {"start": span[0].isoformat(), "end": span[1].isoformat()} if span else None
            ),
            # 只回是否已配置，姓名本身不出接口。
            "target_configured": bool(target_name),
            "timetable_exists": timetable_path.is_file(),
            "timetable_path": str(timetable_path),
            "timetable_trains": len(_load_timetable(timetable_path)),
            "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "error": error,
        }

    @router.get("/today")
    def today():
        return _schedule(date.today(), excel_dir, timetable_path, target_name)

    @router.get("/tomorrow")
    def tomorrow():
        return _schedule(date.today() + timedelta(days=1), excel_dir, timetable_path, target_name)

    @router.get("/day")
    def day(target_date: str = Query(alias="date", pattern=r"^\d{4}-\d{2}-\d{2}$")):
        try:
            parsed = date.fromisoformat(target_date)
        except ValueError as exc:
            raise HTTPException(422, "日期格式应为 YYYY-MM-DD") from exc
        return _schedule(parsed, excel_dir, timetable_path, target_name)

    @router.get("/train")
    def train(code: str = Query(pattern=r"^[GCD]\d+$")):
        timetable = _load_timetable(timetable_path)
        data = timetable.get(code.upper())
        return {"found": data is not None, "train": data}

    @router.post("/timetable")
    async def replace_timetable(file: UploadFile = File(...)):
        """补/换本机车次时刻表。原项目没有在线抓详细时刻表，这里让用户自己导入。"""
        raw = await file.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise HTTPException(413, "时刻表 JSON 超过 2MB 上限")
        try:
            data = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(422, f"时刻表不是有效的 UTF-8 JSON：{exc}") from exc
        if not isinstance(data, dict) or not data:
            raise HTTPException(422, "时刻表应为 {车次: 信息} 的对象")
        for code, train in data.items():
            if not re.match(r"^[GCD]\d+$", str(code)):
                raise HTTPException(422, f"车次号格式不正确：{code}")
            if not isinstance(train, dict) or not isinstance(train.get("stops", []), list):
                raise HTTPException(422, f"{code} 的信息缺少 stops 数组")
        timetable_path.parent.mkdir(parents=True, exist_ok=True)
        timetable_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {
            "success": True,
            "trains": len(data),
            "codes": sorted(data),
            "message": f"时刻表已更新，共 {len(data)} 趟车。",
        }

    @router.post("/excel")
    async def replace_excel(file: UploadFile = File(...)):
        import openpyxl

        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in (".xlsx", ".xlsm"):
            raise HTTPException(422, "仅支持 .xlsx / .xlsm 格式")
        raw = await file.read(MAX_EXCEL_BYTES + 1)
        if len(raw) > MAX_EXCEL_BYTES:
            raise HTTPException(413, "交路表超过 20MB 上限")
        if not raw:
            raise HTTPException(400, "文件内容为空")
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
                handle.write(raw)
                temp_path = Path(handle.name)
            try:
                workbook = openpyxl.load_workbook(str(temp_path), read_only=True, data_only=True)
            except Exception as exc:
                raise HTTPException(422, f"无法读取 Excel：{exc}") from exc
            sheet_names = list(workbook.sheetnames)
            workbook.close()
            if not any(all(hint in name for hint in TARGET_SHEET_HINTS) for name in sheet_names):
                raise HTTPException(422, "交路表里找不到「高1-14组」工作表")
            safe_name = re.sub(r"[^\w一-鿿.\-()（）]+", "_", Path(file.filename).name)[-120:]
            if not safe_name.lower().endswith(suffix):
                safe_name += suffix
            excel_dir.mkdir(parents=True, exist_ok=True)
            destination = excel_dir / safe_name
            shutil.copyfile(temp_path, destination)
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink(missing_ok=True)
        return {
            "success": True,
            "filename": destination.name,
            "message": f"交路表已更新为 {destination.name}",
            "status": status(),
        }

    return router
