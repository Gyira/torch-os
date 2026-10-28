"""主动提问：按我最近的状态，一天问一个「关于我」的问题。

三种去处，各自清楚：

- **我回答了** → 直接写进自我档案。被问到、并且我亲口答了，这就是确认，不再要二次确认；
- **我跳过**   → 这个问题冷却 7 天，换成下一个；
- **别再问这个** → 永久不再问。

问题从两处来，同一张卡片消费：

1. **dsh 投的提问队列**（`<库>/.orch-os` 旁的 `.torch-os/提问队列/`）——大模型读过四库之后
   认为「这里有个缺口，值得问」，逐条带依据（哪几篇笔记说明该问）。torch 不调模型，只负责
   什么时候问、答案写哪、用过就收走；
2. **固定题库**——队列为空时的保底，离线也能问，且保证「署名 / 三观」这类地基问题不会被漏掉。

队列优先、题库兜底，是因为「问什么最有价值」该由读过内容的一方决定；而「什么时候问」
属于界面节奏，不该交给模型。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import pillar_notes
from .identity import IdentityWrite, _identity_path, read_identity, write_identity
from .vault import parse_text, safe_note_path
from .vault_connection import VaultReadOnlyError, ensure_vault_writable

STATE_RELATIVE = ".torch-os/questions.json"
QUEUE_DIR = ".torch-os/提问队列"
QUEUE_DONE = f"{QUEUE_DIR}/已用"
QUEUE_TTL = timedelta(days=14)
STATE_VERSION = 1
COOLDOWN_SKIPPED = timedelta(days=7)
DAILY_LIMIT = 1
MAX_ANSWER = 4000


def _state_path(vault_dir: Path | str) -> Path:
    return safe_note_path(Path(vault_dir), STATE_RELATIVE)


def _now() -> datetime:
    return datetime.now().astimezone()


def load_state(vault_dir: Path | str) -> dict[str, Any]:
    path = _state_path(vault_dir)
    empty = {"version": STATE_VERSION, "answered": {}, "skipped": {}, "declined": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty
    if not isinstance(raw, dict):
        return empty
    answered = raw.get("answered") if isinstance(raw.get("answered"), dict) else {}
    skipped = raw.get("skipped") if isinstance(raw.get("skipped"), dict) else {}
    declined = raw.get("declined") if isinstance(raw.get("declined"), list) else []
    return {"version": STATE_VERSION, "answered": answered, "skipped": skipped,
            "declined": [str(item) for item in declined]}


def save_state(vault_dir: Path | str, state: dict[str, Any]) -> None:
    path = _state_path(vault_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _signals(vault_dir: Path | str) -> dict[str, Any]:
    identity = read_identity(vault_dir)
    notes = pillar_notes.list_notes(vault_dir, pillar="self", limit=500)
    counts: dict[str, int] = {}
    for note in notes:
        key = str(note.get("chapter") or "")
        counts[key] = counts.get(key, 0) + 1
    projects = pillar_notes.list_notes(vault_dir, chapter="project_plan", limit=3)
    return {
        "identity": {key: str(identity.get(key) or "").strip()
                     for key in ("name", "motto", "worldview", "lifeview", "values", "notes")},
        "counts": counts,
        "project": str(projects[0]["title"]) if projects else "",
        "notes": len(notes),
    }


def _has(signals: dict[str, Any], chapter: str) -> bool:
    return int(signals["counts"].get(chapter, 0)) > 0


BANK: tuple[dict[str, Any], ...] = (
    {
        "id": "identity.name", "kind": "field", "field": "name", "priority": 100,
        "prompt": "我该怎么称呼你？",
        "hint": "写进身份卡的「署名」。以后每次开场、每条提醒，我都这么叫你。",
        "placeholder": "小林 / 阿杰 / 你习惯的任何写法",
        "when": lambda s: not s["identity"]["name"],
    },
    {
        "id": "identity.worldview", "kind": "field", "field": "worldview", "priority": 96,
        "prompt": "你觉得这个世界大体上是怎样的？",
        "hint": "不用宏大。一句你真心信的话就够，比如「努力基本有用，但运气占三成」。",
        "placeholder": "一句话就行",
        "when": lambda s: not s["identity"]["worldview"],
    },
    {
        "id": "identity.lifeview", "kind": "field", "field": "lifeview", "priority": 92,
        "prompt": "人这一生，你觉得最要紧的是什么？",
        "hint": "这会决定我以后怎么跟你讨论取舍——是稳一点，还是赌一把。",
        "placeholder": "一句话就行",
        "when": lambda s: not s["identity"]["lifeview"],
    },
    {
        "id": "identity.values", "kind": "field", "field": "values", "priority": 88,
        "prompt": "有哪件事，是你宁可吃亏也不做的？",
        "hint": "写底线比写理想有用。我会记住，并且在你要越界时提醒你。",
        "placeholder": "比如：不骗人 / 不为了钱做没价值的事",
        "when": lambda s: not s["identity"]["values"],
    },
    {
        "id": "identity.notes", "kind": "field", "field": "notes", "priority": 84,
        "prompt": "如果只能用一段话介绍自己，你会怎么说？",
        "hint": "写给三个月后的自己看，也写给我看。",
        "placeholder": "我是谁、在哪、在做什么、想去哪",
        "when": lambda s: not s["identity"]["notes"],
    },
    {
        "id": "memory.seed", "kind": "memory", "chapter": "memory", "priority": 72,
        "prompt": "到目前为止，哪一段经历最塑造了你？",
        "hint": "不用完整自传，一段就行。我会把它放进「记忆与自传」，以后聊天时当成背景。",
        "placeholder": "发生了什么，你从中变成了什么样的人",
        "when": lambda s: not _has(s, "memory"),
    },
    {
        "id": "relationship.people", "kind": "memory", "chapter": "relationship", "priority": 68,
        "prompt": "你身边最重要的人是谁？我该怎么称呼 TA？",
        "hint": "只记称呼和关系，不写隐私细节。以后提到 TA 时我不会叫错。",
        "placeholder": "比如：我妈，老周（同事）",
        "when": lambda s: not _has(s, "relationship"),
    },
    {
        "id": "meaning.three_years", "kind": "memory", "chapter": "meaning", "priority": 64,
        "prompt": "三年后回头看，你希望自己已经做成了什么？",
        "hint": "写具体的一件事，比「变得更好」有用得多。",
        "placeholder": "一件可以验证的事",
        "when": lambda s: not _has(s, "meaning"),
    },
    {
        "id": "project.followup", "kind": "memory", "chapter": "memory", "priority": 58,
        "prompt": "你手上那件事，现在到哪一步了？",
        "hint": "我会把它记成一条进展，以后不再问「你最近怎么样」这种空话。",
        "placeholder": "卡在哪 / 下一步是什么",
        "when": lambda s: bool(s["project"]),
    },
    {
        "id": "distill.method", "kind": "memory", "chapter": "distill_work", "priority": 52,
        "prompt": "有没有一件你反复做、并且越做越顺的事？方法是什么？",
        "hint": "这类东西写下来才算真的会了。以后遇到同类问题，我会直接把方法搬出来。",
        "placeholder": "步骤写粗一点也行",
        "when": lambda s: not _has(s, "distill_work"),
    },
)

_BY_ID = {item["id"]: item for item in BANK}


def _parse(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _cooled(question_id: str, state: dict[str, Any], now: datetime) -> bool:
    if question_id in state["declined"]:
        return False
    if question_id in state["answered"]:
        return False
    last = _parse(state["skipped"].get(question_id, ""))
    if last is not None and now - last < COOLDOWN_SKIPPED:
        return False
    return True


def _answered_today(state: dict[str, Any], now: datetime) -> int:
    today = now.date().isoformat()
    return sum(1 for stamp in state["answered"].values()
               if str(stamp).startswith(today))


def queue_dir(vault_dir: Path | str) -> Path:
    return Path(vault_dir) / QUEUE_DIR


def load_queue(vault_dir: Path | str, *, now: datetime | None = None) -> list[dict[str, Any]]:
    """dsh 投的提问卡：每张都带「为什么问」。过期或字段不全的直接忽略。"""
    moment = now or _now()
    directory = queue_dir(vault_dir)
    if not directory.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.md")):
        try:
            meta, _body = parse_text(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        prompt = str(meta.get("prompt") or "").strip()
        field = str(meta.get("field") or "").strip()
        chapter = str(meta.get("chapter") or "").strip()
        kind = "field" if field else ("memory" if chapter else "")
        if not prompt or not kind:
            continue
        created = _parse(str(meta.get("created") or ""))
        if created is not None and moment - created > QUEUE_TTL:
            continue
        try:
            priority = int(meta.get("priority") or 120)
        except (TypeError, ValueError):
            priority = 120
        why = str(meta.get("why") or "").strip()
        items.append({
            "id": f"queue:{path.stem}",
            "kind": kind,
            "field": field,
            "chapter": chapter,
            "prompt": prompt,
            "hint": why,
            "why": why,
            "placeholder": str(meta.get("placeholder") or "").strip(),
            "priority": priority,
            "created": str(meta.get("created") or ""),
            "path": str(path),
            "queued": True,
        })
    return items


def consume_queue_item(queue_path: Path) -> str:
    """用过的提问卡挪进「已用」，留痕但不占队列。"""
    target_dir = queue_path.parent / "已用"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / queue_path.name
    index = 2
    while target.exists():
        target = target_dir / f"{queue_path.stem}-{index}.md"
        index += 1
    os.replace(queue_path, target)
    return target.name


def resolve_question(vault_dir: Path | str, question_id: str) -> tuple[dict[str, Any] | None, Path | None]:
    """题库 id 或队列 id（`queue:<文件名>`）都能解析；队列的顺带给出文件路径。"""
    key = str(question_id or "")
    if key.startswith("queue:"):
        for item in load_queue(vault_dir):
            if item["id"] == key:
                return item, Path(item["path"])
        return None, None
    question = _BY_ID.get(key)
    return (dict(question) if question else None), None


def _payload(question: dict[str, Any], remaining: int) -> dict[str, Any]:
    return {
        "id": question["id"],
        "kind": question["kind"],
        "prompt": question["prompt"],
        "hint": question["hint"],
        "why": str(question.get("why") or ""),
        "queued": bool(question.get("queued")),
        "placeholder": question["placeholder"],
        "target": question.get("field") or question.get("chapter") or "",
        "remaining": remaining,
    }


def question_state(vault_dir: Path | str, *, force: bool = False,
                   now: datetime | None = None) -> dict[str, Any]:
    """今天的提问状态：`force=True` 时无视「一天一个」的上限（我主动点「再问一个」）。"""
    moment = now or _now()
    state = load_state(vault_dir)
    signals = _signals(vault_dir)
    queued = [item for item in load_queue(vault_dir, now=moment)
              if _cooled(item["id"], state, moment)]
    bank = [item for item in BANK
            if _cooled(item["id"], state, moment) and item["when"](signals)]
    # 队列在前：这是读过内容之后挑出来的缺口；题库只在队列空着时兜底。
    candidates = queued + bank
    candidates.sort(key=lambda item: (int(item.get("priority") or 0), str(item.get("created") or "")),
                    reverse=True)
    answered_today = _answered_today(state, moment) >= DAILY_LIMIT
    allowed = force or not answered_today
    question = _payload(candidates[0], len(candidates)) if candidates and allowed else None
    return {
        "question": question,
        "answered_today": answered_today,
        "remaining": len(candidates),
        "exhausted": not candidates,
    }


def next_question(vault_dir: Path | str, *, now: datetime | None = None) -> dict[str, Any] | None:
    """今天该问的那一个；问完/跳过完按冷却和上限给出 None。"""
    return question_state(vault_dir, now=now)["question"]


def _summary(text: str) -> str:
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return first[:40] + ("…" if len(first) > 40 else "")


def _write_answer_note(vault_dir: Path | str, question: dict[str, Any], text: str) -> dict[str, Any]:
    chapter = str(question.get("chapter") or "memory")
    summary = _summary(text)
    body = (f"> {summary}\n\n## 我的回答\n\n{text}\n\n"
            f"## 当时在问什么\n\n{question['prompt']}\n")
    note = pillar_notes.create_note(
        vault_dir, pillar="self", chapter=chapter, title=summary or question["prompt"][:30],
        body=body, tags=["提问", "主动回答"], source="torch-question",
    )
    return {"target": chapter, "path": note.get("path") or "", "title": note.get("title") or ""}


def _write_identity_field(vault_dir: Path | str, field: str, text: str,
                          *, read_only: bool) -> dict[str, Any]:
    current = read_identity(vault_dir)
    payload = {key: str(current.get(key) or "")
               for key in ("name", "motto", "worldview", "lifeview", "values", "notes")}
    payload[field] = text[:MAX_ANSWER]
    payload["revision"] = str(current.get("revision") or "")
    write_identity(vault_dir, IdentityWrite(**payload), read_only=read_only)
    identity_path = _identity_path(Path(vault_dir))
    return {"target": "identity", "field": field,
            "path": identity_path.relative_to(Path(vault_dir).resolve()).as_posix(),
            "title": field}


def answer_question(vault_dir: Path | str, question_id: str, text: str,
                    *, read_only: bool = False) -> dict[str, Any]:
    question, queue_path = resolve_question(vault_dir, question_id)
    if question is None:
        raise LookupError("没有这个问题")
    answer = str(text or "").strip()
    if not answer:
        raise ValueError("回答不能为空")
    ensure_vault_writable(read_only, "回答提问并写入自我档案")

    field = question.get("field")
    if field:
        current = read_identity(vault_dir)
        if str(current.get(field) or "").strip():
            # 这期间我已经在别处填了：不要覆盖，改成留一条记忆。
            result = _write_answer_note(vault_dir, {**question, "chapter": "memory"}, answer)
        else:
            result = _write_identity_field(vault_dir, field, answer, read_only=read_only)
    else:
        result = _write_answer_note(vault_dir, question, answer)

    state = load_state(vault_dir)
    state["answered"][question["id"]] = _now().isoformat(timespec="seconds")
    save_state(vault_dir, state)
    if queue_path is not None:
        consume_queue_item(queue_path)
    return {"ok": True, "question": question["id"], **result}


def skip_question(vault_dir: Path | str, question_id: str, *, forever: bool = False) -> dict[str, Any]:
    question, queue_path = resolve_question(vault_dir, question_id)
    if question is None:
        raise LookupError("没有这个问题")
    state = load_state(vault_dir)
    if forever:
        if question["id"] not in state["declined"]:
            state["declined"].append(question["id"])
        state["skipped"].pop(question["id"], None)
    else:
        state["skipped"][question["id"]] = _now().isoformat(timespec="seconds")
    save_state(vault_dir, state)
    if queue_path is not None:
        # 队列里的是「此刻针对我」的问题；跳过就当用过了，不再堆着。
        consume_queue_item(queue_path)
    return {"ok": True, "skipped": question["id"], "forever": bool(forever),
            "next": next_question(vault_dir)}


class AnswerPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=MAX_ANSWER)


class SkipPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=80)
    forever: bool = False


def create_questions_router(vault_dir: Path | str, *, read_only: bool = False,
                            prefix: str = "/api") -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["questions"])

    @router.get("/questions/next")
    def questions_next(force: bool = False):
        # force=true 就是卡片上的「再问一个」：我今天想多聊几个，不受一天一个的限制。
        return question_state(vault_dir, force=force)

    @router.post("/questions/answer")
    def questions_answer(payload: AnswerPayload):
        try:
            return answer_question(vault_dir, payload.id, payload.text, read_only=read_only)
        except LookupError as error:
            raise HTTPException(404, str(error)) from error
        except VaultReadOnlyError as error:
            raise HTTPException(409, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.post("/questions/skip")
    def questions_skip(payload: SkipPayload):
        try:
            return skip_question(vault_dir, payload.id, forever=payload.forever)
        except LookupError as error:
            raise HTTPException(404, str(error)) from error

    return router


__all__ = ["BANK", "STATE_RELATIVE", "QUEUE_DIR", "QUEUE_DONE", "load_state", "save_state",
           "load_queue", "consume_queue_item", "resolve_question",
           "next_question", "question_state", "answer_question", "skip_question",
           "create_questions_router"]
