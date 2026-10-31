"""健康系统的本地动作库（文本复用自 openGym，见 ``scripts/import_opengym_exercises.py``）。

只读、无网络：动作列表在 ``backend/app/data/exercises.json``，第一次查询时载入内存。
不复制 openGym 的图片/动画（那是它自己的授权素材）；这里只有名称、部位、器械、
目标肌群和一句描述，用于训练记录的动作选择与搜索。
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_FILE = Path(__file__).resolve().parent / "health_assets" / "exercises.json"
MAX_RESULTS = 60

BODY_LABELS = {
    "back": "背部", "cardio": "有氧", "chest": "胸部", "lower arms": "小臂",
    "lower legs": "小腿", "neck": "颈部", "shoulders": "肩部",
    "upper arms": "上臂", "upper legs": "大腿", "waist": "腰腹",
    "full body": "全身",
}
EQUIPMENT_LABELS = {
    "assisted": "辅助器械", "band": "弹力带", "barbell": "杠铃",
    "body weight": "自重", "bosu ball": "波速球", "cable": "龙门架",
    "dumbbell": "哑铃", "elliptical machine": "椭圆机", "ez barbell": "曲杆",
    "hammer": "锤式器械", "kettlebell": "壶铃", "leverage machine": "杠杆器械",
    "medicine ball": "药球", "olympic barbell": "奥杆", "resistance band": "阻力带",
    "roller": "泡沫轴", "rope": "绳索", "skierg machine": "滑雪机",
    "sled machine": "雪橇机", "smith machine": "史密斯机", "stability ball": "瑞士球",
    "stationary bike": "动感单车", "stepmill machine": "登楼机", "tire": "轮胎",
    "trap bar": "六角杠", "upper body ergometer": "上肢功率车",
    "weighted": "负重", "wheel roller": "健腹轮",
}
_CJK = re.compile(r"[\u4e00-\u9fff]")


@lru_cache(maxsize=1)
def all_exercises() -> tuple[dict[str, Any], ...]:
    try:
        payload = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return ()
    cleaned: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        cleaned.append({
            "id": str(item.get("id") or ""),
            "name": str(item.get("name") or ""),
            "body": str(item.get("body") or ""),
            "body_label": BODY_LABELS.get(str(item.get("body") or ""), str(item.get("body") or "")),
            "equipment": str(item.get("equipment") or ""),
            "equipment_label": EQUIPMENT_LABELS.get(str(item.get("equipment") or ""),
                                                     str(item.get("equipment") or "")),
            "target": str(item.get("target") or ""),
            "category": str(item.get("category") or ""),
            "description": str(item.get("description") or ""),
            "zh": bool(_CJK.search(str(item.get("name") or ""))),
        })
    return tuple(cleaned)


def facets() -> dict[str, Any]:
    items = all_exercises()
    bodies: dict[str, int] = {}
    equipment: dict[str, int] = {}
    for item in items:
        if item["body"]:
            bodies[item["body"]] = bodies.get(item["body"], 0) + 1
        if item["equipment"]:
            equipment[item["equipment"]] = equipment.get(item["equipment"], 0) + 1
    return {
        "total": len(items),
        "bodies": [
            {"key": key, "label": BODY_LABELS.get(key, key), "count": count}
            for key, count in sorted(bodies.items(), key=lambda pair: -pair[1])
        ],
        "equipments": [
            {"key": key, "label": EQUIPMENT_LABELS.get(key, key), "count": count}
            for key, count in sorted(equipment.items(), key=lambda pair: -pair[1])
        ],
    }


def search(query: str = "", body: str = "", equipment: str = "",
           limit: int = 40) -> list[dict[str, Any]]:
    text = str(query or "").strip().casefold()
    body_key = str(body or "").strip().casefold()
    equipment_key = str(equipment or "").strip().casefold()
    found: list[dict[str, Any]] = []
    for item in all_exercises():
        if body_key and item["body"].casefold() != body_key:
            continue
        if equipment_key and item["equipment"].casefold() != equipment_key:
            continue
        if text:
            haystack = " ".join([item["name"], item["target"], item["equipment"],
                                 item["body_label"], item["equipment_label"],
                                 item["description"]]).casefold()
            if text not in haystack:
                continue
        found.append(item)
    # 中文名优先，其次按名称；未命中中文名时英文名仍可搜到。
    found.sort(key=lambda item: (not item["zh"], len(item["name"]), item["name"]))
    return found[:max(1, min(int(limit or 40), MAX_RESULTS))]


def find(exercise_id: str) -> dict[str, Any] | None:
    key = str(exercise_id or "").strip()
    if not key:
        return None
    for item in all_exercises():
        if item["id"] == key:
            return item
    return None


__all__ = ["BODY_LABELS", "EQUIPMENT_LABELS", "all_exercises", "facets", "search", "find"]
