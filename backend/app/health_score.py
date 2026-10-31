"""健康评分、派生值与跨维度洞察（2026-10-31）。

所有公式都是**可解释的简单算式**，不训练模型、不调用 AI：
输入是一段每日记录（``health_store.read_day`` 的形状）与身体档案，
输出是 ``{total, breakdown, insufficient, derived, targets}``。

评分原则（与设计稿一致）：

1. 缺数据的维度**不参与加权**，按剩余维度归一化，并在 ``insufficient`` 里说明，
   绝不当成 0 分；
2. 有数据但表现差，才会得低分（例如一周 0 次训练是事实，不是缺数据）；
3. 评分只做趋势参考，不构成医学判断；异常值另有区间提示。

洞察规则同样保守：每组样本 < 5 或总体 < 7 天就不生成，避免用两三天数据
得出「结论」。每条洞察都带 ``sample_size`` 与日期范围，前端必须显示它。
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

BREAKDOWN_LABELS = {
    "sleep": "睡眠", "nutrition": "营养", "activity": "活动",
    "mental": "心理", "body": "身体", "training": "训练",
}
WEIGHT_KEYS = ("sleep", "nutrition", "activity", "mental", "body", "training")


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _section(day: dict[str, Any], name: str) -> dict[str, Any]:
    value = (day.get("sections") or {}).get(name)
    return value if isinstance(value, dict) else {}


def age_of(profile: dict[str, Any], day: str = "") -> int | None:
    birth = str(profile.get("birth_date") or "")
    if not birth:
        return None
    try:
        born = date.fromisoformat(birth)
        today = date.fromisoformat(day or date.today().isoformat())
    except ValueError:
        return None
    years = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    return years if 0 < years < 120 else None


def bmi(weight_kg: Any, height_cm: Any) -> float | None:
    weight, height = _num(weight_kg), _num(height_cm)
    if not weight or not height or height <= 0:
        return None
    return round(weight / ((height / 100) ** 2), 1)


def bmi_band(value: float | None) -> str:
    if value is None:
        return ""
    if value < 18.5:
        return "偏瘦"
    if value < 24:
        return "正常"
    if value < 28:
        return "超重"
    return "肥胖"


def bmr(profile: dict[str, Any], weight_kg: Any = None, day: str = "") -> int | None:
    """Mifflin-St Jeor；体重缺失时用目标体重或最近体重。"""
    weight = _num(weight_kg) or _num((profile.get("goals") or {}).get("weight_kg"))
    height = _num(profile.get("height_cm"))
    age = age_of(profile, day)
    if not weight or not height or age is None:
        return None
    gender = str(profile.get("gender") or "")
    base = 10 * weight + 6.25 * height - 5 * age
    if gender == "女":
        base -= 161
    elif gender == "男":
        base += 5
    else:
        base -= 78  # 未知性别取男女均值
    return int(round(base))


def tdee(profile: dict[str, Any], weight_kg: Any = None, day: str = "") -> int | None:
    base = bmr(profile, weight_kg, day)
    return int(round(base * 1.4)) if base else None


def targets(profile: dict[str, Any], day: dict[str, Any] | None = None) -> dict[str, Any]:
    goals = profile.get("goals") or {}
    weight = _num((_section(day or {}, "body")).get("weight_kg")) or _num(goals.get("weight_kg"))
    calorie_goal = _num(goals.get("calories")) or tdee(profile, weight)
    protein_goal = _num(goals.get("protein_g")) or (round(weight * 1.6) if weight else None)
    return {
        "sleep_minutes": _num(goals.get("sleep_minutes")) or 450,
        "steps": _num(goals.get("steps")) or 8000,
        "water_ml": _num(goals.get("water_ml")) or 2000,
        "calories": calorie_goal,
        "protein_g": protein_goal,
        "weekly_training": _num(goals.get("weekly_training")) or 4,
        "weight_kg": _num(goals.get("weight_kg")),
        "body_fat_pct": _num(goals.get("body_fat_pct")),
    }


def sleep_score(sleep: dict[str, Any]) -> dict[str, Any] | None:
    """时长 40 + 深睡占比 25 + REM 占比 15 + 入睡耗时 10 + 主观质量 10。"""
    duration = _num(sleep.get("duration_min"))
    if not duration or duration <= 0:
        return None
    parts: dict[str, float] = {}
    weights: dict[str, float] = {}

    # 时长：7.0-9.0 小时满分；不足按比例扣，过长轻微扣分。
    if duration < 420:
        parts["duration"] = _clamp(duration / 420 * 100)
    elif duration <= 540:
        parts["duration"] = 100.0
    else:
        parts["duration"] = _clamp(100 - (duration - 540) / 3)
    weights["duration"] = 0.40

    deep = _num(sleep.get("deep_min"))
    if deep is not None and duration > 0:
        ratio = deep / duration
        parts["deep"] = 100.0 if 0.18 <= ratio <= 0.28 else _clamp(100 - abs(ratio - 0.23) * 500)
        weights["deep"] = 0.25

    rem = _num(sleep.get("rem_min"))
    if rem is not None and duration > 0:
        ratio = rem / duration
        parts["rem"] = 100.0 if 0.18 <= ratio <= 0.28 else _clamp(100 - abs(ratio - 0.23) * 500)
        weights["rem"] = 0.15

    latency = _num(sleep.get("latency_min"))
    if latency is not None:
        if latency <= 20:
            parts["latency"] = 100.0
        elif latency <= 60:
            parts["latency"] = _clamp(100 - (latency - 20) * 2)
        else:
            parts["latency"] = _clamp(20 - (latency - 60))
        weights["latency"] = 0.10

    quality = _num(sleep.get("quality"))
    if quality is not None:
        parts["quality"] = _clamp(quality / 5 * 100)
        weights["quality"] = 0.10

    total_weight = sum(weights.values()) or 1
    score = round(sum(parts[key] * weights[key] for key in parts) / total_weight)
    return {"score": int(_clamp(score)), "parts": {key: round(value) for key, value in parts.items()},
            "rating": _sleep_rating(score)}


def _sleep_rating(score: float) -> str:
    if score >= 85:
        return "优秀"
    if score >= 70:
        return "良好"
    if score >= 55:
        return "一般"
    return "偏差"


def blood_pressure_level(systolic: Any, diastolic: Any) -> str:
    high, low = _num(systolic), _num(diastolic)
    if high is None or low is None:
        return ""
    if high >= 180 or low >= 110:
        return "3 级高血压"
    if high >= 160 or low >= 100:
        return "2 级高血压"
    if high >= 140 or low >= 90:
        return "1 级高血压"
    if high >= 120 or low >= 80:
        return "正常高值"
    if high < 90 or low < 60:
        return "偏低"
    return "正常"


def abnormal_items(day: dict[str, Any]) -> list[dict[str, Any]]:
    """超出参考区间/常见阈值的当天记录；只做提示，不做诊断。"""
    items: list[dict[str, Any]] = []
    vitals = _section(day, "vitals")
    systolic, diastolic = _num(vitals.get("systolic")), _num(vitals.get("diastolic"))
    level = blood_pressure_level(systolic, diastolic)
    if level and level not in {"正常"}:
        items.append({"key": "vitals.systolic", "label": "血压",
                      "value": f"{int(systolic)}/{int(diastolic)} mmHg", "level": level})
    checks = (
        ("resting_hr", "静息心率", 50, 100, "bpm", 0),
        ("spo2", "血氧", 95, 100, "%", 0),
        ("temperature_c", "体温", 36.0, 37.3, "°C", 1),
        ("hrv_ms", "HRV", 20, 100, "ms", 0),
        ("glucose_mmol", "血糖", 3.9, 6.1, "mmol/L", 1),
    )
    for field, label, low, high, unit, digits in checks:
        value = _num(vitals.get(field))
        if value is None:
            continue
        if value < low or value > high:
            items.append({"key": f"vitals.{field}", "label": label,
                          "value": f"{value:.{digits}f} {unit}".strip(),
                          "level": "偏低" if value < low else "偏高"})
    return items


def _sleep_dimension(day: dict[str, Any], goal: float) -> float | None:
    sleep = _section(day, "sleep")
    duration = _num(sleep.get("duration_min"))
    if not duration:
        return None
    detail = sleep_score(sleep)
    duration_score = _clamp(duration / goal * 100) if goal else 100.0
    if detail is None:
        return duration_score
    return round(duration_score * 0.6 + detail["score"] * 0.4, 1)


def _nutrition_dimension(day: dict[str, Any], target: dict[str, Any]) -> float | None:
    from .health_store import nutrition_totals

    totals = nutrition_totals(day)
    parts: list[tuple[float, float]] = []
    calories = _num(totals.get("calories"))
    calories_goal = _num(target.get("calories"))
    if calories and calories_goal:
        parts.append((_clamp(100 - abs(calories - calories_goal) / calories_goal * 120), 0.4))
    protein = _num(totals.get("protein_g"))
    protein_goal = _num(target.get("protein_g"))
    if protein and protein_goal:
        parts.append((_clamp(protein / protein_goal * 100), 0.4))
    water = _num(totals.get("water_ml"))
    water_goal = _num(target.get("water_ml"))
    if water and water_goal:
        parts.append((_clamp(water / water_goal * 100), 0.2))
    if not parts:
        return None
    weight = sum(weight for _score, weight in parts) or 1
    return round(sum(score * weight for score, weight in parts) / weight, 1)


def _activity_dimension(day: dict[str, Any], target: dict[str, Any]) -> float | None:
    activity = _section(day, "activity")
    steps = _num(activity.get("steps"))
    steps_goal = _num(target.get("steps")) or 8000
    if steps is None:
        return None
    steps_score = _clamp(steps / steps_goal * 100)
    sedentary = _num(activity.get("sedentary_min"))
    if sedentary is None:
        return round(steps_score, 1)
    sedentary_score = _clamp(100 - max(0, sedentary - 300) / 6)
    return round(steps_score * 0.7 + sedentary_score * 0.3, 1)


def _mental_dimension(day: dict[str, Any], mindful_goal: float = 10) -> float | None:
    mind = _section(day, "mind")
    parts: list[tuple[float, float]] = []
    mood = _num(mind.get("mood"))
    if mood:
        parts.append((_clamp(mood / 5 * 100), 0.5))
    stress = _num(mind.get("stress"))
    if stress:
        parts.append((_clamp((5 - stress) / 4 * 100), 0.3))
    mindful = _num(mind.get("mindful_min"))
    if mindful is not None:
        parts.append((_clamp(mindful / (mindful_goal or 10) * 100), 0.2))
    if not parts:
        return None
    weight = sum(weight for _score, weight in parts) or 1
    return round(sum(score * weight for score, weight in parts) / weight, 1)


def _body_dimension(day: dict[str, Any], profile: dict[str, Any],
                    history: list[dict[str, Any]], target: dict[str, Any]) -> float | None:
    body = _section(day, "body")
    weight = _num(body.get("weight_kg"))
    height = _num(profile.get("height_cm"))
    if not weight:
        return None
    parts: list[tuple[float, float]] = []
    value = bmi(weight, height)
    if value is not None:
        if 18.5 <= value < 24:
            parts.append((100.0, 0.5))
        elif value < 18.5:
            parts.append((_clamp(100 - (18.5 - value) * 20), 0.5))
        else:
            parts.append((_clamp(100 - (value - 24) * 12), 0.5))
    fat = _num(body.get("body_fat_pct"))
    if fat is not None:
        low, high = (18, 32) if profile.get("gender") == "女" else (10, 28)
        if low <= fat <= high:
            parts.append((100.0, 0.3))
        else:
            distance = (low - fat) if fat < low else (fat - high)
            parts.append((_clamp(100 - distance * 8), 0.3))
    waist = _num(body.get("waist_cm"))
    if waist is not None:
        parts.append((100.0 if waist < 90 else _clamp(100 - (waist - 90) * 4), 0.2))
    if not parts:
        return None
    weight_sum = sum(weight for _score, weight in parts) or 1
    score = sum(value * weight for value, weight in parts) / weight_sum
    goal = _num(target.get("weight_kg"))
    if goal:
        previous = [(item["date"], _num(_section(item, "body").get("weight_kg")))
                    for item in history if item.get("date") != day.get("date")]
        previous = [(key, value) for key, value in previous if value is not None]
        if previous:
            _, last = previous[-1]
            if abs(last - goal) > abs(weight - goal) + 0.2:
                score = _clamp(score + 5)
            elif abs(weight - goal) > abs(last - goal) + 0.2:
                score = _clamp(score - 5)
    return round(score, 1)


def _training_dimension(day: dict[str, Any], history: list[dict[str, Any]],
                        target: dict[str, Any]) -> float | None:
    from .health_store import training_totals

    goal = _num(target.get("weekly_training")) or 4
    try:
        current_day = date.fromisoformat(str(day.get("date")))
    except ValueError:
        return None
    window_start = (current_day - timedelta(days=6)).isoformat()
    window = [item for item in history if window_start <= str(item.get("date")) <= str(day.get("date"))]
    sessions = sum(len(item.get("training") or []) for item in window)
    if not sessions:
        recorded = len([item for item in window if item.get("exists", True)])
        if recorded < 3:
            return None
        return 0.0
    count_score = _clamp(sessions / goal * 100)
    volume = sum(training_totals(item)["volume_kg"] for item in window)
    if volume <= 0:
        return round(count_score * 0.7, 1)
    earlier = [item for item in history
               if item.get("date") and item["date"] < window_start]
    earlier_volume = sum(training_totals(item)["volume_kg"] for item in earlier[-7:])
    if earlier_volume <= 0:
        volume_score = 70.0
    else:
        change = (volume - earlier_volume) / earlier_volume
        volume_score = _clamp(50 + change * 100)
    return round(count_score * 0.7 + volume_score * 0.3, 1)


def score_day(day: dict[str, Any], profile: dict[str, Any],
              history: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """计算一天的健康指数；缺数据的维度不参与加权。"""
    history = list(history or [])
    # 同一天可能来自磁盘旧版本：一律以传入的这条为准，避免评分用上一次的数据。
    history = [item for item in history
               if str(item.get("date") or "") != str(day.get("date") or "")]
    history.append(day)
    target = targets(profile, day)
    derived: dict[str, Any] = {"targets": target}
    body = _section(day, "body")
    value = bmi(body.get("weight_kg"), profile.get("height_cm"))
    if value is not None:
        derived["bmi"] = value
        derived["bmi_band"] = bmi_band(value)
    estimate = bmr(profile, body.get("weight_kg"), str(day.get("date") or ""))
    if estimate:
        derived["bmr"] = estimate
    detail = sleep_score(_section(day, "sleep"))
    if detail:
        derived["sleep_score"] = detail["score"]
        derived["sleep_rating"] = detail["rating"]
    level = blood_pressure_level(_section(day, "vitals").get("systolic"),
                                 _section(day, "vitals").get("diastolic"))
    if level:
        derived["bp_level"] = level
    derived["abnormal"] = abnormal_items(day)

    dimensions = {
        "sleep": _sleep_dimension(day, target["sleep_minutes"]),
        "nutrition": _nutrition_dimension(day, target),
        "activity": _activity_dimension(day, target),
        "mental": _mental_dimension(day, 10),
        "body": _body_dimension(day, profile, history, target),
        "training": _training_dimension(day, history, target),
    }
    weights = profile.get("score_weights") if isinstance(profile.get("score_weights"), dict) else {}
    breakdown = {key: (round(value, 1) if value is not None else None)
                 for key, value in dimensions.items()}
    available = {key: value for key, value in dimensions.items() if value is not None}
    insufficient = [BREAKDOWN_LABELS[key] for key in WEIGHT_KEYS if key not in available]
    if not available:
        return {"total": None, "breakdown": breakdown, "insufficient": insufficient,
                "derived": derived, "weights": weights}
    weight_map = {key: float(weights.get(key) or 0) for key in available}
    if sum(weight_map.values()) <= 0:
        weight_map = {key: 1.0 for key in available}
    denominator = sum(weight_map.values())
    numerator = sum(weight_map[key] * value for key, value in available.items())
    total = round(numerator / denominator)
    return {"total": int(_clamp(total)), "breakdown": breakdown,
            "insufficient": insufficient, "derived": derived, "weights": weights}


def score_series(days: list[dict[str, Any]], profile: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"date": day["date"], **score_day(day, profile, days)} for day in days]


# ── 跨维度洞察 ───────────────────────────────────────────────────────

def _series(days: list[dict[str, Any]], section: str, field: str) -> list[tuple[str, float]]:
    result = []
    for day in days:
        value = _num(_section(day, section).get(field))
        if value is not None:
            result.append((str(day.get("date")), value))
    return result


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _pearson(pairs: list[tuple[float, float]]) -> float | None:
    n = len(pairs)
    if n < 3:
        return None
    xs = [pair[0] for pair in pairs]
    ys = [pair[1] for pair in pairs]
    mx, my = _mean(xs), _mean(ys)
    numerator = sum((x - mx) * (y - my) for x, y in pairs)
    denominator = math.sqrt(sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys))
    return numerator / denominator if denominator else None


def _pct_change(first: float, second: float) -> str:
    if not second:
        return "—"
    return f"{(first - second) / abs(second) * 100:+.0f}%"


def insights(days: list[dict[str, Any]], profile: dict[str, Any],
             read_ids: list[str] | None = None) -> list[dict[str, Any]]:
    """规则洞察；样本不足时宁可不生成。"""
    from .health_store import nutrition_totals, training_totals

    read = set(read_ids or [])
    items: list[dict[str, Any]] = []
    ordered = sorted(days, key=lambda item: str(item.get("date")))
    if len(ordered) < 7:
        return []
    start, end = ordered[0]["date"], ordered[-1]["date"]
    span = f"{start} 至 {end}"

    def emit(kind: str, title: str, description: str, effect: str,
             sample: int, extra: dict[str, Any] | None = None) -> None:
        insight_id = f"{kind}:{start}:{end}"
        items.append({
            "id": insight_id, "type": kind, "title": title,
            "description": description, "effect": effect,
            "sample_size": sample, "range": span,
            "read": insight_id in read, **(extra or {}),
        })

    # 1. 睡眠 → 次日训练量
    sleep_by_day = dict(_series(ordered, "sleep", "duration_min"))
    short_minutes: list[float] = []
    long_minutes: list[float] = []
    for index, day in enumerate(ordered[:-1]):
        sleep = sleep_by_day.get(day["date"])
        if sleep is None:
            continue
        minutes = training_totals(ordered[index + 1])["minutes"]
        if minutes <= 0:
            continue
        if sleep < 360:
            short_minutes.append(minutes)
        elif sleep >= 420:
            long_minutes.append(minutes)
    if len(short_minutes) >= 5 and len(long_minutes) >= 5:
        short_avg, long_avg = _mean(short_minutes), _mean(long_minutes)
        if long_avg and abs(short_avg - long_avg) / long_avg >= 0.08:
            direction = "少" if short_avg < long_avg else "多"
            emit("sleep_training", "睡眠不足的次日，训练更短",
                 f"前一晚睡眠 <6 小时的 {len(short_minutes)} 天，次日训练平均 "
                 f"{short_avg:.0f} 分钟；睡眠 ≥7 小时的 {len(long_minutes)} 天平均 "
                 f"{long_avg:.0f} 分钟。",
                 _pct_change(short_avg, long_avg),
                 len(short_minutes) + len(long_minutes),
                 {"dimension": "睡眠 ↔ 训练", "note": f"睡眠少的次日训练量{direction}"})

    # 2. 步数 → 当天情绪
    steps = dict(_series(ordered, "activity", "steps"))
    moods = dict(_series(ordered, "mind", "mood"))
    high_moods = [moods[day["date"]] for day in ordered
                  if day["date"] in steps and day["date"] in moods and steps[day["date"]] >= 8000]
    low_moods = [moods[day["date"]] for day in ordered
                 if day["date"] in steps and day["date"] in moods and steps[day["date"]] < 5000]
    if len(high_moods) >= 7 and len(low_moods) >= 7:
        diff = _mean(high_moods) - _mean(low_moods)
        if abs(diff) >= 0.4:
            emit("steps_mood", "步数与情绪同向变化",
                 f"步数 ≥8000 的 {len(high_moods)} 天，情绪均值 {_mean(high_moods):.1f}/5；"
                 f"步数 <5000 的 {len(low_moods)} 天为 {_mean(low_moods):.1f}/5。",
                 f"{diff:+.1f} 分", len(high_moods) + len(low_moods),
                 {"dimension": "活动 ↔ 心理", "note": "只能说明相关，不能证明因果"})

    # 3. 蛋白质 → 体重（餐次明细在 meals 里，先合成为当天总量）
    proteins: dict[str, float] = {}
    for day in ordered:
        value = _num(nutrition_totals(day).get("protein_g"))
        if value is not None:
            proteins[str(day.get("date"))] = value
    weights = dict(_series(ordered, "body", "weight_kg"))
    pairs = [(proteins[key], weights[key]) for key in proteins if key in weights]
    if len(pairs) >= 14:
        coefficient = _pearson(pairs)
        if coefficient is not None and abs(coefficient) >= 0.4:
            emit("protein_weight", "蛋白质摄入与体重存在相关",
                 f"{len(pairs)} 天同时有蛋白质与体重记录，Pearson 相关系数 "
                 f"{coefficient:+.2f}。相关不等于因果，体重还受总热量、训练与水分影响。",
                 f"r = {coefficient:+.2f}", len(pairs),
                 {"dimension": "饮食 ↔ 身体"})

    # 4. 训练 → 次日静息心率
    hr = dict(_series(ordered, "vitals", "resting_hr"))
    trained: list[float] = []
    rested: list[float] = []
    for index, day in enumerate(ordered[:-1]):
        next_day = ordered[index + 1]
        value = hr.get(next_day["date"])
        if value is None:
            continue
        if day.get("training"):
            trained.append(value)
        else:
            rested.append(value)
    if len(trained) >= 5 and len(rested) >= 5:
        diff = _mean(rested) - _mean(trained)
        if abs(diff) >= 3:
            emit("training_hr", "训练日的次日静息心率不同",
                 f"训练后的次日静息心率平均 {_mean(trained):.0f} bpm（{len(trained)} 天），"
                 f"不训练的次日平均 {_mean(rested):.0f} bpm（{len(rested)} 天）。",
                 f"{_mean(trained) - _mean(rested):+.0f} bpm", len(trained) + len(rested),
                 {"dimension": "训练 ↔ 体征", "note": "单次训练后心率偏高常见，持续偏高才值得留意"})

    # 5. 周末效应
    weekday_sleep: list[float] = []
    weekend_sleep: list[float] = []
    for day in ordered:
        sleep = sleep_by_day.get(day["date"])
        if sleep is None:
            continue
        weekday = date.fromisoformat(day["date"]).weekday()
        (weekend_sleep if weekday >= 5 else weekday_sleep).append(sleep)
    if len(weekday_sleep) >= 7 and len(weekend_sleep) >= 4:
        diff = _mean(weekend_sleep) - _mean(weekday_sleep)
        if abs(diff) >= 25:
            emit("weekend_sleep", "周末与工作日的睡眠不同",
                 f"工作日平均睡眠 {_mean(weekday_sleep) / 60:.1f} 小时（{len(weekday_sleep)} 天），"
                 f"周末 {_mean(weekend_sleep) / 60:.1f} 小时（{len(weekend_sleep)} 天）。",
                 f"{diff:+.0f} 分钟", len(weekday_sleep) + len(weekend_sleep),
                 {"dimension": "作息节律"})

    order = {"sleep_training": 0, "steps_mood": 1, "protein_weight": 2,
             "training_hr": 3, "weekend_sleep": 4}
    items.sort(key=lambda item: (item["read"], order.get(item["type"], 9)))
    return items


__all__ = ["BREAKDOWN_LABELS", "WEIGHT_KEYS", "age_of", "bmi", "bmi_band", "bmr",
           "tdee", "targets", "sleep_score", "blood_pressure_level", "abnormal_items",
           "score_day", "score_series", "insights"]
