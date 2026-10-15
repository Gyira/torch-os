"""Local, offline BaZi (four pillars) reference chart.

Traditional-culture reference only: the chart is computed from the user's own
birth data on this machine, never sent to a model, and never turned into a
memory automatically. Birth data can be cleared at any time.
"""
from __future__ import annotations

import math
import importlib.util
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from .db import Database, utc_now

SHANGHAI = ZoneInfo('Asia/Shanghai')
ELEMENTS = ('金', '木', '水', '火', '土')
STANDARD_MERIDIAN = 120.0
MISSING_DEPENDENCY = '缺少排盘依赖 lunar-python：请在工程目录运行 scripts\\setup.ps1，或执行 pip install lunar-python==1.4.8 后重启本地服务。'
SCHEMA = """
CREATE TABLE IF NOT EXISTS bazi_profiles (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    birth_date TEXT NOT NULL CHECK(length(birth_date) = 10),
    birth_time TEXT NOT NULL CHECK(length(birth_time) = 5),
    longitude REAL CHECK(longitude IS NULL OR (longitude >= -180 AND longitude <= 180)),
    use_true_solar INTEGER NOT NULL DEFAULT 0 CHECK(use_true_solar IN (0, 1)),
    updated_at TEXT NOT NULL
);
"""


def shanghai_today() -> date:
    return datetime.now(SHANGHAI).date()


class BaziProfileWrite(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

    birth_date: str
    birth_time: str
    longitude: float | None = Field(default=None, ge=-180, le=180)
    use_true_solar: StrictBool = False

    @field_validator('birth_date')
    @classmethod
    def valid_birth_date(cls, value: str) -> str:
        try:
            parsed = date.fromisoformat(value)
        except (TypeError, ValueError) as exc:
            raise ValueError('出生日期必须是 YYYY-MM-DD') from exc
        if parsed > shanghai_today():
            raise ValueError('出生日期不能在未来')
        if parsed.year < 1900 or parsed.year > 2100:
            raise ValueError('出生年份需要在 1900–2100 之间')
        return parsed.isoformat()

    @field_validator('birth_time')
    @classmethod
    def valid_birth_time(cls, value: str) -> str:
        if not isinstance(value, str) or len(value) != 5 or value[2] != ':':
            raise ValueError('出生时间必须是 HH:MM')
        hour, minute = value[:2], value[3:]
        if not hour.isdigit() or not minute.isdigit():
            raise ValueError('出生时间必须是 HH:MM')
        if not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
            raise ValueError('出生时间必须是 00:00–23:59')
        return f'{int(hour):02d}:{int(minute):02d}'

    @model_validator(mode='after')
    def true_solar_needs_longitude(self):
        if self.use_true_solar and self.longitude is None:
            raise ValueError('使用真太阳时需要填写出生地经度；不知道可以留空并按北京时间排盘')
        return self


def equation_of_time_minutes(day: date) -> float:
    """NOAA-style approximation of the equation of time (minutes, ±~16)."""
    b = 2 * math.pi * (day.timetuple().tm_yday - 81) / 364.0
    return 9.87 * math.sin(2 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)


def true_solar_time(base: datetime, longitude: float) -> tuple[datetime, float, float]:
    longitude_minutes = (longitude - STANDARD_MERIDIAN) * 4.0
    eot = equation_of_time_minutes(base.date())
    return base + timedelta(minutes=longitude_minutes + eot), longitude_minutes, eot


def dependency_available() -> bool:
    """The calendar library is optional at import time so the app still boots."""
    return importlib.util.find_spec('lunar_python') is not None


def build_chart(profile: dict[str, Any]) -> dict[str, Any]:
    """Compute the four pillars for one saved profile. Pure local calculation."""
    try:
        from lunar_python import Solar
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(MISSING_DEPENDENCY) from exc

    birth_date = date.fromisoformat(profile['birth_date'])
    hour, minute = (int(part) for part in profile['birth_time'].split(':'))
    base = datetime(birth_date.year, birth_date.month, birth_date.day, hour, minute)
    solar_info: dict[str, Any] | None = None
    chart_time = base
    if profile.get('use_true_solar') and profile.get('longitude') is not None:
        chart_time, longitude_minutes, eot = true_solar_time(base, float(profile['longitude']))
        solar_info = {
            'applied': True,
            'longitude': float(profile['longitude']),
            'longitude_minutes': round(longitude_minutes, 2),
            'equation_of_time_minutes': round(eot, 2),
            'total_minutes': round(longitude_minutes + eot, 2),
            'corrected_time': chart_time.isoformat(timespec='minutes'),
            'note': '真太阳时 = 北京时间 + 经度时差 + 均时差（NOAA 近似公式）。',
        }
    else:
        solar_info = {
            'applied': False,
            'note': '未填写经度，按北京时间（东八区标准时）排盘；填写出生地经度后可改用真太阳时。',
        }

    lunar = Solar.fromYmdHms(chart_time.year, chart_time.month, chart_time.day,
                             chart_time.hour, chart_time.minute, 0).getLunar()
    eight_char = lunar.getEightChar()
    # Convention: 晚子时（23:00–24:00）日柱仍按当天（sect=2），时柱按次日干起子时。
    eight_char.setSect(2)
    pillars = [
        {'name': '年柱', 'ganzhi': eight_char.getYear(), 'gan': eight_char.getYearGan(),
         'zhi': eight_char.getYearZhi(), 'wu_xing': eight_char.getYearWuXing(),
         'na_yin': eight_char.getYearNaYin(), 'shi_shen_gan': eight_char.getYearShiShenGan(),
         'shi_shen_zhi': list(eight_char.getYearShiShenZhi())},
        {'name': '月柱', 'ganzhi': eight_char.getMonth(), 'gan': eight_char.getMonthGan(),
         'zhi': eight_char.getMonthZhi(), 'wu_xing': eight_char.getMonthWuXing(),
         'na_yin': eight_char.getMonthNaYin(), 'shi_shen_gan': eight_char.getMonthShiShenGan(),
         'shi_shen_zhi': list(eight_char.getMonthShiShenZhi())},
        {'name': '日柱', 'ganzhi': eight_char.getDay(), 'gan': eight_char.getDayGan(),
         'zhi': eight_char.getDayZhi(), 'wu_xing': eight_char.getDayWuXing(),
         'na_yin': eight_char.getDayNaYin(), 'shi_shen_gan': eight_char.getDayShiShenGan(),
         'shi_shen_zhi': list(eight_char.getDayShiShenZhi())},
        {'name': '时柱', 'ganzhi': eight_char.getTime(), 'gan': eight_char.getTimeGan(),
         'zhi': eight_char.getTimeZhi(), 'wu_xing': eight_char.getTimeWuXing(),
         'na_yin': eight_char.getTimeNaYin(), 'shi_shen_gan': eight_char.getTimeShiShenGan(),
         'shi_shen_zhi': list(eight_char.getTimeShiShenZhi())},
    ]
    counts = {element: 0 for element in ELEMENTS}
    for pillar in pillars:
        for element in pillar['wu_xing']:
            if element in counts:
                counts[element] += 1
    return {
        'schema': 1,
        'generated_at': datetime.now(SHANGHAI).isoformat(timespec='seconds'),
        'birth': {
            'date': profile['birth_date'], 'time': profile['birth_time'],
            'longitude': profile.get('longitude'), 'use_true_solar': bool(profile.get('use_true_solar')),
        },
        'chart_time': chart_time.isoformat(timespec='minutes'),
        'true_solar': solar_info,
        'lunar': {
            'text': lunar.toString(),
            'year': lunar.getYear(),
            'month': lunar.getMonth(),
            'day': lunar.getDay(),
            'zodiac': lunar.getYearShengXiao(),
        },
        'pillars': pillars,
        'day_master': eight_char.getDayGan(),
        'five_elements': counts,
        'five_elements_note': '按四柱八个字的五行属性计数（天干与地支本气），只作结构展示，不判断好坏或吉凶。',
        'notes': [
            '传统文化参考：用于自我反思与记录，不作为命运、健康、财务或人生重大决定的依据。',
            '年柱以立春为界；月柱按节气划分；晚子时（23:00–24:00）日柱按当天。',
            '全部计算在本机离线完成，不会把出生资料发送到任何模型或网络服务；可随时清除。',
        ],
        'extraction_version': 'bazi-v1',
    }


class BaziService:
    def __init__(self, database: Database):
        self.database = database
        self.database.initialize()
        with self.database.connect() as connection:
            connection.executescript(SCHEMA)

    def get_profile(self) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute('SELECT * FROM bazi_profiles WHERE id=1').fetchone()
        return dict(row) if row else None

    def save_profile(self, payload: BaziProfileWrite) -> dict[str, Any]:
        values = payload.model_dump()
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO bazi_profiles(id,birth_date,birth_time,longitude,use_true_solar,updated_at)
                VALUES (1,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET birth_date=excluded.birth_date,birth_time=excluded.birth_time,
                longitude=excluded.longitude,use_true_solar=excluded.use_true_solar,updated_at=excluded.updated_at""",
                (values['birth_date'], values['birth_time'], values['longitude'],
                 1 if values['use_true_solar'] else 0, utc_now()),
            )
        return self.get_profile() or {}

    def clear_profile(self) -> bool:
        with self.database.connect() as connection:
            cursor = connection.execute('DELETE FROM bazi_profiles WHERE id=1')
        return cursor.rowcount > 0


class ClearRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirm: StrictBool = False

    @model_validator(mode='after')
    def require_confirmation(self):
        if self.confirm is not True:
            raise ValueError('请明确确认清除出生资料')
        return self


def create_bazi_router(database: Database, prefix: str = '/api') -> APIRouter:
    service = BaziService(database)
    router = APIRouter(prefix=prefix, tags=['bazi'])

    def profile_payload(profile: dict[str, Any] | None) -> dict[str, Any]:
        if profile is None:
            return {'profile': None, 'chart': None}
        try:
            chart = build_chart(profile)
        except RuntimeError as error:
            raise HTTPException(503, str(error)) from error
        return {'profile': profile, 'chart': chart}

    @router.get('/bazi/profile')
    def get_profile():
        return profile_payload(service.get_profile())

    @router.put('/bazi/profile')
    def save_profile(payload: BaziProfileWrite):
        profile = service.save_profile(payload)
        # Audit counts only: never birth date, time or longitude values.
        database.audit('bazi_profile_saved', {'has_longitude': profile.get('longitude') is not None})
        return profile_payload(profile)

    @router.get('/bazi/chart')
    def get_chart():
        profile = service.get_profile()
        if profile is None:
            raise HTTPException(404, '还没有保存出生资料；填写并保存后即可排盘')
        try:
            return build_chart(profile)
        except RuntimeError as error:
            raise HTTPException(503, str(error)) from error

    @router.post('/bazi/profile/clear')
    def clear_profile(payload: ClearRequest):
        if payload.confirm is not True:
            raise HTTPException(422, '请明确确认清除出生资料')
        removed = service.clear_profile()
        if removed:
            database.audit('bazi_profile_cleared', {})
        return {'cleared': removed}

    return router
