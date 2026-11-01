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
_metric("nutrition", "food_text", "吃了什么", kind="text",
        help_text="用一句话描述今天吃了什么；保存时可以让 AI 按描述估算热量")
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
_metric("activity", "exercise_text", "做了什么运动", kind="text",
        help_text="例如：跑步 1 小时，跑步机显示 500 大卡；保存时可以让 AI 按描述估算")
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
_metric("training", "calories", "运动消耗", unit="kcal", kind="int",
        minimum=0, maximum=5000, higher_better=True, aggregation="sum")
_metric("training", "distance_km", "运动距离", unit="km", minimum=0, maximum=200,
        step=0.1, precision=1, aggregation="sum")
_metric("training", "steps", "运动步数", unit="步", kind="int",
        minimum=0, maximum=100000, aggregation="sum")
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

# ── 常吃食物（按常见份量预置，单位：kcal / g）────────────────────────
# 只用于「点一下就填」和 AI 不可用时的兜底；份量是常见估值，用户仍可在
# 估算预览里改数字。名称与关键词都参与搜索。
FOOD_PRESETS: tuple[dict[str, Any], ...] = (
    {"name": "米饭", "portion": "1 碗（约 150g）", "keywords": "米饭 白饭 大米",
     "calories": 174, "protein_g": 3.9, "carbs_g": 38.9, "fat_g": 0.5},
    {"name": "米饭（半碗）", "portion": "半碗（约 75g）", "keywords": "米饭 半碗",
     "calories": 87, "protein_g": 2.0, "carbs_g": 19.5, "fat_g": 0.3},
    {"name": "杂粮饭", "portion": "1 碗（约 150g）", "keywords": "杂粮饭 糙米饭",
     "calories": 180, "protein_g": 4.5, "carbs_g": 37.0, "fat_g": 1.5},
    {"name": "馒头", "portion": "1 个（约 100g）", "keywords": "馒头 馍",
     "calories": 223, "protein_g": 7.0, "carbs_g": 47.0, "fat_g": 1.1},
    {"name": "花卷", "portion": "1 个（约 100g）", "keywords": "花卷",
     "calories": 210, "protein_g": 6.0, "carbs_g": 42.0, "fat_g": 2.0},
    {"name": "面条", "portion": "1 碗（约 200g）", "keywords": "面条 挂面 汤面",
     "calories": 220, "protein_g": 7.0, "carbs_g": 44.0, "fat_g": 1.5},
    {"name": "米线", "portion": "1 碗（约 200g）", "keywords": "米线 米粉",
     "calories": 230, "protein_g": 4.0, "carbs_g": 50.0, "fat_g": 1.0},
    {"name": "白粥", "portion": "1 碗（约 300g）", "keywords": "粥 白粥 稀饭",
     "calories": 90, "protein_g": 3.0, "carbs_g": 18.0, "fat_g": 1.0},
    {"name": "小米粥", "portion": "1 碗（约 300g）", "keywords": "小米粥",
     "calories": 100, "protein_g": 3.0, "carbs_g": 21.0, "fat_g": 1.0},
    {"name": "燕麦", "portion": "1 碗（约 40g 干）", "keywords": "燕麦 麦片",
     "calories": 150, "protein_g": 5.0, "carbs_g": 27.0, "fat_g": 3.0},
    {"name": "面包", "portion": "1 片（约 30g）", "keywords": "面包 吐司",
     "calories": 80, "protein_g": 2.5, "carbs_g": 14.0, "fat_g": 1.5},
    {"name": "包子", "portion": "1 个（约 100g）", "keywords": "包子 肉包 菜包",
     "calories": 220, "protein_g": 6.0, "carbs_g": 32.0, "fat_g": 7.0},
    {"name": "饺子", "portion": "10 个（约 200g）", "keywords": "饺子 水饺",
     "calories": 480, "protein_g": 20.0, "carbs_g": 55.0, "fat_g": 18.0},
    {"name": "油条", "portion": "1 根（约 60g）", "keywords": "油条",
     "calories": 270, "protein_g": 4.0, "carbs_g": 28.0, "fat_g": 16.0},
    {"name": "煎饼果子", "portion": "1 个（约 180g）", "keywords": "煎饼 煎饼果子",
     "calories": 400, "protein_g": 12.0, "carbs_g": 45.0, "fat_g": 18.0},
    {"name": "鸡蛋", "portion": "1 个（约 50g）", "keywords": "鸡蛋 水煮蛋 煎蛋",
     "calories": 72, "protein_g": 6.3, "carbs_g": 0.4, "fat_g": 4.9},
    {"name": "鸡蛋羹", "portion": "1 碗（约 150g）", "keywords": "鸡蛋羹 蒸蛋",
     "calories": 100, "protein_g": 8.0, "carbs_g": 2.0, "fat_g": 6.5},
    {"name": "牛奶", "portion": "1 杯（约 250ml）", "keywords": "牛奶 纯牛奶",
     "calories": 135, "protein_g": 7.5, "carbs_g": 12.0, "fat_g": 7.5},
    {"name": "低脂牛奶", "portion": "1 杯（约 250ml）", "keywords": "低脂奶 脱脂奶",
     "calories": 90, "protein_g": 8.0, "carbs_g": 11.0, "fat_g": 1.5},
    {"name": "豆浆", "portion": "1 杯（约 300ml）", "keywords": "豆浆",
     "calories": 45, "protein_g": 3.0, "carbs_g": 6.0, "fat_g": 1.0},
    {"name": "酸奶", "portion": "1 杯（约 200g）", "keywords": "酸奶",
     "calories": 140, "protein_g": 6.0, "carbs_g": 20.0, "fat_g": 4.0},
    {"name": "鸡胸肉", "portion": "1 份（约 100g）", "keywords": "鸡胸肉 鸡胸",
     "calories": 133, "protein_g": 31.0, "carbs_g": 0.0, "fat_g": 1.0},
    {"name": "鸡腿", "portion": "1 个（约 100g）", "keywords": "鸡腿",
     "calories": 181, "protein_g": 24.0, "carbs_g": 0.0, "fat_g": 9.0},
    {"name": "牛肉", "portion": "1 份（约 100g）", "keywords": "牛肉 牛腩",
     "calories": 250, "protein_g": 26.0, "carbs_g": 0.0, "fat_g": 15.0},
    {"name": "猪瘦肉", "portion": "1 份（约 100g）", "keywords": "瘦肉 猪肉",
     "calories": 143, "protein_g": 20.0, "carbs_g": 0.0, "fat_g": 6.0},
    {"name": "五花肉", "portion": "1 份（约 100g）", "keywords": "五花肉 红烧肉",
     "calories": 395, "protein_g": 14.0, "carbs_g": 0.0, "fat_g": 37.0},
    {"name": "鱼", "portion": "1 份（约 100g）", "keywords": "鱼 鱼肉",
     "calories": 100, "protein_g": 18.0, "carbs_g": 0.0, "fat_g": 3.0},
    {"name": "虾", "portion": "1 份（约 100g）", "keywords": "虾 虾仁",
     "calories": 93, "protein_g": 18.0, "carbs_g": 1.0, "fat_g": 1.5},
    {"name": "豆腐", "portion": "1 块（约 100g）", "keywords": "豆腐",
     "calories": 80, "protein_g": 8.0, "carbs_g": 2.0, "fat_g": 4.0},
    {"name": "青菜", "portion": "1 份（约 200g）", "keywords": "青菜 蔬菜 小白菜",
     "calories": 40, "protein_g": 3.0, "carbs_g": 5.0, "fat_g": 1.0},
    {"name": "西兰花", "portion": "1 份（约 150g）", "keywords": "西兰花",
     "calories": 50, "protein_g": 4.0, "carbs_g": 7.0, "fat_g": 0.5},
    {"name": "番茄炒蛋", "portion": "1 份（约 250g）", "keywords": "番茄炒蛋 西红柿炒蛋",
     "calories": 250, "protein_g": 12.0, "carbs_g": 10.0, "fat_g": 18.0},
    {"name": "宫保鸡丁", "portion": "1 份（约 250g）", "keywords": "宫保鸡丁",
     "calories": 450, "protein_g": 25.0, "carbs_g": 20.0, "fat_g": 30.0},
    {"name": "红烧肉", "portion": "1 份（约 200g）", "keywords": "红烧肉",
     "calories": 600, "protein_g": 20.0, "carbs_g": 10.0, "fat_g": 52.0},
    {"name": "麻婆豆腐", "portion": "1 份（约 250g）", "keywords": "麻婆豆腐",
     "calories": 300, "protein_g": 15.0, "carbs_g": 12.0, "fat_g": 22.0},
    {"name": "清蒸鱼", "portion": "1 份（约 200g）", "keywords": "清蒸鱼",
     "calories": 180, "protein_g": 30.0, "carbs_g": 2.0, "fat_g": 6.0},
    {"name": "炒青菜", "portion": "1 份（约 200g）", "keywords": "炒青菜 炒蔬菜",
     "calories": 90, "protein_g": 3.0, "carbs_g": 6.0, "fat_g": 6.0},
    {"name": "凉拌黄瓜", "portion": "1 份（约 150g）", "keywords": "凉拌黄瓜 黄瓜",
     "calories": 40, "protein_g": 1.0, "carbs_g": 6.0, "fat_g": 1.5},
    {"name": "炒饭", "portion": "1 份（约 300g）", "keywords": "炒饭 蛋炒饭",
     "calories": 550, "protein_g": 15.0, "carbs_g": 75.0, "fat_g": 20.0},
    {"name": "拉面", "portion": "1 碗（约 400g）", "keywords": "拉面 牛肉面",
     "calories": 500, "protein_g": 20.0, "carbs_g": 70.0, "fat_g": 16.0},
    {"name": "螺蛳粉", "portion": "1 碗（约 400g）", "keywords": "螺蛳粉",
     "calories": 600, "protein_g": 18.0, "carbs_g": 75.0, "fat_g": 25.0},
    {"name": "麻辣烫", "portion": "1 份（约 400g）", "keywords": "麻辣烫",
     "calories": 500, "protein_g": 20.0, "carbs_g": 50.0, "fat_g": 25.0},
    {"name": "火锅", "portion": "1 顿", "keywords": "火锅",
     "calories": 800, "protein_g": 40.0, "carbs_g": 50.0, "fat_g": 50.0},
    {"name": "烧烤", "portion": "1 顿", "keywords": "烧烤 烤串",
     "calories": 700, "protein_g": 35.0, "carbs_g": 40.0, "fat_g": 45.0},
    {"name": "汉堡", "portion": "1 个", "keywords": "汉堡",
     "calories": 500, "protein_g": 25.0, "carbs_g": 45.0, "fat_g": 25.0},
    {"name": "披萨", "portion": "1 块（约 120g）", "keywords": "披萨 比萨",
     "calories": 285, "protein_g": 12.0, "carbs_g": 33.0, "fat_g": 11.0},
    {"name": "炸鸡", "portion": "1 块（约 100g）", "keywords": "炸鸡",
     "calories": 300, "protein_g": 20.0, "carbs_g": 12.0, "fat_g": 20.0},
    {"name": "薯条", "portion": "1 份（约 100g）", "keywords": "薯条",
     "calories": 365, "protein_g": 4.0, "carbs_g": 45.0, "fat_g": 18.0},
    {"name": "三明治", "portion": "1 个（约 150g）", "keywords": "三明治",
     "calories": 300, "protein_g": 15.0, "carbs_g": 30.0, "fat_g": 13.0},
    {"name": "沙拉", "portion": "1 份（约 250g）", "keywords": "沙拉 蔬菜沙拉",
     "calories": 150, "protein_g": 8.0, "carbs_g": 12.0, "fat_g": 8.0},
    {"name": "可乐", "portion": "1 罐（约 330ml）", "keywords": "可乐 汽水",
     "calories": 139, "protein_g": 0.0, "carbs_g": 35.0, "fat_g": 0.0},
    {"name": "奶茶", "portion": "1 杯（约 500ml）", "keywords": "奶茶",
     "calories": 350, "protein_g": 5.0, "carbs_g": 55.0, "fat_g": 12.0},
    {"name": "美式咖啡", "portion": "1 杯（约 300ml）", "keywords": "美式 咖啡",
     "calories": 5, "protein_g": 0.5, "carbs_g": 0.0, "fat_g": 0.0},
    {"name": "拿铁", "portion": "1 杯（约 300ml）", "keywords": "拿铁 咖啡",
     "calories": 150, "protein_g": 8.0, "carbs_g": 12.0, "fat_g": 8.0},
    {"name": "啤酒", "portion": "1 罐（约 330ml）", "keywords": "啤酒",
     "calories": 150, "protein_g": 1.0, "carbs_g": 12.0, "fat_g": 0.0},
    {"name": "苹果", "portion": "1 个（约 200g）", "keywords": "苹果",
     "calories": 104, "protein_g": 0.5, "carbs_g": 28.0, "fat_g": 0.3},
    {"name": "香蕉", "portion": "1 根（约 120g）", "keywords": "香蕉",
     "calories": 107, "protein_g": 1.3, "carbs_g": 27.0, "fat_g": 0.4},
    {"name": "橙子", "portion": "1 个（约 200g）", "keywords": "橙子 橘子",
     "calories": 94, "protein_g": 1.8, "carbs_g": 24.0, "fat_g": 0.2},
    {"name": "西瓜", "portion": "1 块（约 300g）", "keywords": "西瓜",
     "calories": 90, "protein_g": 1.8, "carbs_g": 23.0, "fat_g": 0.3},
    {"name": "坚果", "portion": "1 小把（约 30g）", "keywords": "坚果 花生 核桃",
     "calories": 180, "protein_g": 5.0, "carbs_g": 6.0, "fat_g": 15.0},
    {"name": "红薯", "portion": "1 个（约 200g）", "keywords": "红薯 地瓜",
     "calories": 180, "protein_g": 3.0, "carbs_g": 42.0, "fat_g": 0.3},
    {"name": "玉米", "portion": "1 根（约 200g）", "keywords": "玉米",
     "calories": 110, "protein_g": 4.0, "carbs_g": 24.0, "fat_g": 1.5},
    {"name": "土豆", "portion": "1 个（约 200g）", "keywords": "土豆 马铃薯",
     "calories": 150, "protein_g": 4.0, "carbs_g": 34.0, "fat_g": 0.2},
    {"name": "汤", "portion": "1 碗（约 300ml）", "keywords": "汤 清汤",
     "calories": 60, "protein_g": 3.0, "carbs_g": 6.0, "fat_g": 2.0},
    {"name": "粽子", "portion": "1 个（约 150g）", "keywords": "粽子",
     "calories": 300, "protein_g": 6.0, "carbs_g": 50.0, "fat_g": 9.0},
    {"name": "蛋糕", "portion": "1 块（约 100g）", "keywords": "蛋糕 甜点",
     "calories": 350, "protein_g": 5.0, "carbs_g": 45.0, "fat_g": 17.0},
)

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


def food_items(query: str = "", limit: int = 80) -> list[dict[str, Any]]:
    """搜索常吃食物；空查询返回完整清单（上限 limit）。"""
    text = str(query or "").strip().casefold()
    count = max(1, min(int(limit or 80), 200))
    items: list[dict[str, Any]] = []
    for index, item in enumerate(FOOD_PRESETS):
        haystack = " ".join([
            str(item.get("name") or ""), str(item.get("portion") or ""),
            str(item.get("keywords") or ""),
        ]).casefold()
        if text and text not in haystack:
            continue
        items.append({
            "id": f"food-{index + 1}", **{key: value for key, value in item.items()
                                          if key != "keywords"},
        })
        if len(items) >= count:
            break
    return items


__all__ = ["GROUPS", "METRICS", "LAB_TESTS", "LAB_CATEGORIES", "FIELD_ORDER",
           "FOOD_PRESETS", "SECTION_TITLES", "metric", "section_fields",
           "numeric_fields", "catalog", "custom_metric_key", "food_items"]
