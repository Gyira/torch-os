"""四库布局的唯一路径与索引策略来源（2026-10-26 人生路 v2）。

- 四个用户库：00_人生路 / 01_自我档案 / 02_生活运营 / 03_知识库；
- 人生路 v2 把反思复盘、目标、项目收进分组目录（`02_反思复盘`、`04_目标`、`05_项目`），
  侧边栏分组见 `CHAPTER_GROUPS`；v1 的旧章节目录只由迁移搬到新路径
  （见 `V1_CHAPTER_MOVES`），运行时不再写入；
- 工作台任务与待办在 `00_人生路/08_任务与待办`；旧 00-System 只做只读兜底；
- 旧布局（02-Life/journal、01-Identity、90-Inbox…）只做只读兜底，迁移后不再写入。

所有路径都以创建目录（Vault 根）为基准，使用 POSIX 相对路径字符串，
避免在 Windows / Obsidian / 测试之间出现分隔符差异。
"""

from __future__ import annotations

import re
import stat
import unicodedata
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
}
PILLAR_DIRS = {
    "life": "00_人生路",
    "self": "01_自我档案",
    "ops": "02_生活运营",
    "knowledge": "03_知识库",
}
PILLAR_ORDER = ("life", "self", "ops", "knowledge")

# key, pillar, 显示名, 相对路径, 默认 type
CHAPTERS: tuple[tuple[str, str, str, str, str], ...] = (
    # 00_人生路
    ("journal", "life", "日记", "00_人生路/01_日记", "journal"),
    ("task", "life", "任务与待办", "00_人生路/08_任务与待办", "task"),
    ("reflection", "life", "反思", "00_人生路/02_反思复盘/01_反思", "reflection"),
    ("insight_life", "life", "生活心得", "00_人生路/03_心得体会/01_生活心得", "insight"),
    ("insight_work", "life", "工作心得", "00_人生路/03_心得体会/02_工作心得", "insight"),
    ("insight_study", "life", "学习心得", "00_人生路/03_心得体会/03_学习心得", "insight"),
    ("review_week", "life", "周复盘", "00_人生路/02_反思复盘/02_周复盘", "weekly_review"),
    ("review_month", "life", "月复盘", "00_人生路/02_反思复盘/03_月复盘", "monthly_review"),
    ("goal_year", "life", "年度目标", "00_人生路/04_目标/01_年度目标", "goal"),
    ("goal_quarter", "life", "季度目标", "00_人生路/04_目标/02_季度目标", "goal"),
    ("goal_month", "life", "月度目标", "00_人生路/04_目标/03_月度目标", "goal"),
    ("habit", "life", "习惯追踪", "00_人生路/06_目标与计划/04_习惯追踪", "habit_log"),
    ("project_plan", "life", "项目计划", "00_人生路/05_项目/01_项目计划", "plan"),
    ("work_review_project", "life", "项目复盘", "00_人生路/05_项目/02_项目复盘", "reflection"),
    ("work_review_meeting", "life", "会议收获", "00_人生路/07_工作复盘/02_会议收获", "insight"),
    ("work_review_pitfall", "life", "踩坑记录", "00_人生路/07_工作复盘/03_踩坑记录", "insight"),
    # v1.0 写作优先：随笔单独成章；任务/习惯仍在人生路，由工作台专用接口管理。
    ("essay", "life", "随笔", "00_人生路/09_随笔", "essay"),
    # 01_自我档案
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
    # 02_生活运营
    ("health", "ops", "健康与体重", "02_生活运营/01_健康与体重", "health_month"),
    ("finance", "ops", "财富与记账", "02_生活运营/02_财富与记账", "finance_month"),
    ("medical", "ops", "体检与医疗", "02_生活运营/03_体检与医疗", "medical"),
    ("affairs", "ops", "证件与事务", "02_生活运营/04_证件与事务", "document"),
    # AI 确认后的每日聊天记忆（只写摘要，原文留在 .torch-os/love）
    ("love_memory", "ops", "情感与记忆", "02_生活运营/05_情感与记忆", "memory"),
    # v1.0 生活运营 AI 报告：专家角色生成的可读产物，落数据层；家庭是占位章节。
    ("health_report", "ops", "健康报告", "02_生活运营/06_健康报告", "report"),
    ("finance_report", "ops", "财务报告", "02_生活运营/07_财务报告", "report"),
    ("life_advice", "ops", "生活建议", "02_生活运营/08_生活建议", "advice"),
    ("family", "ops", "家庭与智能家居", "02_生活运营/09_家庭与智能家居", "family"),
    # 03_知识库
    ("inbox", "knowledge", "收件箱", "03_知识库/00_收件箱", "inbox"),
    ("topic", "knowledge", "主题笔记", "03_知识库/01_主题笔记", "topic"),
    ("reading", "knowledge", "读书笔记", "03_知识库/02_读书笔记", "reading"),
    ("course", "knowledge", "课程笔记", "03_知识库/03_课程笔记", "course"),
    ("concept", "knowledge", "概念卡片", "03_知识库/04_概念卡片", "concept"),
    ("method_work", "knowledge", "工作方法", "03_知识库/05_方法库/01_工作方法", "method"),
    ("method_general", "knowledge", "通用方法", "03_知识库/05_方法库/02_通用方法", "method"),
    ("output", "knowledge", "输出与作品", "03_知识库/06_输出与作品", "output"),
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

# 2026-10-30：人生路自定义本子 = 00_人生路 下的一个文件夹。
# 固定章节仍然优先；不在固定一级目录里的直属文件夹都视为“我的本子”。
# key 使用 `custom:<文件夹名>`，避免和固定章节 key 冲突。
CUSTOM_NOTEBOOK_PREFIX = "custom:"
CUSTOM_NOTEBOOK_MAX_NAME = 48
CUSTOM_NOTEBOOK_ROOT = PILLAR_DIRS["life"]
LIFE_TOP_DIRS = frozenset(
    item[3].split("/")[1] for item in CHAPTERS if item[1] == "life"
)
_NOTEBOOK_FORBIDDEN = re.compile(r'[<>:"/\\|?*#^\[\]\x00-\x1f]')
_NOTEBOOK_FOLDED_TOP_DIRS = {name.casefold() for name in LIFE_TOP_DIRS}
# 自定义本子不能和内置本子/章节同名，否则首页会出现两个「日记」。
LIFE_RESERVED_NOTEBOOK_TITLES = frozenset(
    {item[2] for item in CHAPTERS if item[1] == "life"}
    | {group[1] for group in CHAPTER_GROUPS.get("life", ())}
)
_NOTEBOOK_FOLDED_RESERVED_TITLES = {
    title.casefold() for title in LIFE_RESERVED_NOTEBOOK_TITLES
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
    "00_人生路/06_目标与计划/05_项目计划": "project_plan",
    "00_人生路/07_工作复盘/01_项目复盘": "work_review_project",
}

CHAPTER_BY_KEY = {item[0]: item for item in CHAPTERS}
CHAPTER_BY_PATH = {item[3].casefold(): item for item in CHAPTERS}

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
    "_旧版", "_系统", "_模板",
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


def chapter_manifest() -> dict[str, Any]:
    """给 /api/pillars 的章节清单；前端不复制目录。

    `chapters` 保持 `CHAPTERS` 的原始顺序（未分组章节的位置不变），`groups` 只按
    key 引用章节，供侧边栏分组展示。
    """
    pillars: list[dict[str, Any]] = []
    for key in PILLAR_ORDER:
        chapters = [
            {"key": item[0], "title": item[2], "path": item[3], "default_type": item[4]}
            for item in CHAPTERS if item[1] == key
        ]
        groups = [
            {"key": group_key, "title": group_title, "chapters": list(group_chapters)}
            for group_key, group_title, group_chapters in CHAPTER_GROUPS.get(key, ())
        ]
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
    for key, directory in PILLAR_DIRS.items():
        if text == directory or text.startswith(directory + "/"):
            return key
    return ""


def chapter_key_of(relpath: str) -> str:
    normalized = normalize_relpath(relpath)
    text = normalized.casefold()
    for path, item in sorted(CHAPTER_BY_PATH.items(), key=lambda pair: -len(pair[0])):
        if text == path or text.startswith(path + "/"):
            return item[0]
    custom = custom_notebook_name_from_relpath(normalized)
    return custom_notebook_key(custom) if custom else ""


def chapter_dir(chapter_key: str) -> str:
    value = str(chapter_key or "").strip()
    if value.startswith(CUSTOM_NOTEBOOK_PREFIX):
        try:
            return custom_notebook_path(value[len(CUSTOM_NOTEBOOK_PREFIX):])
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
        raise ValueError("这个名称是人生路的固定章节目录，请换一个")
    if folded in _NOTEBOOK_FOLDED_RESERVED_TITLES:
        raise ValueError("这个名称和内置本子/章节重名，请换一个")
    if folded in SKIP_DIRECTORIES:
        raise ValueError("这个名称是系统保留目录，请换一个")
    if text.split(".")[0].upper() in _RESERVED_STEMS:
        raise ValueError("这个名称是系统保留名，请换一个")
    return text


def custom_notebook_key(name: str) -> str:
    return f"{CUSTOM_NOTEBOOK_PREFIX}{name}"


def custom_notebook_name(chapter_key: str) -> str:
    value = str(chapter_key or "").strip()
    if not value.startswith(CUSTOM_NOTEBOOK_PREFIX):
        return ""
    try:
        return validate_notebook_name(value[len(CUSTOM_NOTEBOOK_PREFIX):])
    except ValueError:
        return ""


def is_custom_notebook_key(chapter_key: str) -> bool:
    return bool(custom_notebook_name(chapter_key))


def custom_notebook_path(name: str) -> str:
    return f"{CUSTOM_NOTEBOOK_ROOT}/{validate_notebook_name(name)}"


def custom_notebook_name_from_relpath(relpath: str) -> str:
    """00_人生路/<自定义文件夹>/... → 文件夹名；固定一级目录返回空串。"""
    text = normalize_relpath(relpath)
    parts = text.split("/")
    if len(parts) < 2 or parts[0] != CUSTOM_NOTEBOOK_ROOT:
        return ""
    name = parts[1]
    if name.casefold() in _NOTEBOOK_FOLDED_TOP_DIRS:
        return ""
    if name.startswith((".", "_")) or name.casefold() in SKIP_DIRECTORIES:
        return ""
    try:
        return validate_notebook_name(name)
    except ValueError:
        return ""


def list_custom_notebooks(vault_dir: Path | str) -> list[str]:
    """列出 00_人生路 下的自定义本子文件夹（不跟符号链接）。"""
    root = Path(vault_dir) / CUSTOM_NOTEBOOK_ROOT
    try:
        entries = list(root.iterdir())
    except OSError:
        return []
    names: list[str] = []
    for entry in entries:
        name = entry.name
        if name.casefold() in _NOTEBOOK_FOLDED_TOP_DIRS:
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
    current = "01_自我档案/01_内核与三观/身份（identity）.md"
    legacy = "01_自我档案/01_内核与三观/identity.md"
    return current, legacy


def legacy_identity_path() -> str:
    return "01-Identity/身份（identity）.md"


def legacy_journal_path(day: str) -> str:
    return f"02-Life/journal/{day}.md"


def inbox_dir() -> str:
    return chapter_dir("inbox")


def ensure_layout(vault_dir: Path | str) -> list[str]:
    """幂等创建四库和章节目录；返回实际创建的相对路径。"""
    root = Path(vault_dir)
    created: list[str] = []
    for relpath in [PILLAR_DIRS[key] for key in PILLAR_ORDER]:
        path = root / relpath
        if not path.is_dir():
            path.mkdir(parents=True, exist_ok=True)
            created.append(relpath)
    for item in CHAPTERS:
        path = root / item[3]
        if not path.is_dir():
            path.mkdir(parents=True, exist_ok=True)
            created.append(item[3])
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
    "PILLAR_DIRS", "PILLAR_ORDER", "CHAPTERS", "CHAPTER_BY_KEY", "CHAPTER_BY_PATH",
    "CHAPTER_GROUPS", "V1_CHAPTER_MOVES", "LEGACY_DIRS", "normalize_relpath",
    "chapter_manifest", "pillar_of", "chapter_key_of", "chapter_dir",
    "chapter_default_type", "v1_chapter_move", "is_foreign",
    "is_skipped", "index_policy", "safe_note_stem", "validate_target",
    "journal_path", "identity_paths", "legacy_identity_path", "legacy_journal_path",
    "inbox_dir", "ensure_layout", "layout_status",
]
