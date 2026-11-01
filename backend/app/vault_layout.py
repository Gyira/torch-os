"""四库布局的唯一路径与索引策略来源（2026-10-26 人生路 v2）。

- 四个用户库：00_人生路 / 01_自我档案 / 02_生活运营 / 03_知识库；
- 人生路 v2 把反思复盘、目标、项目收进分组目录（`02_反思复盘`、`04_目标`、`05_项目`），
  侧边栏分组见 `CHAPTER_GROUPS`；v1 的旧章节目录只由迁移搬到新路径
  （见 `V1_CHAPTER_MOVES`），运行时不再写入；
- 工作台任务与待办在 `02_生活运营/00_待办事项`（任务根 + 收集箱/已完成/回收站）；
  旧 00_人生路/07_任务与待办、08_任务与待办、00-System 只做只读兜底与迁移；
- 2026-11-01 生活运营目录重排：`03_体检与医疗` 退役并入
  `02_健康系统/03_体检与检验`，其余目录压紧编号（见 `OPS_LAYOUT_MOVES`）；
- 旧布局（02-Life/journal、01-Identity、90-Inbox…）只做只读兜底，迁移后不再写入。

所有路径都以创建目录（Vault 根）为基准，使用 POSIX 相对路径字符串，
避免在 Windows / Obsidian / 测试之间出现分隔符差异。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LAYOUT_VERSION = "pillars-v2"
SYSTEM_DIR = "00-System"
FOREIGN_FILES = ("dashboard.md", "dashboard-2.md")

PILLAR_TITLES = {
    "life": "人生路",
    "self": "自我档案",
    "ops": "生活运营",
    "knowledge": "知识库",
    # 恋爱记忆不是第五个主库：它只作为生活运营下的写作域，供恋爱系统使用。
    "love": "恋爱记忆",
}
PILLAR_DIRS = {
    "life": "00_人生路",
    "self": "01_自我档案",
    "ops": "02_生活运营",
    "knowledge": "03_知识库",
}
PILLAR_ORDER = ("life", "self", "ops", "knowledge")

# 恋爱系统 v2（2026-11-01）：生活运营下的二级分区；记忆使用独立写作域，
# 但不进入四库首页/迁移状态，避免把“四库”变成五库。
LOVE_PILLAR = "love"
LOVE_ROOT = "02_生活运营/01_恋爱系统"
LOVE_MEMORY_DIR = f"{LOVE_ROOT}/01_恋爱记忆"
LOVE_JOURNAL_DIR = f"{LOVE_MEMORY_DIR}/恋爱日志"
LOVE_DRAFT_DIR = f"{LOVE_MEMORY_DIR}/AI 草稿"
LOVE_JOURNAL_CHAPTER = "love_journal"
LOVE_DRAFT_CHAPTER = "love_drafts"
LOVE_NOTEBOOK_PREFIX = "custom:love:"

# 任务数据层（2026-10-31）：落在生活运营下，根目录直接是收集箱/已完成/回收站/各清单文件夹。
TASK_ROOT = "02_生活运营/00_待办事项"
TASK_INBOX_DIR = f"{TASK_ROOT}/收集箱"
TASK_DONE_DIR = f"{TASK_ROOT}/已完成"
TASK_TRASH_DIR = f"{TASK_ROOT}/回收站"
TASK_SYSTEM_DIR = f"{TASK_ROOT}/_系统"
TASK_SPECIAL_DIRS = (TASK_INBOX_DIR, TASK_DONE_DIR, TASK_TRASH_DIR, TASK_SYSTEM_DIR)

# 自我档案重构（2026-11-01）：三个部分一一对应三个目录。
# - 01_档案：11 个固定本子 + 身份/画像两个特殊文件；
# - 02_人生导师：分层记忆、待确认、每日三问与历史；
# - 03_八字系统：个人命盘与 AI 解读。
SELF_ROOT = PILLAR_DIRS["self"]
SELF_ARCHIVE_ROOT = f"{SELF_ROOT}/01_档案"
SELF_MENTOR_ROOT = f"{SELF_ROOT}/02_人生导师"
SELF_BAZI_ROOT = f"{SELF_ROOT}/03_八字系统"
SELF_IDENTITY_RELATIVE = f"{SELF_ARCHIVE_ROOT}/身份（identity）.md"
SELF_PORTRAIT_RELATIVE = f"{SELF_ARCHIVE_ROOT}/00_画像.md"
SELF_BACKUP_PREFIX = f"{SELF_ROOT}/99_重构备份-"
SELF_MENTOR_MEMORY_DIR = f"{SELF_MENTOR_ROOT}/记忆"
SELF_MENTOR_PENDING_DIR = f"{SELF_MENTOR_ROOT}/待确认"
SELF_MENTOR_PENDING_PATCHES = f"{SELF_MENTOR_PENDING_DIR}/档案补丁"
SELF_MENTOR_PENDING_ACCEPTED = f"{SELF_MENTOR_PENDING_DIR}/已采纳"
SELF_MENTOR_PENDING_REJECTED = f"{SELF_MENTOR_PENDING_DIR}/已驳回"
SELF_MENTOR_QUESTIONS_DIR = f"{SELF_MENTOR_ROOT}/提问"
SELF_MENTOR_QUESTIONS_HISTORY = f"{SELF_MENTOR_QUESTIONS_DIR}/历史"
SELF_MENTOR_HISTORY_DIR = f"{SELF_MENTOR_ROOT}/历史"
SELF_MENTOR_PORTRAIT_HISTORY = f"{SELF_MENTOR_HISTORY_DIR}/画像"
SELF_MENTOR_OLD_OBSERVATIONS = f"{SELF_MENTOR_HISTORY_DIR}/旧忆灵观察"
SELF_BAZI_READINGS_DIR = f"{SELF_BAZI_ROOT}/解读"
SELF_BAZI_HISTORY_DIR = f"{SELF_BAZI_ROOT}/历史"
SELF_ARCHIVE_LAYOUT_SCHEMA = "self-archive-v1"
SELF_ARCHIVE_LAYOUT_MARKER = ".torch-os/self-archive-layout.json"

# key, pillar, 显示名, 相对路径, 默认 type
CHAPTERS: tuple[tuple[str, str, str, str, str], ...] = (
    # 00_人生路
    ("journal", "life", "日记", "00_人生路/01_日记", "journal"),
    ("reflection", "life", "反思", "00_人生路/02_反思复盘/01_反思", "reflection"),
    ("insight_life", "life", "生活心得", "00_人生路/03_心得体会/01_生活心得", "insight"),
    ("insight_work", "life", "工作心得", "00_人生路/03_心得体会/02_工作心得", "insight"),
    ("insight_study", "life", "学习心得", "00_人生路/03_心得体会/03_学习心得", "insight"),
    ("review_week", "life", "周复盘", "00_人生路/02_反思复盘/02_周复盘", "weekly_review"),
    ("review_month", "life", "月复盘", "00_人生路/02_反思复盘/03_月复盘", "monthly_review"),
    ("goal_year", "life", "年度目标", "00_人生路/04_目标/01_年度目标", "goal"),
    ("goal_quarter", "life", "季度目标", "00_人生路/04_目标/02_季度目标", "goal"),
    ("goal_month", "life", "月度目标", "00_人生路/04_目标/03_月度目标", "goal"),
    ("habit", "life", "习惯追踪", "00_人生路/04_目标/04_习惯追踪", "habit_log"),
    ("project_plan", "life", "项目计划", "00_人生路/05_项目/01_项目计划", "plan"),
    ("work_review_project", "life", "项目复盘", "00_人生路/05_项目/02_项目复盘", "reflection"),
    ("work_review_meeting", "life", "会议收获", "00_人生路/06_工作复盘/02_会议收获", "insight"),
    ("work_review_pitfall", "life", "踩坑记录", "00_人生路/06_工作复盘/03_踩坑记录", "insight"),
    # v1.0 写作优先：随笔单独成章；任务/习惯仍在人生路，由工作台专用接口管理。
    ("essay", "life", "随笔", "00_人生路/09_随笔", "essay"),
    # 01_自我档案（旧章节：2026-11-01 重构后只做迁移/只读兜底，不再创建空目录）
    ("core_values", "self", "内核与三观", "01_自我档案/01_内核与三观", "value"),
    ("memory", "self", "记忆与自传", "01_自我档案/02_记忆与自传", "memory"),
    ("idea", "self", "想法与灵感", "01_自我档案/03_想法与灵感", "idea"),
    ("relationship", "self", "情感与关系", "01_自我档案/04_情感与关系", "relationship"),
    ("meaning", "self", "意义与目标", "01_自我档案/05_意义与目标", "meaning"),
    ("distill_work", "self", "工作方法沉淀", "01_自我档案/06_沉淀与创作/01_工作方法", "principle"),
    ("distill_career", "self", "职业原则", "01_自我档案/06_沉淀与创作/02_职业原则", "principle"),
    # 命理参考：个人命盘与长期自省用，独立成一个侧边栏分区
    ("bazi", "self", "我的八字参考", "01_自我档案/07_我的八字参考", "bazi"),
    # v1.0 忆灵观察：AI 画像快照与观察报告，只追加不覆盖；memory: false，避免再次进入候选池。
    ("yiling", "self", "忆灵观察", "01_自我档案/08_忆灵观察", "observation"),
    # 01_自我档案（v2，2026-11-01）：01_档案 下的 11 个固定本子。
    # 顺序就是左栏顺序；本子文件夹固定，不可在应用内删除/重命名。
    ("self_facts", "self", "基础事实", "01_自我档案/01_档案/01_基础事实", "self_archive_entry"),
    ("self_body", "self", "身体与能量", "01_自我档案/01_档案/02_身体与能量", "self_archive_entry"),
    ("self_behavior", "self", "行为与习惯", "01_自我档案/01_档案/03_行为与习惯", "self_archive_entry"),
    ("self_emotion", "self", "情绪与压力", "01_自我档案/01_档案/04_情绪与压力", "self_archive_entry"),
    ("self_motivation", "self", "动机与需求", "01_自我档案/01_档案/05_动机与需求", "self_archive_entry"),
    ("self_values", "self", "三观与决策", "01_自我档案/01_档案/06_三观与决策", "self_archive_entry"),
    ("self_relationship", "self", "关系与边界", "01_自我档案/01_档案/07_关系与边界", "self_archive_entry"),
    ("self_narrative", "self", "自我叙事", "01_自我档案/01_档案/08_自我叙事", "self_archive_entry"),
    ("self_paradox", "self", "矛盾与盲区", "01_自我档案/01_档案/09_矛盾与盲区", "self_archive_entry"),
    ("self_timeline", "self", "人生时间线", "01_自我档案/01_档案/10_人生时间线", "self_archive_entry"),
    ("self_experiments", "self", "验证与更新", "01_自我档案/01_档案/11_验证与更新", "self_archive_entry"),
    # 02_生活运营
    ("task", "ops", "待办事项", TASK_ROOT, "task"),
    # 健康系统（2026-10-31）：与恋爱系统平级的生活运营二级分区，数据全部在
    # `02_健康系统/`；旧 `01_健康与体重` 月度表只做只读兜底，不再写入。
    ("health", "ops", "健康系统", "02_生活运营/02_健康系统", "health_day"),
    ("health_daily", "ops", "每日健康", "02_生活运营/02_健康系统/01_每日健康", "health_day"),
    ("health_profile", "ops", "身体档案", "02_生活运营/02_健康系统/02_身体档案", "health_profile"),
    ("health_lab", "ops", "体检与检验", "02_生活运营/02_健康系统/03_体检与检验", "health_lab"),
    ("health_train", "ops", "训练记录", "02_生活运营/02_健康系统/04_训练记录", "health_train"),
    ("health_consult", "ops", "问诊记录", "02_生活运营/02_健康系统/05_问诊记录", "health_consult"),
    ("finance", "ops", "财富与记账", "02_生活运营/08_财富与记账", "finance_month"),
    # 2026-11-01：体检/医疗不再单独占一个生活运营顶层目录；检验记录归
    # `02_健康系统/03_体检与检验`，`medical` 章节退役。下列 03–06 是重排后的
    # 当前生活运营目录；`finance` 与 `love_memory` 是旧数据的只读兼容路径，
    # 不再创建空目录，也不参与前端导航。
    ("affairs", "ops", "证件与事务", "02_生活运营/03_证件与事务", "document"),
    # AI 确认后的每日聊天记忆（只写摘要，原文留在 .torch-os/love）
    ("love_memory", "ops", "情感与记忆", "02_生活运营/07_情感与记忆", "memory"),
    # v1.0 生活运营 AI 报告：专家角色生成的可读产物，落数据层；家庭是占位章节。
    ("health_report", "ops", "健康报告", "02_生活运营/02_健康系统/00_健康报告", "report"),
    ("finance_report", "ops", "财务报告", "02_生活运营/04_财务报告", "report"),
    ("life_advice", "ops", "生活建议", "02_生活运营/05_生活建议", "advice"),
    ("family", "ops", "家庭与智能家居", "02_生活运营/06_家庭与智能家居", "family"),
    # 03_知识库
    ("inbox", "knowledge", "收件箱", "03_知识库/00_收件箱", "inbox"),
    ("topic", "knowledge", "主题笔记", "03_知识库/01_主题笔记", "topic"),
    ("reading", "knowledge", "读书笔记", "03_知识库/02_读书笔记", "reading"),
    ("course", "knowledge", "课程笔记", "03_知识库/03_课程笔记", "course"),
    ("concept", "knowledge", "概念卡片", "03_知识库/04_概念卡片", "concept"),
    ("method_work", "knowledge", "工作方法", "03_知识库/05_方法库/01_工作方法", "method"),
    ("method_general", "knowledge", "通用方法", "03_知识库/05_方法库/02_通用方法", "method"),
    ("output", "knowledge", "输出与作品", "03_知识库/06_输出与作品", "output"),
    # 02_生活运营 / 01_恋爱系统：只给恋爱记忆写作域使用，不进入四库首页。
    (LOVE_JOURNAL_CHAPTER, LOVE_PILLAR, "恋爱日志", LOVE_JOURNAL_DIR, "love_memory"),
    (LOVE_DRAFT_CHAPTER, LOVE_PILLAR, "AI 草稿", LOVE_DRAFT_DIR, "love_memory"),
)

# 侧边栏分组：只影响展示，章节仍是清单里的唯一来源；没有分组的库给空列表。
# key, 显示名, 章节 key（引用 CHAPTERS，不复制路径）。
CHAPTER_GROUPS: dict[str, tuple[tuple[str, str, tuple[str, ...]], ...]] = {
    "life": (
        ("insight", "心得体会", ("insight_life", "insight_work", "insight_study")),
        ("review", "反思复盘", ("reflection", "review_week", "review_month")),
        ("goal", "目标", ("goal_year", "goal_quarter", "goal_month")),
        ("project", "项目", ("project_plan", "work_review_project")),
    ),
}

# 2026-10-30：自定义本子 = 某个库根目录下的一个文件夹。
# 人生路使用 `custom:<文件夹名>`（兼容已有 key）；知识库使用
# `custom:knowledge:<文件夹名>`，避免两个库出现同名本子时无法区分。
CUSTOM_NOTEBOOK_PREFIX = "custom:"
KNOWLEDGE_NOTEBOOK_PREFIX = f"{CUSTOM_NOTEBOOK_PREFIX}knowledge:"
CUSTOM_NOTEBOOK_MAX_NAME = 48
CUSTOM_NOTEBOOK_PILLARS = ("life", "knowledge", LOVE_PILLAR)
CUSTOM_NOTEBOOK_ROOT = PILLAR_DIRS["life"]
# 内置本子（首页卡片）→ 标题与该本子包含的章节 key。任务/习惯不是本子卡片。
BUILTIN_NOTEBOOKS: dict[str, dict[str, tuple[str, tuple[str, ...]]]] = {
    "life": {
        "journal": ("日记", ("journal",)),
        "reflection": ("反思复盘", ("reflection", "review_week", "review_month")),
        "insight": ("心得体会", ("insight_life", "insight_work", "insight_study")),
        "goal": ("目标", ("goal_year", "goal_quarter", "goal_month")),
        "project": ("项目", ("project_plan", "work_review_project")),
        "essay": ("随笔", ("essay",)),
        "work": ("工作复盘", ("work_review_meeting", "work_review_pitfall")),
    },
    "knowledge": {
        "inbox": ("收件箱", ("inbox",)),
        "topic": ("主题笔记", ("topic",)),
        "reading": ("读书笔记", ("reading",)),
        "course": ("课程笔记", ("course",)),
        "concept": ("概念卡片", ("concept",)),
        "method": ("方法库", ("method_work", "method_general")),
        "output": ("输出与作品", ("output",)),
    },
    "ops": {
        "task": ("待办事项", ("task",)),
    },
    "love": {
        "journal": ("恋爱日志", (LOVE_JOURNAL_CHAPTER,)),
        "drafts": ("AI 草稿", (LOVE_DRAFT_CHAPTER,)),
    },
}
# 用户明确保留的默认内置本子：人生路随笔、知识库收件箱、生活运营待办事项、
# 恋爱记忆的「恋爱日志」与「AI 草稿」。前三个是原有保护；恋爱记忆的两个
# 固定本子不参与删除，避免首页与本子列表对不上。
PROTECTED_NOTEBOOK_KEYS: dict[str, frozenset[str]] = {
    "life": frozenset({"essay"}),
    "knowledge": frozenset({"inbox"}),
    "ops": frozenset({"task"}),
    "love": frozenset({"journal", "drafts"}),
}
DELETED_NOTEBOOKS_REL = Path(".torch-os") / "deleted-notebooks.json"
# 固定章节仍然优先；不在固定一级目录里的直属文件夹才视为“我的本子”。
FIXED_TOP_DIRS = frozenset(item[3].split("/")[1] for item in CHAPTERS)
_NOTEBOOK_FORBIDDEN = re.compile(r'[<>:"/\\|?*#^\[\]\x00-\x1f]')
_NOTEBOOK_FOLDED_TOP_DIRS = {name.casefold() for name in FIXED_TOP_DIRS}
# 恋爱记忆的两个固定本子是目录，但不是“自定义本子”；列表和路径解析都要跳过。
LOVE_FIXED_NOTEBOOKS = frozenset({
    "恋爱日志".casefold(),
    "AI 草稿".casefold(),
})
# 自定义本子不能和任何内置本子/章节/库名同名，否则首页会出现两个「日记」。
RESERVED_NOTEBOOK_TITLES = frozenset(
    {item[2] for item in CHAPTERS}
    | {group[1] for groups in CHAPTER_GROUPS.values() for group in groups}
    | set(PILLAR_TITLES.values())
)
_NOTEBOOK_FOLDED_RESERVED_TITLES = {
    title.casefold() for title in RESERVED_NOTEBOOK_TITLES
}

# v1 → v2 章节搬迁（只给迁移预览/执行用）：旧目录下的文件按相对子路径搬到新章节，
# 未列出的旧目录（日记、任务、习惯、会议、踩坑…）保持原地不动。
V1_CHAPTER_MOVES: dict[str, str] = {
    "00_人生路/02_反思": "reflection",
    "00_人生路/04_周复盘": "review_week",
    "00_人生路/05_月复盘": "review_month",
    "00_人生路/06_目标与计划/01_年度目标": "goal_year",
    "00_人生路/06_目标与计划/02_季度目标": "goal_quarter",
    "00_人生路/06_目标与计划/03_月度目标": "goal_month",
    "00_人生路/06_目标与计划/04_习惯追踪": "habit",
    "00_人生路/06_目标与计划/05_项目计划": "project_plan",
    "00_人生路/07_工作复盘/01_项目复盘": "work_review_project",
    "00_人生路/06_工作复盘/01_项目复盘": "work_review_project",
}

CHAPTER_BY_KEY = {item[0]: item for item in CHAPTERS}
CHAPTER_BY_PATH = {item[3].casefold(): item for item in CHAPTERS}

# 只读兼容章节：旧库可能还有文件，运行时按旧路径读取；新库不再创建空目录。
# 2026-11-01 自我档案重构：旧 self 章节进入迁移/只读兜底，新前端使用 self_* 章节。
LEGACY_CHAPTER_KEYS = frozenset({
    "finance", "love_memory",
    "core_values", "memory", "idea", "relationship", "meaning",
    "distill_work", "distill_career", "bazi", "yiling",
})

# 2026-11-01 生活运营目录重排：取消独立的「03_体检与医疗」，它的内容并入
# `02_健康系统/03_体检与检验`；其余目录压紧编号。迁移只动顶层目录，逐个文件
# 冲突保留原文件，不覆盖。标记写 `.torch-os/ops-layout.json`，幂等可重跑。
OPS_ROOT = "02_生活运营"
OPS_LAYOUT_SCHEMA = "ops-layout-v2"
OPS_LAYOUT_MARKER = ".torch-os/ops-layout.json"
OPS_HEALTH_LAB = f"{OPS_ROOT}/02_健康系统/03_体检与检验"
OPS_LAYOUT_MOVES: tuple[tuple[str, str], ...] = (
    (f"{OPS_ROOT}/03_体检与医疗", OPS_HEALTH_LAB),
    (f"{OPS_ROOT}/04_证件与事务", f"{OPS_ROOT}/03_证件与事务"),
    (f"{OPS_ROOT}/07_财务报告", f"{OPS_ROOT}/04_财务报告"),
    (f"{OPS_ROOT}/08_生活建议", f"{OPS_ROOT}/05_生活建议"),
    (f"{OPS_ROOT}/09_家庭与智能家居", f"{OPS_ROOT}/06_家庭与智能家居"),
    # 旧只读章节也一并压紧编号；实际不存在时什么也不做。
    (f"{OPS_ROOT}/05_情感与记忆", f"{OPS_ROOT}/07_情感与记忆"),
    (f"{OPS_ROOT}/02_财富与记账", f"{OPS_ROOT}/08_财富与记账"),
)

# 旧布局 → 新章节（只在迁移预览里使用；运行时读取旧路径只做只读兜底）。
LEGACY_DIRS = {
    "02-life/journal": "00_人生路/01_日记",
    "01-identity": "01_自我档案/01_内核与三观",
    "90-inbox": "03_知识库/00_收件箱",
    "80-wolai": "03_知识库/00_收件箱/_导入",
    "提示词": "03_知识库/00_收件箱",
}

# 扫描/活动/审计共用的跳过目录（大小写不敏感）。
SKIP_DIRECTORIES = {
    ".obsidian", ".git", ".trash", ".claude", ".claudian", ".torch-os",
    "00-system", "99-templates", "templates", "relationship", "relationships",
    "relationship-vault", "relationship vault", "generated", "_附件",
    "_旧版", "_系统", "_模板", "回收站",
}
SKIP_FILES = {name.casefold() for name in FOREIGN_FILES}
MEMORY_PILLARS = {"self"}

_FORBIDDEN_STEM = re.compile(r'[<>:"/\\|?*#^\[\]\x00-\x1f]')
_RESERVED_STEMS = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def normalize_relpath(value: Any) -> str:
    text = unicodedata.normalize("NFC", str(value or "").replace("\\", "/")).strip()
    text = re.sub(r"/+", "/", text).strip("/")
    if not text or text.startswith("/") or ".." in text.split("/"):
        return ""
    return text


def chapter_manifest(*, hidden: set[str] | frozenset[str] = frozenset()) -> dict[str, Any]:
    """给 /api/pillars 的章节清单；前端不复制目录。

    `chapters` 保持 `CHAPTERS` 的原始顺序（未分组章节的位置不变），`groups` 只按
    key 引用章节，供侧边栏分组展示。`hidden` 是已删除内置本子的章节 key，
    这些章节以及被删空的分组不会出现在清单里。
    """
    hidden_keys = set(hidden or ())
    pillars: list[dict[str, Any]] = []
    for key in PILLAR_ORDER:
        chapters = [
            {"key": item[0], "title": item[2], "path": item[3], "default_type": item[4]}
            for item in CHAPTERS if item[1] == key and item[0] not in hidden_keys
        ]
        groups = []
        for group_key, group_title, group_chapters in CHAPTER_GROUPS.get(key, ()):
            visible = [chapter for chapter in group_chapters if chapter not in hidden_keys]
            if visible:
                groups.append({"key": group_key, "title": group_title,
                               "chapters": visible})
        pillars.append({
            "key": key,
            "title": PILLAR_TITLES[key],
            "dir": PILLAR_DIRS[key],
            "chapters": chapters,
            "groups": groups,
        })
    return {"layout_version": LAYOUT_VERSION, "pillars": pillars}


def pillar_of(relpath: str) -> str:
    text = normalize_relpath(relpath)
    # 恋爱记忆在 02_生活运营 下，必须先于 ops 匹配，否则会被误判成生活运营。
    if text == LOVE_MEMORY_DIR or text.startswith(LOVE_MEMORY_DIR + "/"):
        return LOVE_PILLAR
    for key, directory in PILLAR_DIRS.items():
        if text == directory or text.startswith(directory + "/"):
            return key
    return ""


def notebook_root(pillar: str) -> str:
    """自定义本子所在根目录；love 使用恋爱记忆目录，其余用四库根目录。"""
    key = str(pillar or "")
    if key == LOVE_PILLAR:
        return LOVE_MEMORY_DIR
    return PILLAR_DIRS.get(key, "")


def chapter_key_of(relpath: str) -> str:
    normalized = normalize_relpath(relpath)
    text = normalized.casefold()
    for path, item in sorted(CHAPTER_BY_PATH.items(), key=lambda pair: -len(pair[0])):
        if text == path or text.startswith(path + "/"):
            return item[0]
    custom = custom_notebook_name_from_relpath(normalized)
    pillar = custom_notebook_pillar_of(relpath)
    return custom_notebook_key(custom, pillar) if custom and pillar else ""


def chapter_dir(chapter_key: str) -> str:
    value = str(chapter_key or "").strip()
    pillar = custom_notebook_pillar(value)
    if pillar:
        name = custom_notebook_name(value)
        try:
            return custom_notebook_path(name, pillar)
        except ValueError:
            return ""
    item = CHAPTER_BY_KEY.get(value)
    return item[3] if item else ""


def chapter_default_type(chapter_key: str) -> str:
    if is_custom_notebook_key(chapter_key):
        return "note"
    item = CHAPTER_BY_KEY.get(chapter_key)
    return item[4] if item else "note"


def validate_notebook_name(value: Any) -> str:
    """校验“本子=文件夹”的名称；返回 NFC 规范化后的安全文件夹名。"""
    text = unicodedata.normalize("NFC", str(value or "")).strip()
    if not text:
        raise ValueError("本子名称不能为空")
    if len(text) > CUSTOM_NOTEBOOK_MAX_NAME:
        raise ValueError(f"本子名称最多 {CUSTOM_NOTEBOOK_MAX_NAME} 个字符")
    if text in {".", ".."} or text.startswith((".", "_")) or text.endswith((".", " ")):
        raise ValueError("本子名称不能以 . 或 _ 开头，也不能以点/空格结尾")
    if _NOTEBOOK_FORBIDDEN.search(text):
        raise ValueError("本子名称不能包含 < > : \" / \\ | ? * # ^ [ ] 等字符")
    folded = text.casefold()
    if folded in _NOTEBOOK_FOLDED_TOP_DIRS:
        raise ValueError("这个名称是固定章节目录，请换一个")
    if folded in _NOTEBOOK_FOLDED_RESERVED_TITLES:
        raise ValueError("这个名称和内置本子/章节重名，请换一个")
    if folded in SKIP_DIRECTORIES:
        raise ValueError("这个名称是系统保留目录，请换一个")
    if text.split(".")[0].upper() in _RESERVED_STEMS:
        raise ValueError("这个名称是系统保留名，请换一个")
    return text


def custom_notebook_key(name: str, pillar: str = "life") -> str:
    """人生路沿用 `custom:<名字>`；知识库/恋爱记忆带库前缀。"""
    clean = validate_notebook_name(name)
    if pillar == "knowledge":
        return f"{KNOWLEDGE_NOTEBOOK_PREFIX}{clean}"
    if pillar == LOVE_PILLAR:
        return f"{LOVE_NOTEBOOK_PREFIX}{clean}"
    return f"{CUSTOM_NOTEBOOK_PREFIX}{clean}"


def custom_notebook_pillar(chapter_key: str) -> str:
    """自定义本子 key → life/knowledge/love；不是合法本子 key 返回空串。"""
    value = str(chapter_key or "").strip()
    if value.startswith(KNOWLEDGE_NOTEBOOK_PREFIX):
        try:
            validate_notebook_name(value[len(KNOWLEDGE_NOTEBOOK_PREFIX):])
            return "knowledge"
        except ValueError:
            return ""
    if value.startswith(LOVE_NOTEBOOK_PREFIX):
        try:
            validate_notebook_name(value[len(LOVE_NOTEBOOK_PREFIX):])
            return LOVE_PILLAR
        except ValueError:
            return ""
    if value.startswith(CUSTOM_NOTEBOOK_PREFIX):
        try:
            validate_notebook_name(value[len(CUSTOM_NOTEBOOK_PREFIX):])
            return "life"
        except ValueError:
            return ""
    return ""


def custom_notebook_name(chapter_key: str) -> str:
    pillar = custom_notebook_pillar(chapter_key)
    if not pillar:
        return ""
    prefix = {
        "knowledge": KNOWLEDGE_NOTEBOOK_PREFIX,
        LOVE_PILLAR: LOVE_NOTEBOOK_PREFIX,
    }.get(pillar, CUSTOM_NOTEBOOK_PREFIX)
    return str(chapter_key).strip()[len(prefix):]


def is_custom_notebook_key(chapter_key: str) -> bool:
    return bool(custom_notebook_pillar(chapter_key))


def custom_notebook_path(name: str, pillar: str = "life") -> str:
    root = notebook_root(pillar)
    if not root:
        raise ValueError("只有人生路、知识库和恋爱记忆支持自定义本子")
    return f"{root}/{validate_notebook_name(name)}"


def custom_notebook_pillar_of(relpath: str) -> str:
    """路径属于哪个库；只有支持自定义本子的库会返回 key 用的库。"""
    pillar = pillar_of(relpath)
    return pillar if pillar in CUSTOM_NOTEBOOK_PILLARS else ""


def custom_notebook_name_from_relpath(relpath: str) -> str:
    """<库根>/<自定义文件夹>/<笔记>.md → 文件夹名；库根直属文件与固定目录返回空串。"""
    text = normalize_relpath(relpath)
    pillar = custom_notebook_pillar_of(text)
    if not pillar:
        return ""
    root = notebook_root(pillar)
    root_parts = root.split("/") if root else []
    parts = text.split("/")
    # 至少要有「库根 / 本子文件夹 / 笔记」；恋爱记忆的库根有三段。
    if not root_parts or len(parts) < len(root_parts) + 2:
        return ""
    if parts[:len(root_parts)] != root_parts:
        return ""
    name = parts[len(root_parts)]
    if name.casefold() in _NOTEBOOK_FOLDED_TOP_DIRS:
        return ""
    if pillar == LOVE_PILLAR and name.casefold() in LOVE_FIXED_NOTEBOOKS:
        return ""
    if name.startswith((".", "_")) or name.casefold() in SKIP_DIRECTORIES:
        return ""
    try:
        return validate_notebook_name(name)
    except ValueError:
        return ""


def list_custom_notebooks(vault_dir: Path | str,
                          pillar: str = "life") -> list[str]:
    """列出某个库根目录下的自定义本子文件夹（不跟符号链接）。"""
    if pillar not in CUSTOM_NOTEBOOK_PILLARS:
        return []
    root_relative = notebook_root(pillar)
    if not root_relative:
        return []
    root = Path(vault_dir) / root_relative
    try:
        entries = list(root.iterdir())
    except OSError:
        return []
    names: list[str] = []
    for entry in entries:
        name = entry.name
        if name.casefold() in _NOTEBOOK_FOLDED_TOP_DIRS:
            continue
        if pillar == LOVE_PILLAR and name.casefold() in LOVE_FIXED_NOTEBOOKS:
            continue
        if name.startswith((".", "_")) or name.casefold() in SKIP_DIRECTORIES:
            continue
        try:
            info = entry.lstat()
        except OSError:
            continue
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            continue
        if getattr(info, "st_file_attributes", 0) & getattr(
                stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            continue
        try:
            names.append(validate_notebook_name(name))
        except ValueError:
            continue
    return sorted(names, key=str.casefold)


def builtin_notebook_title(pillar: str, key: str) -> str:
    item = BUILTIN_NOTEBOOKS.get(str(pillar or ""), {}).get(str(key or ""))
    return item[0] if item else ""


def builtin_notebook_chapters(pillar: str, key: str) -> tuple[str, ...]:
    item = BUILTIN_NOTEBOOKS.get(str(pillar or ""), {}).get(str(key or ""))
    return item[1] if item else ()


def is_protected_notebook(pillar: str, key: str) -> bool:
    return str(key or "") in PROTECTED_NOTEBOOK_KEYS.get(str(pillar or ""), frozenset())


def can_delete_builtin_notebook(pillar: str, key: str) -> bool:
    value = str(key or "")
    return (value in BUILTIN_NOTEBOOKS.get(str(pillar or ""), {})
            and not is_protected_notebook(pillar, value))


def _deleted_notebooks_file(vault_dir: Path | str) -> Path:
    return Path(vault_dir).resolve() / DELETED_NOTEBOOKS_REL


def deleted_notebook_keys(vault_dir: Path | str) -> dict[str, set[str]]:
    """读取被删除的内置本子 key；损坏/缺失时按“没有删除”处理。"""
    result = {pillar: set() for pillar in BUILTIN_NOTEBOOKS}
    try:
        payload = json.loads(_deleted_notebooks_file(vault_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return result
    if not isinstance(payload, dict):
        return result
    for pillar, notebooks in BUILTIN_NOTEBOOKS.items():
        values = payload.get(pillar)
        if not isinstance(values, list):
            continue
        for key in values:
            if (isinstance(key, str) and key in notebooks
                    and not is_protected_notebook(pillar, key)):
                result[pillar].add(key)
    return result


def _write_deleted_notebook_keys(vault_dir: Path | str,
                                 deleted: dict[str, set[str]]) -> None:
    root = Path(vault_dir).resolve()
    path = root / DELETED_NOTEBOOKS_REL
    if not any(deleted.values()) and not path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": 1,
        **{pillar: sorted(deleted.get(pillar, set())) for pillar in BUILTIN_NOTEBOOKS},
    }
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def mark_notebook_deleted(vault_dir: Path | str, pillar: str, key: str,
                          *, deleted: bool = True) -> None:
    """记录/撤销一个内置本子的删除标记；随笔和收件箱永远不能标记。"""
    pillar_key = str(pillar or "")
    notebook_key = str(key or "")
    if notebook_key not in BUILTIN_NOTEBOOKS.get(pillar_key, {}):
        raise ValueError("不是内置本子")
    if deleted and is_protected_notebook(pillar_key, notebook_key):
        raise ValueError("这个默认本子不能删除")
    current = deleted_notebook_keys(vault_dir)
    if deleted:
        current[pillar_key].add(notebook_key)
    else:
        current[pillar_key].discard(notebook_key)
    _write_deleted_notebook_keys(vault_dir, current)


def hidden_chapters(vault_dir: Path | str) -> set[str]:
    """被删除的内置本子包含的章节 key；这些章节不再出现在首页/编辑器/写入校验里。"""
    hidden: set[str] = set()
    for pillar, keys in deleted_notebook_keys(vault_dir).items():
        for key in keys:
            hidden.update(builtin_notebook_chapters(pillar, key))
    return hidden


def _unhide_restored_notebooks(vault_dir: Path | str) -> None:
    """手动把章节文件夹从回收站放回后，下一次启动自动恢复本子显示。"""
    deleted = deleted_notebook_keys(vault_dir)
    root = Path(vault_dir)
    changed = False
    for pillar, keys in deleted.items():
        for key in list(keys):
            chapters = builtin_notebook_chapters(pillar, key)
            if not any((root / chapter_dir(chapter)).is_dir() for chapter in chapters):
                continue
            keys.discard(key)
            changed = True
    if not changed:
        return
    try:
        _write_deleted_notebook_keys(vault_dir, deleted)
    except OSError:
        pass


def is_notebook_deleted(vault_dir: Path | str, pillar: str, key: str) -> bool:
    return str(key or "") in deleted_notebook_keys(vault_dir).get(str(pillar or ""), set())


def is_chapter_hidden(vault_dir: Path | str, chapter_key: str) -> bool:
    value = str(chapter_key or "").strip()
    return bool(value) and value in hidden_chapters(vault_dir)


def v1_chapter_move(relpath: str) -> tuple[str, str] | None:
    """v1 章节里的文件 → `(v2 目标路径, 章节 key)`；不在搬迁目录里返回 None。

    只给迁移用：路径不再变化的旧目录（日记、任务、习惯、会议、踩坑…）不在此表，
    调用方按“已在四库、原地保留”处理。
    """
    text = normalize_relpath(relpath)
    if not text:
        return None
    folded = text.casefold()
    # 长目录优先，避免 `06_目标与计划/01_年度目标` 被更短的父目录抢先匹配。
    for source, chapter in sorted(V1_CHAPTER_MOVES.items(), key=lambda pair: -len(pair[0])):
        prefix = source.casefold()
        if not folded.startswith(prefix + "/"):
            continue
        rest = text[len(source) + 1:]
        if not rest:
            continue
        target = f"{chapter_dir(chapter)}/{rest}"
        return normalize_relpath(target), chapter
    return None


def is_foreign(relpath: str, metadata: dict[str, Any] | None = None) -> bool:
    text = normalize_relpath(relpath)
    name = text.rsplit("/", 1)[-1].casefold()
    if name in SKIP_FILES:
        return True
    data = metadata or {}
    return data.get("dashboard") is True or str(data.get("layout") or "") in {"immersive", "side"}


def is_skipped(relpath: str) -> bool:
    text = normalize_relpath(relpath)
    if not text:
        return True
    parts = text.split("/")
    if any(part.startswith(".") or part.casefold() in SKIP_DIRECTORIES for part in parts[:-1]):
        return True
    return parts[-1].startswith(".")


def index_policy(relpath: str, metadata: dict[str, Any] | None = None) -> str:
    """skip / document_only / memory_eligible，三档索引策略。"""
    text = normalize_relpath(relpath)
    if is_skipped(text) or is_foreign(text, metadata):
        return "skip"
    data = metadata or {}
    if data.get("memory") is True:
        return "memory_eligible"
    pillar = pillar_of(text)
    if pillar in MEMORY_PILLARS:
        return "memory_eligible"
    if pillar:
        return "document_only"
    # 旧布局的根级笔记：迁移前保持原有“可生成记忆候选”的行为，避免升级即丢候选。
    # 日记与导出快照明确不做记忆提取。
    legacy = text.casefold()
    if legacy.startswith("02-life/journal/"):
        return "document_only"
    return "memory_eligible"


def safe_note_stem(title: str, *, fallback: str = "未命名") -> str:
    text = unicodedata.normalize("NFC", str(title or ""))
    text = _FORBIDDEN_STEM.sub("-", text)
    text = re.sub(r"\s+", "_", text).strip(" .-_")
    text = text[:96].strip(" .-_")
    if not text:
        text = fallback
    if text.split(".")[0].upper() in _RESERVED_STEMS:
        text = f"笔记-{text}"
    return text


def validate_target(relpath: str) -> str:
    """只允许写进四库章节；任务章节由工作台专用接口管理，旧 00-System 不再写入。"""
    text = normalize_relpath(relpath)
    if not text or not pillar_of(text):
        raise ValueError("目标必须是四库章节内的路径")
    if text.split("/")[0].casefold() == SYSTEM_DIR.casefold() or is_foreign(text):
        raise ValueError("不能写入系统目录或外部资产")
    return text


def journal_path(day: str) -> str:
    return f"00_人生路/01_日记/{day}.md"


def identity_paths() -> tuple[str, str]:
    """身份文件的新旧路径：新路径在 01_档案 下，旧路径只读兜底。"""
    current = SELF_IDENTITY_RELATIVE
    legacy = "01_自我档案/01_内核与三观/身份（identity）.md"
    return current, legacy


def legacy_identity_path() -> str:
    return "01-Identity/身份（identity）.md"


def legacy_journal_path(day: str) -> str:
    return f"02-Life/journal/{day}.md"


def inbox_dir() -> str:
    return chapter_dir("inbox")


def _ops_marker_path(root: Path) -> Path:
    return root / OPS_LAYOUT_MARKER


def _read_ops_marker(root: Path) -> dict[str, Any]:
    path = _ops_marker_path(root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _ops_sources(root: Path) -> list[str]:
    return [source for source, _target in OPS_LAYOUT_MOVES if (root / source).exists()]


def _same_file(left: Path, right: Path) -> bool:
    try:
        if left.stat().st_size != right.stat().st_size:
            return False
        return left.read_bytes() == right.read_bytes()
    except OSError:
        return False


def _merge_ops_tree(source: Path, target: Path, actions: list[str],
                    conflicts: list[str]) -> None:
    """把 source 合并进 target；同名但内容不同的文件留在 source，绝不覆盖。"""
    if not source.exists():
        return
    if source.is_symlink():
        conflicts.append(f"{source}:符号链接不处理")
        return
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)
        actions.append(f"{source.name} → {target.parent.name}/{target.name}")
        return
    if source.is_file():
        if target.is_file() and _same_file(source, target):
            source.unlink()
            return
        conflicts.append(f"{source}:目标已存在且内容不同")
        return
    if not source.is_dir() or not target.is_dir():
        conflicts.append(f"{source}:源与目标类型不同")
        return
    for child in sorted(source.iterdir(), key=lambda item: item.name):
        destination = target / child.name
        if child.is_symlink():
            conflicts.append(f"{child}:符号链接不处理")
            continue
        if not destination.exists():
            child.rename(destination)
            continue
        if child.is_dir() and destination.is_dir():
            _merge_ops_tree(child, destination, actions, conflicts)
            continue
        if child.is_file() and destination.is_file() and _same_file(child, destination):
            child.unlink()
            continue
        conflicts.append(f"{child}:目标已存在且内容不同")
    try:
        if not any(source.iterdir()):
            source.rmdir()
    except OSError as error:
        conflicts.append(f"{source}:删除空目录失败（{error}）")


def _backup_ops_sources(root: Path, sources: list[str], stamp: str) -> str:
    backup_root = root / ".torch-os" / "backups" / "ops-layout" / stamp
    for source in sources:
        path = root / source
        if not path.is_dir():
            continue
        destination = backup_root / source
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copytree(path, destination, dirs_exist_ok=True)
        except OSError:
            # 备份失败不阻断搬移；标记里会记录失败，源目录仍然保留在回收站/原处。
            continue
    return backup_root.relative_to(root).as_posix()


def plan_ops_layout(vault_dir: Path | str) -> dict[str, Any]:
    root = Path(vault_dir)
    sources = _ops_sources(root)
    actions = []
    for source, target in OPS_LAYOUT_MOVES:
        if (root / source).exists():
            actions.append(f"{source} → {target}")
    return {"schema": OPS_LAYOUT_SCHEMA, "sources": sources, "actions": actions,
            "count": len(actions), "marker": _read_ops_marker(root)}


def ensure_ops_layout(vault_dir: Path | str, *, database: Any = None) -> dict[str, Any]:
    """把生活运营的旧顶层目录压紧编号，并把体检/医疗并入健康系统。

    幂等：标记文件存在且没有旧目录时直接返回 already_done；只移动顶层目录，
    同名冲突保留原文件并写进报告。只应在可写实例启动时调用。
    """
    root = Path(vault_dir).resolve()
    if not root.is_dir():
        return {"schema": OPS_LAYOUT_SCHEMA, "status": "skipped", "reason": "vault_missing",
                "moved": 0}
    sources = _ops_sources(root)
    marker = _read_ops_marker(root)
    if marker.get("schema") == OPS_LAYOUT_SCHEMA and not sources:
        return {**marker, "status": "done", "already_done": True, "moved": 0}
    if not sources:
        payload = {"schema": OPS_LAYOUT_SCHEMA, "status": "done", "already_done": True,
                   "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "moved": 0, "actions": [], "conflicts": [], "backup": ""}
        _write_ops_marker(root, payload)
        return payload

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = _backup_ops_sources(root, sources, stamp)
    actions: list[str] = []
    conflicts: list[str] = []
    for source, target in OPS_LAYOUT_MOVES:
        source_path = root / source
        if source_path.exists():
            _merge_ops_tree(source_path, root / target, actions, conflicts)
    payload = {
        "schema": OPS_LAYOUT_SCHEMA, "status": "done" if not conflicts else "partial",
        "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "moved": len(actions), "actions": actions, "conflicts": conflicts,
        "backup": backup,
    }
    _write_ops_marker(root, payload)
    if database is not None:
        try:
            database.audit("ops_layout_migrated", {
                "moved": len(actions), "conflicts": len(conflicts), "backup": backup})
        except Exception:
            pass
    return payload


def _write_ops_marker(root: Path, payload: dict[str, Any]) -> None:
    path = _ops_marker_path(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        pass


def ensure_self_layout(vault_dir: Path | str) -> list[str]:
    """幂等创建自我档案三部分所需的固定目录（2026-11-01 重构）。

    本子文件夹是前端左栏与数据目录的一一对应；应用内不允许删除/重命名。
    """
    root = Path(vault_dir)
    relative_dirs = [
        SELF_ARCHIVE_ROOT,
        SELF_MENTOR_ROOT,
        SELF_MENTOR_MEMORY_DIR,
        SELF_MENTOR_PENDING_DIR,
        SELF_MENTOR_PENDING_PATCHES,
        SELF_MENTOR_PENDING_ACCEPTED,
        SELF_MENTOR_PENDING_REJECTED,
        SELF_MENTOR_QUESTIONS_DIR,
        SELF_MENTOR_QUESTIONS_HISTORY,
        SELF_MENTOR_HISTORY_DIR,
        SELF_MENTOR_PORTRAIT_HISTORY,
        SELF_MENTOR_OLD_OBSERVATIONS,
        SELF_BAZI_ROOT,
        SELF_BAZI_READINGS_DIR,
        SELF_BAZI_HISTORY_DIR,
        *[item[3] for item in CHAPTERS if item[1] == "self" and item[0].startswith("self_")],
    ]
    created: list[str] = []
    for relative in relative_dirs:
        path = root / relative
        if not path.is_dir():
            path.mkdir(parents=True, exist_ok=True)
            created.append(relative)
    return created


def ensure_layout(vault_dir: Path | str) -> list[str]:
    """幂等创建四库和章节目录；返回实际创建的相对路径。

    已删除的内置本子不再重建章节目录，否则重启后空卡片会重新出现。
    """
    root = Path(vault_dir)
    _unhide_restored_notebooks(root)
    hidden = hidden_chapters(root)
    created: list[str] = []
    for relpath in [PILLAR_DIRS[key] for key in PILLAR_ORDER]:
        path = root / relpath
        if not path.is_dir():
            path.mkdir(parents=True, exist_ok=True)
            created.append(relpath)
    for item in CHAPTERS:
        if item[0] in hidden or item[0] in LEGACY_CHAPTER_KEYS:
            continue
        path = root / item[3]
        if not path.is_dir():
            path.mkdir(parents=True, exist_ok=True)
            created.append(item[3])
    for relative in ensure_self_layout(root):
        if relative not in created:
            created.append(relative)
    for relative in TASK_SPECIAL_DIRS:
        path = root / relative
        if not path.is_dir():
            path.mkdir(parents=True, exist_ok=True)
            created.append(relative)
    return created


def _has_files(path: Path) -> bool:
    if not path.is_dir():
        return False
    try:
        return any(item.is_file() for item in path.rglob("*"))
    except OSError:
        return True


def layout_status(vault_dir: Path | str) -> dict[str, Any]:
    root = Path(vault_dir)
    pillars = {key: (root / directory).is_dir() for key, directory in PILLAR_DIRS.items()}
    # 空目录不算“混合布局”：迁移后旧目录会保留，但内容已经搬走。
    legacy = {
        "journal": _has_files(root / "02-Life" / "journal"),
        "identity": _has_files(root / "01-Identity"),
        "inbox": _has_files(root / "90-Inbox"),
    }
    count = sum(1 for value in pillars.values() if value)
    if count == len(PILLAR_DIRS) and not any(legacy.values()):
        state = "v1"
    elif count == 0 and any(legacy.values()):
        state = "legacy"
    elif not any(pillars.values()):
        state = "empty"
    else:
        state = "mixed"
    return {"layout_version": LAYOUT_VERSION, "state": state,
            "pillars": pillars, "legacy": legacy}


__all__ = [
    "LAYOUT_VERSION", "SYSTEM_DIR", "FOREIGN_FILES", "PILLAR_TITLES",
    "TASK_ROOT", "TASK_INBOX_DIR", "TASK_DONE_DIR", "TASK_TRASH_DIR",
    "TASK_SYSTEM_DIR", "TASK_SPECIAL_DIRS",
    "SELF_ROOT", "SELF_ARCHIVE_ROOT", "SELF_MENTOR_ROOT", "SELF_BAZI_ROOT",
    "SELF_IDENTITY_RELATIVE", "SELF_PORTRAIT_RELATIVE", "SELF_BACKUP_PREFIX",
    "SELF_MENTOR_MEMORY_DIR", "SELF_MENTOR_PENDING_DIR",
    "SELF_MENTOR_PENDING_PATCHES", "SELF_MENTOR_PENDING_ACCEPTED",
    "SELF_MENTOR_PENDING_REJECTED", "SELF_MENTOR_QUESTIONS_DIR",
    "SELF_MENTOR_QUESTIONS_HISTORY", "SELF_MENTOR_HISTORY_DIR",
    "SELF_MENTOR_PORTRAIT_HISTORY", "SELF_MENTOR_OLD_OBSERVATIONS",
    "SELF_BAZI_READINGS_DIR", "SELF_BAZI_HISTORY_DIR",
    "SELF_ARCHIVE_LAYOUT_SCHEMA", "SELF_ARCHIVE_LAYOUT_MARKER",
    "PILLAR_DIRS", "PILLAR_ORDER", "LOVE_PILLAR", "LOVE_ROOT", "LOVE_MEMORY_DIR",
    "LOVE_JOURNAL_DIR", "LOVE_DRAFT_DIR", "LOVE_JOURNAL_CHAPTER",
    "LOVE_DRAFT_CHAPTER", "LOVE_NOTEBOOK_PREFIX", "LOVE_FIXED_NOTEBOOKS",
    "notebook_root", "CHAPTERS", "CHAPTER_BY_KEY", "CHAPTER_BY_PATH",
    "LEGACY_CHAPTER_KEYS", "OPS_ROOT", "OPS_LAYOUT_SCHEMA", "OPS_LAYOUT_MARKER",
    "OPS_HEALTH_LAB", "OPS_LAYOUT_MOVES", "CHAPTER_GROUPS", "V1_CHAPTER_MOVES",
    "LEGACY_DIRS", "normalize_relpath",
    "chapter_manifest", "pillar_of", "chapter_key_of", "chapter_dir",
    "chapter_default_type", "v1_chapter_move", "is_foreign",
    "is_skipped", "index_policy", "safe_note_stem", "validate_target",
    "journal_path", "identity_paths", "legacy_identity_path", "legacy_journal_path",
    "inbox_dir", "ensure_layout", "layout_status",
    "plan_ops_layout", "ensure_ops_layout",
]
