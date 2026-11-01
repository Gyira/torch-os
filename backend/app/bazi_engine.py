"""八字排盘引擎（2026-11-01）：恋爱合婚与个人命盘共用。

只做本地计算：四柱、十神、藏干、五行、大运。
AI 只负责解读；不得修改这里的四柱/十神/藏干/大运。
"""
from __future__ import annotations

import re
from typing import Any

SLOTS = ("年柱", "月柱", "日柱", "时柱")
GAN = "甲乙丙丁戊己庚辛壬癸"
ZHI = "子丑寅卯辰巳午未申酉戌亥"
GAN_ELEM = {
    "甲": "木", "乙": "木", "丙": "火", "丁": "火", "戊": "土",
    "己": "土", "庚": "金", "辛": "金", "壬": "水", "癸": "水",
}
ZHI_ELEM = {
    "子": "水", "丑": "土", "寅": "木", "卯": "木", "辰": "土", "巳": "火",
    "午": "火", "未": "土", "申": "金", "酉": "金", "戌": "土", "亥": "水",
}
GAN_YANG = {
    "甲": True, "乙": False, "丙": True, "丁": False, "戊": True,
    "己": False, "庚": True, "辛": False, "壬": True, "癸": False,
}
ZHI_HIDDEN = {
    "子": ("癸",), "丑": ("己", "癸", "辛"), "寅": ("甲", "丙", "戊"),
    "卯": ("乙",), "辰": ("戊", "乙", "癸"), "巳": ("丙", "戊", "庚"),
    "午": ("丁", "己"), "未": ("己", "丁", "乙"), "申": ("庚", "壬", "戊"),
    "酉": ("辛",), "戌": ("戊", "辛", "丁"), "亥": ("壬", "甲"),
}
GEN = {"木": "火", "火": "土", "土": "金", "金": "水", "水": "木"}
KE = {"木": "土", "火": "金", "土": "水", "金": "木", "水": "火"}
TEN_GODS = ("比肩", "劫财", "食神", "伤官", "偏财", "正财",
            "七杀", "正官", "偏印", "正印")
WUXING = ("木", "火", "土", "金", "水")


def ten_god(day_gan: str, other_gan: str) -> str:
    if day_gan not in GAN_ELEM or other_gan not in GAN_ELEM:
        return ""
    day_element = GAN_ELEM[day_gan]
    other_element = GAN_ELEM[other_gan]
    same_polarity = GAN_YANG[day_gan] == GAN_YANG[other_gan]
    if day_element == other_element:
        return "比肩" if same_polarity else "劫财"
    if GEN[day_element] == other_element:
        return "食神" if same_polarity else "伤官"
    if KE[day_element] == other_element:
        return "偏财" if same_polarity else "正财"
    if KE[other_element] == day_element:
        return "七杀" if same_polarity else "正官"
    if GEN[other_element] == day_element:
        return "偏印" if same_polarity else "正印"
    return ""


def pillar(slot: str, gan: str, zhi: str, day_gan: str) -> dict[str, Any]:
    hidden = ZHI_HIDDEN.get(zhi, ())
    return {
        "slot": slot,
        "gan": gan,
        "zhi": zhi,
        "gan_wx": GAN_ELEM.get(gan, ""),
        "zhi_wx": ZHI_ELEM.get(zhi, ""),
        "gan_ss": ten_god(day_gan, gan),
        "zhi_ss": "·".join(ten_god(day_gan, item) for item in hidden),
        "hidden": "/".join(hidden),
    }


def parse_pillar(value: Any) -> tuple[str, str] | None:
    text = str(value or "").strip()
    if len(text) == 2 and text[0] in GAN and text[1] in ZHI:
        return text[0], text[1]
    return None


def default_pillars() -> dict[str, str]:
    return {"year": "", "month": "", "day": "", "hour": ""}


def normalize_pillars(raw: Any) -> dict[str, str]:
    result = default_pillars()
    if isinstance(raw, dict):
        labels = {"year": "年柱", "month": "月柱", "day": "日柱", "hour": "时柱"}
        for key in result:
            value = raw.get(key) or raw.get(labels[key])
            result[key] = str(value or "").strip()
    elif isinstance(raw, str):
        parts = [part for part in re.split(r"[\s,，、]+", raw.strip()) if part]
        if len(parts) == 1 and len(parts[0]) == 8:
            parts = [parts[0][0:2], parts[0][2:4], parts[0][4:6], parts[0][6:8]]
        for index, key in enumerate(result):
            if index < len(parts):
                result[key] = str(parts[index] or "").strip()
    elif isinstance(raw, (list, tuple)):
        for index, key in enumerate(result):
            if index < len(raw):
                result[key] = str(raw[index] or "").strip()
    return result


def normalize_sex(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in ("male", "m", "男", "男命"):
        return "male"
    if text in ("female", "f", "女", "女命"):
        return "female"
    return ""


def _pillars_from_lunar(birth_date: str, birth_time: str) -> tuple[tuple[str, str], ...]:
    from lunar_python import Solar

    year_number, month_number, day_number = (int(part) for part in birth_date.split("-"))
    hour_number, minute_number = (int(part) for part in birth_time.split(":"))
    eight = Solar.fromYmdHms(
        year_number, month_number, day_number, hour_number, minute_number, 0
    ).getLunar().getEightChar()
    return (
        (eight.getYearGan(), eight.getYearZhi()),
        (eight.getMonthGan(), eight.getMonthZhi()),
        (eight.getDayGan(), eight.getDayZhi()),
        (eight.getTimeGan(), eight.getTimeZhi()),
    )


def chart_from_birth(*, name: str, role: str = "", sex: str = "",
                     birth_date: str = "", birth_time: str = "",
                     place: str = "", pillars: Any = None) -> tuple[dict[str, Any], str]:
    """返回 (chart, time_note)。四柱手动优先；缺失时用 lunar_python 排盘。"""
    manual = normalize_pillars(pillars)
    birth_date = str(birth_date or "").strip()
    raw_birth_time = str(birth_time or "").strip()
    birth_time = raw_birth_time or "12:00"
    manual_complete = all(manual.values())
    time_note = ""
    prefix = f"{role} " if role else ""
    if not raw_birth_time and not manual_complete:
        time_note = "出生时间未填，按 12:00（午时）排盘；时柱仅供参考。"
    if manual_complete:
        parsed = {key: parse_pillar(value) for key, value in manual.items()}
        if any(item is None for item in parsed.values()):
            raise ValueError(f"{prefix}的四柱格式应为「甲子」这样的两字干支")
        year, month, day, hour = (parsed[key] for key in ("year", "month", "day", "hour"))
    else:
        if not birth_date:
            raise ValueError(f"{prefix}需要填写出生日期，或直接填写完整四柱")
        try:
            year, month, day, hour = _pillars_from_lunar(birth_date, birth_time)
        except Exception as error:  # noqa: BLE001
            raise ValueError(f"{prefix}的出生日期或时间无法排盘：{error}") from error
    day_gan = day[0]
    pillars_rows = [
        pillar(SLOTS[0], year[0], year[1], day_gan),
        pillar(SLOTS[1], month[0], month[1], day_gan),
        pillar(SLOTS[2], day[0], day[1], day_gan),
        pillar(SLOTS[3], hour[0], hour[1], day_gan),
    ]
    dayun = ""
    if birth_date and (not manual_complete or raw_birth_time):
        try:
            from lunar_python import Solar

            year_number, month_number, day_number = (int(part) for part in birth_date.split("-"))
            hour_number, minute_number = (int(part) for part in (birth_time or "12:00").split(":"))
            eight = Solar.fromYmdHms(
                year_number, month_number, day_number, hour_number, minute_number, 0
            ).getLunar().getEightChar()
            yun = eight.getYun(1 if normalize_sex(sex) == "male" else 0)
            start = f"起运 {yun.getStartYear()} 年 {yun.getStartMonth()} 个月"
            cycles = []
            for item in yun.getDaYun()[1:7]:
                if item.getGanZhi():
                    cycles.append(f"{item.getGanZhi()} {item.getStartYear()}-{item.getEndYear()}")
            dayun = start + ("；" + "；".join(cycles) if cycles else "")
        except Exception:
            dayun = ""
    birth = " ".join(item for item in [birth_date, raw_birth_time] if item)
    chart = {
        "name": str(name or "").strip(),
        "sex": normalize_sex(sex),
        "birth": birth,
        "place": str(place or "").strip(),
        "day_master": f"{day_gan}{GAN_ELEM.get(day_gan, '')}",
        "pillars": pillars_rows,
        "pattern": "",
        "favor": [],
        "avoid": [],
        "dayun": dayun,
        "time_note": time_note,
    }
    return chart, time_note


def element_counts(chart: dict[str, Any]) -> dict[str, int]:
    """按天干 + 地支本气粗算五行数量，仅供页面展示，不做命理定论。"""
    counts = {item: 0 for item in WUXING}
    for row in chart.get("pillars") or []:
        if not isinstance(row, dict):
            continue
        gan_element = str(row.get("gan_wx") or "")
        zhi_element = str(row.get("zhi_wx") or "")
        if gan_element in counts:
            counts[gan_element] += 1
        if zhi_element in counts:
            counts[zhi_element] += 1
    return counts


def dayun_cycles(chart: dict[str, Any]) -> list[dict[str, str]]:
    """把「起运 …；甲子 2024-2034；…」拆成列表。"""
    text = str(chart.get("dayun") or "")
    if "；" not in text:
        return []
    rows: list[dict[str, str]] = []
    for part in text.split("；")[1:]:
        bits = str(part or "").strip().split()
        if len(bits) >= 2:
            rows.append({"gz": bits[0], "years": " ".join(bits[1:])})
    return rows


__all__ = [
    "SLOTS", "GAN", "ZHI", "GAN_ELEM", "ZHI_ELEM", "GAN_YANG", "ZHI_HIDDEN",
    "GEN", "KE", "TEN_GODS", "WUXING",
    "ten_god", "pillar", "parse_pillar", "default_pillars", "normalize_pillars",
    "normalize_sex", "chart_from_birth", "element_counts", "dayun_cycles",
]
