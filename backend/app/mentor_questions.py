"""人生导师的每日三问（2026-11-01）。

题库是 Markdown，用户可以在 Obsidian 里改：

- ``01_自我档案/02_人生导师/提问/题库.md``   固定题库（首次运行从内置种子生成）；
- ``01_自我档案/02_人生导师/提问/动态题库.md`` 全量整理时补充的缺口问题；
- ``01_自我档案/02_人生导师/提问/YYYY-MM-DD.md`` 当天 3 张问题卡（含拟答与状态）。

选择规则：优先空/薄本子、动态题库、高优先级；排除已答、冷却中的跳过、永久拒绝。
导师拟答由 ``mentor/question_draft`` 生成；用户确认拟答时同时写问答记忆与档案条目。
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

import yaml

from . import mentor_archive, mentor_memory, self_archive, vault_layout
from .ai_schemas import extract_json
from .memory_store import operation_lock
from .vault import parse_text, safe_note_path
from .vault_connection import VaultReadOnlyError, ensure_vault_writable

SHANGHAI = ZoneInfo("Asia/Shanghai")
BANK_TYPE = "mentor_question_bank"
TODAY_TYPE = "mentor_questions"
DAILY_COUNT = 3
STATE_RELATIVE = ".torch-os/人生导师/questions.json"
COOLDOWN_SKIPPED = timedelta(days=7)
RECENT_DAYS = 30
MAX_ANSWER = 4_000
MAX_PROMPT = 300

# (id, section, entry_type, priority, prompt, hint)
BANK: tuple[tuple[str, str, str, int, str, str], ...] = (
    ("q_facts_001", "self_facts", "fact", 90, "你的时间和钱，实际大头花在哪？", "各写一项就够，不用算得很细"),
    ("q_facts_002", "self_facts", "fact", 85, "你现在典型的一天是怎么过的？", "从起床到睡前，挑关键节点"),
    ("q_facts_003", "self_facts", "fact", 80, "你目前最持续消耗你的结构性问题是什么？", "通勤、家庭、经济、健康、关系、工作强度"),
    ("q_facts_004", "self_facts", "fact", 75, "你身边常联系的人有几个？多久见一次？", "只写数量和频率"),
    ("q_facts_005", "self_facts", "fact", 70, "你现在住在哪、和谁住、承担什么家庭责任？", "写你愿意记录的部分"),
    ("q_facts_006", "self_facts", "fact", 65, "你主动安排的时间大概有多少？", "一个小时/一天都可以"),
    ("q_body_001", "self_body", "fact", 88, "你最近一周的睡眠时段和时长是怎样的？", "几点睡、几点醒、中途醒不醒"),
    ("q_body_002", "self_body", "fact", 84, "最近一次运动是什么时候？做了什么？", "散步也算"),
    ("q_body_003", "self_body", "inference", 80, "什么事/什么人让你明显有能量？", "举一个最近的具体例子"),
    ("q_body_004", "self_body", "inference", 78, "什么事/什么人让你明显耗竭？", "举一个最近的具体例子"),
    ("q_body_005", "self_body", "behavior", 74, "焦虑、生气、低落时，你身体哪个部位先反应？", "肩颈、胃、头痛、心跳快……"),
    ("q_body_006", "self_body", "behavior", 68, "你靠什么方式恢复？一般需要多久？", "睡觉、独处、走路、找人聊"),
    ("q_behavior_001", "self_behavior", "behavior", 90, "你答应的小事，兑现率大概多少？", "回忆最近五次答应别人的事"),
    ("q_behavior_002", "self_behavior", "behavior", 86, "你最容易拖延的是什么？拖延时替代行为是什么？", "刷手机、打扫、睡觉……"),
    ("q_behavior_003", "self_behavior", "behavior", 82, "你回消息的速度和偏好是什么？", "文字/语音/电话/当面；已读不回的情境"),
    ("q_behavior_004", "self_behavior", "behavior", 78, "压力大的时候你会做什么？", "消失、暴食、购物、刷手机、过度工作……"),
    ("q_behavior_005", "self_behavior", "behavior", 72, "你在工作/学习上更像计划型还是随性型？", "先做难的还是简单的"),
    ("q_behavior_006", "self_behavior", "behavior", 66, "别人反复提醒你的是什么？", "一句最常见的话"),
    ("q_emotion_001", "self_emotion", "behavior", 92, "被否定/被忽视/失控/赶时间时，你分别怎么反应？", "选一个最典型的先写"),
    ("q_emotion_002", "self_emotion", "inference", 88, "你更容易爆发、冷战、讨好、逃避，还是讲理？", "写真实发生过的"),
    ("q_emotion_003", "self_emotion", "behavior", 84, "冲突后你怎么修复？需要对方做什么？", "道歉、解释、补偿、冷战……"),
    ("q_emotion_004", "self_emotion", "inference", 80, "你最怕在冲突里失去什么？", "关系、面子、控制感、被看见……"),
    ("q_emotion_005", "self_emotion", "hypothesis", 74, "你最近一次情绪崩溃前，身体给过什么信号？", "回看那几天"),
    ("q_emotion_006", "self_emotion", "narrative", 68, "什么情绪你允许自己表达，什么情绪你禁止？", "写一句就够"),
    ("q_motivation_001", "self_motivation", "inference", 90, "安全、认可、自由、成就、亲密、控制、成长、意义，你怎么排序？", "只写前三和后两名"),
    ("q_motivation_002", "self_motivation", "inference", 86, "你最怕什么？被否定、失控、平庸、欠人情，还是别的？", "写最真实的一个"),
    ("q_motivation_003", "self_motivation", "behavior", 82, "你最近反复抱怨的是什么？", "抱怨背后通常有未满足的需求"),
    ("q_motivation_004", "self_motivation", "inference", 78, "什么奖励对你真正有效？钱、自由、赞美、掌控感、关系、成长？", "想一个最近真的推动你的奖励"),
    ("q_motivation_005", "self_motivation", "inference", 72, "批评会让你改变还是防御？", "举一个最近的例子"),
    ("q_motivation_006", "self_motivation", "hypothesis", 66, "你长期没有满足的核心需求是什么？", "先猜一个，之后验证"),
    ("q_values_001", "self_values", "narrative", 96, "冲突时你实际怎么选？写真实选择，不写应该。", "金钱 vs 自由、稳定 vs 成长、关系 vs 成就"),
    ("q_values_002", "self_values", "narrative", 92, "有哪件事，是你宁可吃亏也不做的？", "底线比理想有用"),
    ("q_values_003", "self_values", "narrative", 88, "你觉得这个世界大体上是怎样的？", "一句你真心信的话"),
    ("q_values_004", "self_values", "narrative", 84, "人这一生，你觉得最要紧的是什么？", "这会决定你如何取舍"),
    ("q_values_005", "self_values", "narrative", 80, "什么情况下你会破例？破例后你怎么解释？", "写一次真实的破例"),
    ("q_values_006", "self_values", "inference", 74, "你最后悔的选择违背了哪条规则？", "不用写细节，写违背了什么"),
    ("q_relationship_001", "self_relationship", "behavior", 92, "你能拒绝别人吗？拒绝后要内疚多久？", "写最近一次拒绝"),
    ("q_relationship_002", "self_relationship", "behavior", 88, "你更容易多给、多要、试探，还是回避？", "付出后会不会记账"),
    ("q_relationship_003", "self_relationship", "inference", 84, "你最容易信任什么样的人？最难信任什么样的人？", "各写一个特征"),
    ("q_relationship_004", "self_relationship", "behavior", 80, "什么行为会让你翻脸或离开？", "红线写具体行为"),
    ("q_relationship_005", "self_relationship", "inference", 74, "你在关系里会试探对方吗？怎么试探？", "举一个例子"),
    ("q_relationship_006", "self_relationship", "hypothesis", 68, "你反复遇到的关系模式是什么？", "如果说不清，先写一个最近的"),
    ("q_narrative_001", "self_narrative", "narrative", 88, "你把自己讲成什么故事？逆袭者、幸存者、探索者、照顾者，还是别的？", "只写自我叙事，不当结论"),
    ("q_narrative_002", "self_narrative", "narrative", 82, "你最常用哪个标签解释自己？它帮你看见什么、遮住了什么？", "星座/MBTI/九型都可以"),
    ("q_narrative_003", "self_narrative", "narrative", 78, "如果去掉所有标签，你会怎么描述自己？", "写三句话"),
    ("q_narrative_004", "self_narrative", "inference", 72, "你自述「我很____」，但行为证据是什么？", "写一组对照"),
    ("q_narrative_005", "self_narrative", "hypothesis", 66, "如果重写你的人生故事，你想改哪一段？", "不用完整，写一段"),
    ("q_narrative_006", "self_narrative", "narrative", 60, "你希望别人记住你什么？", "一句话"),
    ("q_paradox_001", "self_paradox", "inference", 90, "你哪里说一套做一套？", "写最明显的一处"),
    ("q_paradox_002", "self_paradox", "hypothesis", 86, "别人反复提醒你、但你不爱听的是什么？", "可能是真的"),
    ("q_paradox_003", "self_paradox", "hypothesis", 82, "你最不敢承认的动机是什么？", "只写你愿意写的"),
    ("q_paradox_004", "self_paradox", "inference", 78, "你总在同一类事/同一类人上卡住吗？", "写一个反复出现的模式"),
    ("q_paradox_005", "self_paradox", "hypothesis", 72, "你对自己下的哪个结论最可能是错的？", "先写一个，之后验证"),
    ("q_paradox_006", "self_paradox", "behavior", 66, "你用忙碌、幽默或理性遮住了什么？", "写一个具体场景"),
    ("q_timeline_001", "self_timeline", "fact", 90, "到目前为止，哪一段经历最塑造了你？", "不用完整自传，一段就行"),
    ("q_timeline_002", "self_timeline", "narrative", 86, "你人生里最关键的三次选择是什么？", "各写一句当时的理由"),
    ("q_timeline_003", "self_timeline", "narrative", 82, "你最后悔的三个选择是什么？共同主题是什么？", "写主题，不写细节"),
    ("q_timeline_004", "self_timeline", "narrative", 78, "你最骄傲的三个选择是什么？共同主题是什么？", "写主题"),
    ("q_timeline_005", "self_timeline", "fact", 72, "你最近三年发生了哪些关键节点？", "工作、关系、健康、搬迁……"),
    ("q_timeline_006", "self_timeline", "inference", 66, "你常用「当时没办法」或「都是别人」解释什么？", "写一个例子"),
    ("q_experiments_001", "self_experiments", "hypothesis", 88, "选一个你对自己的假设，设计一个小实验，怎么验证？", "观察指标、时间范围、结果"),
    ("q_experiments_002", "self_experiments", "behavior", 82, "最近哪条结论被你的行为推翻了？", "写新旧结论"),
    ("q_experiments_003", "self_experiments", "behavior", 78, "哪个标签最近被你当成了借口？", "写具体场景"),
    ("q_experiments_004", "self_experiments", "hypothesis", 72, "哪个矛盾反复出现，你一直没验证？", "写一个"),
    ("q_experiments_005", "self_experiments", "fact", 66, "这季度你的时间和钱，支持了你自述的哪条价值观？", "对照实际支出与时间"),
    ("q_experiments_006", "self_experiments", "hypothesis", 60, "你最近一次破例，暴露了什么？", "写一个"),
)

_BY_ID = {item[0]: item for item in BANK}


class QuestionError(RuntimeError):
    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _now() -> str:
    return datetime.now(SHANGHAI).isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now(SHANGHAI).date().isoformat()


# 云同步、杀毒软件或编辑器可能在文件刚变化时短暂持有句柄；Windows 此时会
# 拒绝 os.replace（WinError 5/32），表现为一次偶发的 500。重试约 6.5 秒。
_REPLACE_RETRY_DELAYS = (0.1, 0.2, 0.4, 0.8, 1.0, 1.0, 1.5, 1.5)


def _transient_replace_error(error: OSError) -> bool:
    return isinstance(error, PermissionError) or getattr(error, "winerror", None) in (5, 32, 33)


def _replace_with_retry(source: str, target: Path) -> None:
    for delay in _REPLACE_RETRY_DELAYS:
        try:
            os.replace(source, target)
            return
        except OSError as error:
            if not _transient_replace_error(error):
                raise
        time.sleep(delay)
    os.replace(source, target)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", delete=False,
        dir=str(path.parent), prefix=".q-", suffix=".tmp",
    )
    try:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        _replace_with_retry(handle.name, path)
    except OSError as error:
        try:
            handle.close()
        except OSError:
            pass
        try:
            Path(handle.name).unlink(missing_ok=True)
        except OSError:
            pass
        raise QuestionError(
            "今日三问暂时无法保存：文件正被其他程序占用（常见于云同步或杀毒软件），请稍后重试",
            503,
        ) from error


def _ensure_write(read_only: bool, action: str) -> None:
    try:
        ensure_vault_writable(read_only, action)
    except VaultReadOnlyError as error:
        raise QuestionError(str(error), 409) from error


def _bank_text(rows: Iterable[tuple[str, str, str, int, str, str]], title: str) -> str:
    lines = ["---", f"type: {BANK_TYPE}", "version: 1", "---", "", f"# {title}", ""]
    current = ""
    for qid, section, entry_type, priority, prompt, hint in rows:
        meta = self_archive.book_meta(section)
        if meta["title"] != current:
            current = meta["title"]
            lines.extend([f"## {current}", ""])
        parts = [f"- [{qid}] {prompt}", f"类型={entry_type}", f"优先级={priority}"]
        if hint:
            parts.append(f"提示={hint}")
        lines.append("｜".join(parts))
    lines.append("")
    return "\n".join(lines)


def _path(vault: Path, name: str) -> Path:
    return safe_note_path(vault, f"{vault_layout.SELF_MENTOR_QUESTIONS_DIR}/{name}")


def ensure_bank(vault_dir: Path | str, *, read_only: bool = False) -> None:
    vault = Path(vault_dir).resolve()
    path = _path(vault, "题库.md")
    if path.is_file():
        return
    _ensure_write(read_only, "创建人生导师题库")
    _atomic_write(path, _bank_text(BANK, "人生导师题库"))
    dynamic = _path(vault, "动态题库.md")
    if not dynamic.is_file():
        _atomic_write(dynamic, _bank_text((), "动态题库"))


def _parse_line(line: str) -> dict[str, Any] | None:
    match = re.match(r"^\s*-\s*\[([^\]]+)\]\s*(.+)$", line)
    if not match:
        return None
    qid = match.group(1).strip()
    rest = match.group(2).strip()
    parts = re.split(r"\s*[｜|]\s*", rest)
    prompt = parts[0].strip()
    if not qid or not prompt:
        return None
    values: dict[str, str] = {}
    for part in parts[1:]:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        values[key.strip()] = value.strip()
    entry_type = values.get("类型", "narrative").strip()
    if entry_type not in self_archive.ENTRY_TYPES:
        entry_type = "narrative"
    try:
        priority = int(values.get("优先级", "50"))
    except ValueError:
        priority = 50
    return {
        "id": qid,
        "prompt": prompt[:MAX_PROMPT],
        "entry_type": entry_type,
        "priority": max(0, min(priority, 100)),
        "hint": values.get("提示", "").strip(),
        "source": "bank",
    }


def parse_bank(text: str, *, source: str = "bank") -> list[dict[str, Any]]:
    section = ""
    rows: list[dict[str, Any]] = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            title = stripped[3:].strip()
            section = next((key for key, item in self_archive.BOOK_BY_KEY.items()
                            if item[1] == title), "")
            continue
        item = _parse_line(line)
        if item is None or section not in self_archive.BOOK_BY_KEY:
            continue
        item["section"] = section
        item["section_title"] = self_archive.BOOK_BY_KEY[section][1]
        item["source"] = source
        rows.append(item)
    return rows


def _read_bank_file(path: Path, source: str) -> list[dict[str, Any]]:
    try:
        _meta, body = parse_text(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, yaml.YAMLError):
        return []
    return parse_bank(body, source=source)


def read_bank(vault_dir: Path | str) -> list[dict[str, Any]]:
    vault = Path(vault_dir).resolve()
    rows = _read_bank_file(_path(vault, "题库.md"), "bank")
    rows += _read_bank_file(_path(vault, "动态题库.md"), "dynamic")
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in rows:
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        result.append(item)
    return result


def append_dynamic_questions(vault_dir: Path | str, questions: Iterable[Mapping[str, Any]], *,
                             read_only: bool = False) -> list[dict[str, Any]]:
    _ensure_write(read_only, "写入人生导师动态题库")
    vault = Path(vault_dir).resolve()
    path = _path(vault, "动态题库.md")
    if not path.is_file():
        _atomic_write(path, _bank_text((), "动态题库"))
    _meta, body = parse_text(path.read_text(encoding="utf-8-sig"))
    existing = {item["id"] for item in parse_bank(body, source="dynamic")}
    lines = [body.rstrip()]
    added: list[dict[str, Any]] = []
    current = ""
    for raw in questions:
        section = self_archive.normalize_book_key(raw.get("section"))
        if not section:
            continue
        prompt = str(raw.get("prompt") or "").strip()[:MAX_PROMPT]
        if not prompt:
            continue
        qid = str(raw.get("id") or "").strip()
        if not qid:
            qid = "q_dyn_" + hashlib.sha256(
                f"{section}|{prompt}".encode("utf-8")).hexdigest()[:10]
        if qid in existing:
            continue
        entry_type = str(raw.get("entry_type") or "narrative")
        if entry_type not in self_archive.ENTRY_TYPES:
            entry_type = "narrative"
        try:
            priority = int(raw.get("priority") or 55)
        except (TypeError, ValueError):
            priority = 55
        title = self_archive.BOOK_BY_KEY[section][1]
        if title != current:
            current = title
            lines.extend(["", f"## {title}"])
        parts = [f"- [{qid}] {prompt}", f"类型={entry_type}", f"优先级={priority}"]
        why = str(raw.get("why") or "").strip()
        if why:
            parts.append(f"提示={why}")
        lines.append("｜".join(parts))
        existing.add(qid)
        added.append({"id": qid, "section": section, "prompt": prompt})
    if not added:
        return []
    metadata = {"type": BANK_TYPE, "version": 1, "updated_at": _now()}
    _atomic_write(path, "---\n" + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False)
                  + "---\n\n" + "\n".join(lines).strip() + "\n")
    return added


def _state_path(vault: Path) -> Path:
    return vault / STATE_RELATIVE


def load_state(vault_dir: Path | str) -> dict[str, Any]:
    vault = Path(vault_dir).resolve()
    empty = {"schema": 1, "asked": {}, "skipped": {}, "declined": []}
    try:
        raw = json.loads(_state_path(vault).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty
    if not isinstance(raw, dict):
        return empty
    asked = raw.get("asked") if isinstance(raw.get("asked"), dict) else {}
    skipped = raw.get("skipped") if isinstance(raw.get("skipped"), dict) else {}
    declined = raw.get("declined") if isinstance(raw.get("declined"), list) else []
    return {"schema": 1, "asked": asked, "skipped": skipped,
            "declined": [str(item) for item in declined]}


def save_state(vault_dir: Path | str, state: Mapping[str, Any]) -> None:
    path = _state_path(Path(vault_dir).resolve())
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, json.dumps(dict(state), ensure_ascii=False, indent=2) + "\n")


def _parse_time(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _eligible(item: Mapping[str, Any], state: Mapping[str, Any], now: datetime) -> bool:
    qid = str(item.get("id") or "")
    if qid in state.get("declined", []):
        return False
    asked = _parse_time(state.get("asked", {}).get(qid))
    if asked is not None and now - asked < timedelta(days=RECENT_DAYS):
        return False
    skipped = _parse_time(state.get("skipped", {}).get(qid))
    if skipped is not None and now - skipped < COOLDOWN_SKIPPED:
        return False
    return True


def _weak_books(vault: Path) -> set[str]:
    weak: set[str] = set()
    for key in self_archive.BOOK_ORDER:
        if not self_archive.list_entries(vault, key):
            weak.add(key)
    return weak


def _select(vault: Path, bank: list[dict[str, Any]], state: Mapping[str, Any], *,
            now: datetime, count: int = DAILY_COUNT) -> list[dict[str, Any]]:
    weak = _weak_books(vault)
    rows = [item for item in bank if _eligible(item, state, now)]
    rng = random.Random(now.date().isoformat())
    rng.shuffle(rows)
    rows.sort(key=lambda item: (
        1 if item.get("section") in weak else 0,
        1 if item.get("source") == "dynamic" else 0,
        int(item.get("priority") or 0),
    ), reverse=True)
    selected: list[dict[str, Any]] = []
    used_sections: dict[str, int] = {}
    for item in rows:
        section = str(item.get("section") or "")
        if used_sections.get(section, 0) >= 2:
            continue
        selected.append(item)
        used_sections[section] = used_sections.get(section, 0) + 1
        if len(selected) >= count:
            break
    return selected


def _dated_path(vault: Path, day: str) -> Path:
    return safe_note_path(vault, f"{vault_layout.SELF_MENTOR_QUESTIONS_DIR}/{day}.md")


def _serialize_today(day: str, items: Iterable[Mapping[str, Any]]) -> str:
    metadata = {"type": TODAY_TYPE, "date": day, "updated_at": _now(),
                "status": "done" if all(item.get("status") in ("answered", "skipped", "declined")
                                        for item in items) else "open"}
    lines = ["---", *yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False)
             .rstrip("\n").split("\n"), "---", "", f"# {day} · 今日三问", ""]
    for item in items:
        lines.extend([
            f"## {item.get('id')}",
            "",
            f"- 问题：{item.get('prompt')}",
            f"- 本子：{item.get('section')}",
            f"- 类型：{item.get('entry_type')}",
            f"- 提示：{item.get('hint') or ''}",
            f"- 来源：{item.get('source') or 'bank'}",
            f"- 拟答：{item.get('suggested') or ''}",
            f"- 拟定来源：{', '.join(item.get('suggested_sources') or [])}",
            f"- 状态：{item.get('status') or 'pending'}",
            f"- 回答：{item.get('answer') or ''}",
            f"- 回答方式：{item.get('answer_mode') or ''}",
            f"- 档案状态：{item.get('archive_status') or ''}",
            f"- 档案条目：{item.get('archive_entry_id') or ''}",
            f"- 档案错误：{item.get('archive_error') or ''}",
            "",
        ])
    return "\n".join(lines).strip() + "\n"


def parse_today(text: str) -> list[dict[str, Any]]:
    _metadata, body = parse_text(text)
    items: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            if current:
                items.append(current)
            current = {"id": stripped[3:].strip()}
            continue
        if current is None or not stripped.startswith("- "):
            continue
        content = stripped[2:]
        if "：" not in content and ":" not in content:
            continue
        key, value = re.split(r"[：:]", content, maxsplit=1)
        key = key.strip()
        value = value.strip()
        mapping = {
            "问题": "prompt", "本子": "section", "类型": "entry_type",
            "提示": "hint", "来源": "source", "拟答": "suggested",
            "状态": "status", "回答": "answer", "回答方式": "answer_mode",
            "档案状态": "archive_status", "档案条目": "archive_entry_id",
            "档案错误": "archive_error",
        }
        if key == "拟定来源":
            current["suggested_sources"] = [part.strip() for part in value.split(",") if part.strip()]
        elif key in mapping:
            current[mapping[key]] = value
    if current:
        items.append(current)
    for item in items:
        item.setdefault("suggested", "")
        item.setdefault("suggested_sources", [])
        item.setdefault("status", "pending")
        item.setdefault("answer", "")
        item.setdefault("answer_mode", "")
        item.setdefault("archive_status", "")
        item.setdefault("archive_entry_id", "")
        item.setdefault("archive_error", "")
        item.setdefault("section_title",
                        self_archive.BOOK_BY_KEY.get(item.get("section", ""), ("", "", ""))[1])
    return items


def _read_today(vault: Path, day: str) -> list[dict[str, Any]] | None:
    path = _dated_path(vault, day)
    if not path.is_file():
        return None
    try:
        return parse_today(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, yaml.YAMLError):
        return None


def ensure_today(vault_dir: Path | str, *, day: str = "", read_only: bool = False) -> dict[str, Any]:
    ensure_bank(vault_dir, read_only=read_only)
    vault = Path(vault_dir).resolve()
    date = day or _today()
    items = _read_today(vault, date)
    saved = items is not None
    if items is None:
        state = load_state(vault)
        selected = _select(vault, read_bank(vault), state, now=datetime.now(SHANGHAI))
        items = [dict(item, status="pending", suggested="", suggested_sources=[], answer="")
                 for item in selected]
        if not read_only:
            _atomic_write(_dated_path(vault, date), _serialize_today(date, items))
            for item in selected:
                state["asked"][item["id"]] = datetime.now(SHANGHAI).isoformat(timespec="seconds")
            save_state(vault, state)
            saved = True
    return {"date": date, "items": items, "saved": saved,
            "remaining": sum(1 for item in items if item.get("status") == "pending")}


def write_today(vault_dir: Path | str, day: str, items: Iterable[Mapping[str, Any]], *,
                read_only: bool = False) -> None:
    _ensure_write(read_only, "更新今日三问")
    vault = Path(vault_dir).resolve()
    _atomic_write(_dated_path(vault, day), _serialize_today(day, items))


def _find_question(vault: Path, qid: str, *, days: int = 7) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """Find the card by id in today or a recent day's file.

    A page left open across midnight keeps its old qid; answering it updates
    the day that actually contains the card instead of today's questions.
    """
    wanted = str(qid or "").strip()
    if not wanted:
        raise QuestionError("问题不存在", 404)
    today = datetime.now(SHANGHAI).date()
    for offset in range(max(1, int(days))):
        day = (today - timedelta(days=offset)).isoformat()
        items = _read_today(vault, day)
        if items is None:
            continue
        for item in items:
            if item.get("id") == wanted:
                return day, items, item
    raise QuestionError("这个问题不在最近的三问里", 404)


def _qa_source() -> str:
    return f"{vault_layout.SELF_MENTOR_MEMORY_DIR}/问答记忆.md"


def _answer_candidate(item: Mapping[str, Any], answer_text: str,
                      mode: str) -> dict[str, Any]:
    section = str(item.get("section") or "")
    if section not in self_archive.BOOK_BY_KEY:
        raise QuestionError("问题没有目标本子", 422)
    entry_type = str(item.get("entry_type") or "narrative")
    if entry_type not in self_archive.ENTRY_TYPES:
        entry_type = "narrative"
    if mode == "suggested":
        evidence = "用户采纳导师拟答"
    elif mode == "suggested-edited":
        evidence = "用户在导师拟答基础上改后确认"
    else:
        evidence = "用户回答每日三问"
    suggested_sources = [str(value) for value in (item.get("suggested_sources") or [])]
    if mode != "user" and suggested_sources:
        evidence += "（原拟答来源：" + "、".join(suggested_sources) + "）"
    return {
        "action": "create",
        "section": section,
        "entry_id": "",
        "entry_type": entry_type,
        "confidence": "medium",
        "title": str(item.get("prompt") or "问题回答")[:self_archive.MAX_TITLE],
        "text": answer_text,
        "evidence": evidence,
        "counterexample": "",
        "verify": "",
        "sources": [_qa_source()],
        "origin": "question" if mode == "suggested" else "user-answer",
        "old_text": "",
        "base_revision": "",
        "previous_entry_id": "",
        "previous_path": "",
        "previous_text": "",
    }


def _apply_answer_archive(vault: Path, day: str, qid: str,
                          item: dict[str, Any], answer_text: str,
                          mode: str) -> dict[str, Any]:
    candidate = _answer_candidate(item, answer_text, mode)
    result = mentor_archive.apply_candidate(
        vault, candidate,
        operation_id=f"question:{day}:{qid}:archive",
        read_only=False,
    )
    item["answer_mode"] = mode
    if result.get("ok"):
        item["archive_status"] = "done"
        item["archive_entry_id"] = result.get("entry_id") or ""
        item["archive_error"] = ""
        try:
            mentor_memory.link_qa_entry(vault, qid, item["archive_entry_id"])
        except Exception:
            # The answer memory already exists; a missing backlink is not fatal.
            pass
    else:
        item["archive_status"] = "failed"
        item["archive_entry_id"] = ""
        item["archive_error"] = " ".join(
            str(result.get("error") or "档案整理失败").split())[:500]
    return result


def _answer_payload(item: Mapping[str, Any], qid: str, *, qa: Any = None,
                    result: Mapping[str, Any] | None = None,
                    duplicate: bool = False) -> dict[str, Any]:
    archive_saved = item.get("archive_status") == "done"
    return {
        "answered": True,
        "question": qid,
        "qa": qa,
        "entry": result if archive_saved and result else None,
        "archive_update": dict(result) if result else None,
        "answer_saved": True,
        "archive_saved": archive_saved,
        "archive_status": item.get("archive_status") or "",
        "archive_entry_id": item.get("archive_entry_id") or "",
        "archive_error": item.get("archive_error") or "",
        "duplicate": duplicate,
    }


def draft_today(role_service: Any, vault_dir: Path | str, *, read_only: bool = False) -> dict[str, Any]:
    """调用 mentor/question_draft 为今天 3 条问题生成拟答。"""
    if role_service is None:
        raise QuestionError("AI 不可用：请先在 dsh 里配置模型", 503)
    context = role_service.prepare_context("mentor", "mentor_questions",
                                           lambda: ensure_today(vault_dir, read_only=read_only))
    vault = Path(vault_dir).resolve()
    day = _today()
    items = context.value.get("items") or []
    pending = [item for item in items if not item.get("suggested")]
    if not pending:
        return {"date": day, "items": items, "drafted": 0}
    prompt = json.dumps({
        "task": "为今日三问生成拟答",
        "questions": [
            {"id": item.get("id"), "prompt": item.get("prompt"),
             "section": item.get("section"), "entry_type": item.get("entry_type"),
             "hint": item.get("hint")}
            for item in pending
        ],
        "输出要求": (
            "只输出一个 JSON 对象，不要 Markdown 代码块。结构："
            "{\"drafts\":[{\"id\":\"...\",\"can_answer\":true|false,"
            "\"answer\":\"...\",\"sources\":[\"S1\"],\"entry_type\":\"fact|behavior|inference|narrative|hypothesis\"}]}。"
            "只有三库或档案里确实有证据时才 can_answer=true；没有证据就 false，不要编造。"
        ),
    }, ensure_ascii=False)
    try:
        context = role_service.format_context(context, lambda _: prompt)
        result = role_service.run_once(
            "mentor", "question_draft", "请为资料中的问题生成拟答。", context=context, entry="self.mentor",
            selectors=[
                {"entry": "identity"}, {"entry": "self_archive"},
                {"entry": "mentor_memory"},
                {"entry": "journals_recent", "days": 14}, {"entry": "tasks"},
                {"entry": "ops_summary"}, {"entry": "recent_notes"},
            ])
    except Exception as error:  # noqa: BLE001
        raise QuestionError(f"生成拟答失败：{error}", 502) from error
    data = result.get("structured") or extract_json(result.get("text") or "") or {}
    drafts = data.get("drafts") if isinstance(data.get("drafts"), list) else []
    by_id = {str(row.get("id") or ""): row for row in drafts if isinstance(row, dict)}
    updated = 0
    for item in items:
        row = by_id.get(item.get("id"))
        if not row:
            continue
        answer = str(row.get("answer") or "").strip()
        if answer and row.get("can_answer") is not False:
            item["suggested"] = answer[:MAX_ANSWER]
            item["suggested_sources"] = [str(x) for x in (row.get("sources") or [])][:20]
            if str(row.get("entry_type") or "") in self_archive.ENTRY_TYPES:
                item["entry_type"] = str(row.get("entry_type"))
            updated += 1
    if updated and not read_only:
        write_today(vault, day, items, read_only=read_only)
    return {"date": day, "items": items, "drafted": updated}


def answer(vault_dir: Path | str, qid: str, text: str, *,
           accept_suggested: bool = False, read_only: bool = False) -> dict[str, Any]:
    _ensure_write(read_only, "回答今日三问")
    vault = Path(vault_dir).resolve()
    wanted = str(qid or "").strip()
    with operation_lock(f"question|{vault}|{wanted}"):
        day, items, item = _find_question(vault, wanted)
        status = str(item.get("status") or "pending")
        if status == "answered":
            # Duplicate submit, or retry the archive stage after a partial
            # failure. Never append the answer memory a second time.
            if item.get("archive_status") in ("failed", "pending") and str(item.get("answer") or "").strip():
                mode = str(item.get("answer_mode") or
                           ("suggested" if accept_suggested else "user"))
                result = _apply_answer_archive(
                    vault, day, wanted, item, str(item["answer"]), mode)
                write_today(vault, day, items, read_only=False)
                return _answer_payload(item, wanted, result=result, duplicate=True)
            result = None
            if item.get("archive_status") == "done" and item.get("archive_entry_id"):
                entry = self_archive.find_entry(
                    vault, item["archive_entry_id"], include_history=True)
                result = {"ok": True, "entry_id": item["archive_entry_id"],
                          "path": (entry or {}).get("path", "")}
            return _answer_payload(item, wanted, result=result, duplicate=True)
        if status != "pending":
            raise QuestionError("这个问题已经处理过了", 409)
        answer_text = str(text or "").strip()
        suggested = str(item.get("suggested") or "").strip()
        if accept_suggested:
            answer_text = answer_text or suggested
            mode = "suggested" if answer_text == suggested else "suggested-edited"
        else:
            mode = "user"
        if not answer_text:
            raise QuestionError("回答不能为空", 422)
        if len(answer_text) > MAX_ANSWER:
            raise QuestionError("回答过长", 422)
        section = str(item.get("section") or "")
        # Stage 1: answer memory first. It is authoritative for what the user
        # actually said; archive writing must not be able to lose it.
        qa = mentor_memory.append_qa(
            vault, question=str(item.get("prompt") or ""), answer=answer_text,
            qid=wanted, section=section,
            sources=item.get("suggested_sources") or [],
            entry_id="", read_only=False,
        )
        item["status"] = "answered"
        item["answer"] = answer_text
        item["answer_mode"] = mode
        item["archive_status"] = "pending"
        item["archive_entry_id"] = ""
        item["archive_error"] = ""
        # Persist the answer before the archive stage so a crash leaves a
        # recoverable "answered but archive pending" card, not a lost answer.
        write_today(vault, day, items, read_only=False)
        # Stage 2: archive through the same validated update chain as B4b.
        result = _apply_answer_archive(vault, day, wanted, item, answer_text, mode)
        write_today(vault, day, items, read_only=False)
        return _answer_payload(item, wanted, qa=qa, result=result)


def skip(vault_dir: Path | str, qid: str, *, forever: bool = False,
         read_only: bool = False) -> dict[str, Any]:
    _ensure_write(read_only, "跳过今日三问")
    vault = Path(vault_dir).resolve()
    wanted = str(qid or "").strip()
    target = "declined" if forever else "skipped"
    with operation_lock(f"question|{vault}|{wanted}"):
        day, items, item = _find_question(vault, wanted)
        status = str(item.get("status") or "pending")
        if status == target:
            return {"skipped": True, "forever": bool(forever),
                    "question": wanted, "duplicate": True}
        if status != "pending":
            raise QuestionError("这个问题已经处理过了", 409)
        item["status"] = target
        write_today(vault, day, items, read_only=False)
        state = load_state(vault)
        if forever:
            if wanted not in state["declined"]:
                state["declined"].append(wanted)
            state["skipped"].pop(wanted, None)
        else:
            state["skipped"][wanted] = datetime.now(SHANGHAI).isoformat(timespec="seconds")
        save_state(vault, state)
        return {"skipped": True, "forever": bool(forever), "question": wanted}


__all__ = [
    "BANK", "QuestionError", "ensure_bank", "read_bank", "parse_bank",
    "append_dynamic_questions", "ensure_today", "parse_today", "write_today",
    "draft_today", "answer", "skip", "load_state", "save_state",
]
