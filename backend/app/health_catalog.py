"""健康指标目录：健康系统唯一的指标定义表（2026-10-31）。

设计取舍
--------
上一版方案用 ``MetricDefinition`` / ``MetricEntry`` 两张数据库表做「一切皆指标」。
torch-os 的权威源是 Vault 里的 Markdown，SQLite 只是投影；为一个单人系统再引一套
可配置指标表，会让「数据在哪里」变得含糊。这里改成同一目标的最小实现：

- **声明表在代码里**（本文件）：新增一个指标 = 在这里加一行，不用改 schema、不用迁移；
- **记录在每日 Markdown**：``02_生活运营/02_健康系统/01_每日健康/<日期>.md`` 的
  frontmatter 按 ``section.field`` 存储，字段名就是 ``key`` 的最后一段；
- **派生值不落库**：BMI、BMR、睡眠评分、健康指数都由 ``health_score`` 现场计算，
  避免 Markdown 与数据库各存一份「事实」；
- 前端不复制字段表：``GET /api/health/catalog`` 把本文件里的分组、单位、范围、步长
  原样下发，表单、趋势选择器与总览卡片都按它渲染。

用户的私人指标（例如「血氧」「尿酸」之外的自定义项）放在身体档案的 ``custom_metrics``
里，记录写进每日笔记的 ``custom`` 映射；读取时与本文件的指标合并成同一个序列。
"""
from __future__ import annotations

from typing import Any

# 分组顺序 = 前端标签顺序。icon 只是给界面用的短标记，不引入图标字体。
GROUPS: tuple[dict[str, str], ...] = (
    {"key": "body", "title": "身体成分", "icon": "体",
     "hint": "体重、体脂、围度与体水分；BMI / BMR 自动计算。"},
    {"key": "vitals", "title": "生命体征", "icon": "征",
     "hint": "静息心率、血压、血氧、体温、HRV 与血糖。"},
    {"key": "sleep", "title": "睡眠", "icon": "眠",
     "hint": "入睡与起床时间、分期、入睡耗时与主观质量。"},
    {"key": "nutrition", "title": "饮食营养", "icon": "食",
     "hint": "热量、三大宏量、纤维、饮水、咖啡因与酒精。"},
    {"key": "activity", "title": "日常活动", "icon": "动",
     "hint": "步数、久坐、活动消耗与通勤距离。"},
    {"key": "mind", "title": "心理健康", "icon": "心",
     "hint": "情绪、压力、精力、焦虑、正念与一段日记。"},
    {"key": "training", "title": "训练", "icon": "练",
     "hint": "一次训练的主观疲劳、酸痛与心情；组数容量在训练页记录。"},
    {"key": "custom", "title": "自定义指标", "icon": "＋",
     "hint": "在设置里添加自己的指标；值写进每日笔记的 custom。"},
)

# kind:
#   number  小数输入（step 控制精度）
#   int     整数输入
#   rating  1-5 打分（按钮组）
#   time    HH:MM 文本
#   text    单行文本
#   minutes 分钟数（整数，0-1440）
# aggregation: 趋势里同一天多条记录时的合并方式（本系统一天一路，保留给导入数据）
# higher_better: True 越高越好 / False 越低越好 / None 只看区间
METRICS: dict[str, dict[str, Any]] = {}


def _metric(section: str, field: str, label: str, *, unit: str = "", kind: str = "number",
            minimum: float | None = None, maximum: float | None = None,
            step: float = 1, precision: int = 0, normal: tuple[float, float] | None = None,
            target: float | None = None, higher_better: bool | None = None,
            aggregation: str = "last", help_text: str = "") -> None:
    key = f"{section}.{field}"
    METRICS[key] = {
        "key": key, "section": section, "field": field, "label": label,
        "unit": unit, "kind": kind, "min": minimum, "max": maximum,
        "step": step, "precision": precision, "normal": normal,
        "target": target, "higher_better": higher_better,
        "aggregation": aggregation, "help": help_text,
    }


# ── 睡眠 ─────────────────────────────────────────────────────────────
_metric("sleep", "bed", "入睡时间", kind="time", help_text="按 23:40 这样的 24 小时制填写")
_metric("sleep", "wake", "起床时间", kind="time")
_metric("sleep", "duration_min", "睡眠时长", unit="分钟", kind="minutes",
        minimum=0, maximum=1440, target=450, higher_better=True, aggregation="sum")
_metric("sleep", "deep_min", "深睡", unit="分钟", kind="minutes", minimum=0, maximum=720)
_metric("sleep", "light_min", "浅睡", unit="分钟", kind="minutes", minimum=0, maximum=900)
_metric("sleep", "rem_min", "REM", unit="分钟", kind="minutes", minimum=0, maximum=600)
_metric("sleep", "awake_min", "夜间清醒", unit="分钟", kind="minutes", minimum=0, maximum=600)
_metric("sleep", "latency_min", "入睡耗时", unit="分钟", kind="minutes", minimum=0, maximum=240)
_metric("sleep", "wake_count", "夜醒次数", unit="次", kind="int", minimum=0, maximum=30)
_metric("sleep", "quality", "主观质量", kind="rating", minimum=1, maximum=5,
        higher_better=True)
_metric("sleep", "screen_min", "睡前屏幕", unit="分钟", kind="minutes",
        minimum=0, maximum=600)

# ── 身体成分 ─────────────────────────────────────────────────────────
_metric("body", "weight_kg", "体重", unit="kg", minimum=20, maximum=400,
        step=0.1, precision=1, higher_better=None, help_text="同一天多次称重取最后一次")
_metric("body", "body_fat_pct", "体脂率", unit="%", minimum=3, maximum=70,
        step=0.1, precision=1, normal=(10, 28), higher_better=False)
_metric("body", "muscle_kg", "肌肉量", unit="kg", minimum=3, maximum=150,
        step=0.1, precision=1, higher_better=True)
_metric("body", "water_pct", "体水分", unit="%", minimum=20, maximum=75,
        step=0.1, precision=1, normal=(45, 65))
_metric("body", "bone_kg", "骨量", unit="kg", minimum=0.5, maximum=15,
        step=0.1, precision=1)
_metric("body", "visceral_fat", "内脏脂肪等级", unit="级", kind="int",
        minimum=1, maximum=30, normal=(1, 9), higher_better=False)
_metric("body", "bmr", "基础代谢", unit="kcal", kind="int", minimum=500, maximum=5000)
_metric("body", "waist_cm", "腰围", unit="cm", minimum=30, maximum=200,
        step=0.1, precision=1, normal=(50, 90), higher_better=False)
_metric("body", "chest_cm", "胸围", unit="cm", minimum=30, maximum=200, step=0.1, precision=1)
_metric("body", "hip_cm", "臀围", unit="cm", minimum=30, maximum=200, step=0.1, precision=1)
_metric("body", "arm_cm", "臂围", unit="cm", minimum=15, maximum=100, step=0.1, precision=1)
_metric("body", "thigh_cm", "腿围", unit="cm", minimum=20, maximum=120, step=0.1, precision=1)
_metric("body", "calf_cm", "小腿围", unit="cm", minimum=15, maximum=80, step=0.1, precision=1)

# ── 生命体征 ─────────────────────────────────────────────────────────
_metric("vitals", "resting_hr", "静息心率", unit="bpm", kind="int",
        minimum=30, maximum=220, normal=(50, 100), higher_better=False)
_metric("vitals", "systolic", "收缩压", unit="mmHg", kind="int",
        minimum=50, maximum=260, normal=(90, 120), higher_better=False)
_metric("vitals", "diastolic", "舒张压", unit="mmHg", kind="int",
        minimum=30, maximum=180, normal=(60, 80), higher_better=False)
_metric("vitals", "spo2", "血氧", unit="%", kind="int", minimum=70, maximum=100,
        normal=(95, 100), higher_better=True)
_metric("vitals", "temperature_c", "体温", unit="°C", minimum=33, maximum=43,
        step=0.1, precision=1, normal=(36, 37.3))
_metric("vitals", "hrv_ms", "HRV", unit="ms", kind="int", minimum=5, maximum=300,
        normal=(20, 100), higher_better=True)
_metric("vitals", "glucose_mmol", "血糖", unit="mmol/L", minimum=1, maximum=40,
        step=0.1, precision=1, normal=(3.9, 6.1), higher_better=False)

# ── 饮食营养 ─────────────────────────────────────────────────────────
_metric("nutrition", "calories", "热量", unit="kcal", kind="int",
        minimum=0, maximum=10000, higher_better=None, aggregation="sum")
_metric("nutrition", "protein_g", "蛋白质", unit="g", minimum=0, maximum=500,
        step=1, precision=0, higher_better=True, aggregation="sum")
_metric("nutrition", "carbs_g", "碳水", unit="g", minimum=0, maximum=800,
        step=1, precision=0, aggregation="sum")
_metric("nutrition", "fat_g", "脂肪", unit="g", minimum=0, maximum=500,
        step=1, precision=0, aggregation="sum")
_metric("nutrition", "fiber_g", "膳食纤维", unit="g", minimum=0, maximum=200,
        step=1, precision=0, higher_better=True, aggregation="sum")
_metric("nutrition", "water_ml", "饮水", unit="ml", kind="int",
        minimum=0, maximum=8000, target=2000, higher_better=True, aggregation="sum")
_metric("nutrition", "caffeine_mg", "咖啡因", unit="mg", kind="int", minimum=0, maximum=1500)
_metric("nutrition", "alcohol_units", "酒精", unit="单位", minimum=0, maximum=30,
        step=0.5, precision=1, higher_better=False)

# ── 日常活动 ─────────────────────────────────────────────────────────
_metric("activity", "steps", "步数", unit="步", kind="int", minimum=0, maximum=100000,
        target=8000, higher_better=True, aggregation="sum")
_metric("activity", "sedentary_min", "久坐时长", unit="分钟", kind="minutes",
        minimum=0, maximum=1440, higher_better=False, aggregation="sum")
_metric("activity", "active_cal", "活动消耗", unit="kcal", kind="int",
        minimum=0, maximum=5000, higher_better=True, aggregation="sum")
_metric("activity", "distance_km", "活动距离", unit="km", minimum=0, maximum=200,
        step=0.1, precision=1, aggregation="sum")

# ── 心理健康 ─────────────────────────────────────────────────────────
_metric("mind", "mood", "情绪", kind="rating", minimum=1, maximum=5, higher_better=True)
_metric("mind", "stress", "压力", kind="rating", minimum=1, maximum=5, higher_better=False)
_metric("mind", "energy", "精力", kind="rating", minimum=1, maximum=5, higher_better=True)
_metric("mind", "anxiety", "焦虑", kind="rating", minimum=1, maximum=5, higher_better=False)
_metric("mind", "mindful_min", "正念", unit="分钟", kind="minutes",
        minimum=0, maximum=300, target=10, higher_better=True, aggregation="sum")
_metric("mind", "journal", "今日感受", kind="text", help_text="只写给自己看，不进入 AI 结论")

# ── 训练（单次训练的主观字段）────────────────────────────────────────
_metric("training", "minutes", "训练时长", unit="分钟", kind="int",
        minimum=1, maximum=600, target=45, higher_better=True, aggregation="sum")
_metric("training", "rpe", "主观疲劳", kind="rating", minimum=1, maximum=10,
        higher_better=False, help_text="1 很轻松，10 极限")
_metric("training", "soreness", "肌肉酸痛", kind="rating", minimum=1, maximum=5,
        higher_better=False)
_metric("training", "mood_after", "训练后心情", kind="rating", minimum=1, maximum=5,
        higher_better=True)

# ── 体检与检验（常见项目预设；参考区间按成人通用值，报告有区间时以报告为准）──
LAB_TESTS: tuple[dict[str, Any], ...] = (
    {"name": "总胆固醇", "category": "血脂", "unit": "mmol/L", "ref_min": 2.8, "ref_max": 5.2},
    {"name": "甘油三酯", "category": "血脂", "unit": "mmol/L", "ref_min": 0.4, "ref_max": 1.7},
    {"name": "低密度脂蛋白", "category": "血脂", "unit": "mmol/L", "ref_min": 0, "ref_max": 3.4},
    {"name": "高密度脂蛋白", "category": "血脂", "unit": "mmol/L", "ref_min": 1.0, "ref_max": 2.0},
    {"name": "空腹血糖", "category": "血糖", "unit": "mmol/L", "ref_min": 3.9, "ref_max": 6.1},
    {"name": "糖化血红蛋白", "category": "血糖", "unit": "%", "ref_min": 4.0, "ref_max": 6.0},
    {"name": "谷丙转氨酶", "category": "肝功能", "unit": "U/L", "ref_min": 7, "ref_max": 40},
    {"name": "谷草转氨酶", "category": "肝功能", "unit": "U/L", "ref_min": 13, "ref_max": 35},
    {"name": "尿酸", "category": "肾功能", "unit": "μmol/L", "ref_min": 208, "ref_max": 428},
    {"name": "肌酐", "category": "肾功能", "unit": "μmol/L", "ref_min": 44, "ref_max": 106},
    {"name": "维生素 D", "category": "营养", "unit": "ng/mL", "ref_min": 30, "ref_max": 100},
    {"name": "促甲状腺激素", "category": "甲状腺", "unit": "mIU/L", "ref_min": 0.27, "ref_max": 4.2},
)

LAB_CATEGORIES: tuple[str, ...] = ("血脂", "血糖", "肝功能", "肾功能", "甲状腺", "营养", "其他")

FIELD_ORDER: dict[str, tuple[str, ...]] = {}
for _key, _item in METRICS.items():
    FIELD_ORDER.setdefault(_item["section"], ())
    FIELD_ORDER[_item["section"]] = (*FIELD_ORDER[_item["section"]], _item["field"])

SECTION_TITLES = {group["key"]: group["title"] for group in GROUPS}


def metric(key: str) -> dict[str, Any] | None:
    return METRICS.get(str(key or "").strip())


def section_fields(section: str) -> list[dict[str, Any]]:
    """某个分区的字段定义（按声明顺序），供表单与趋势使用。"""
    order = FIELD_ORDER.get(str(section or ""), ())
    return [METRICS[f"{section}.{field}"] for field in order if f"{section}.{field}" in METRICS]


def numeric_fields(section: str) -> list[dict[str, Any]]:
    return [item for item in section_fields(section)
            if item["kind"] in {"number", "int", "minutes", "rating"}]


def catalog() -> dict[str, Any]:
    """下发给前端的目录：只含可填写字段与展示元数据。"""
    groups = []
    for group in GROUPS:
        fields = section_fields(group["key"]) if group["key"] != "custom" else []
        groups.append({**group, "fields": [_public(item) for item in fields]})
    return {
        "groups": groups,
        "lab_tests": [dict(item) for item in LAB_TESTS],
        "lab_categories": list(LAB_CATEGORIES),
        "ratings": {
            "mood": ["很低落", "偏低", "一般", "不错", "很好"],
            "stress": ["很放松", "较放松", "一般", "有点紧", "很紧张"],
            "energy": ["没力气", "偏累", "一般", "有精神", "很充沛"],
            "anxiety": ["很安定", "较安定", "一般", "有点焦虑", "很焦虑"],
            "quality": ["很差", "较差", "一般", "不错", "很好"],
            "rpe": ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"],
        },
        "sections": [{"key": key, "title": value} for key, value in SECTION_TITLES.items()],
    }


def _public(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": item["key"], "field": item["field"], "label": item["label"],
        "unit": item["unit"], "kind": item["kind"], "min": item["min"],
        "max": item["max"], "step": item["step"], "precision": item["precision"],
        "normal": list(item["normal"]) if item["normal"] else None,
        "target": item["target"], "higher_better": item["higher_better"],
        "help": item["help"],
    }


def custom_metric_key(name: str) -> str:
    return f"custom.{str(name or '').strip()}"


__all__ = ["GROUPS", "METRICS", "LAB_TESTS", "LAB_CATEGORIES", "FIELD_ORDER",
           "SECTION_TITLES", "metric", "section_fields", "numeric_fields",
           "catalog", "custom_metric_key"]
