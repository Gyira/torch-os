"""Run the committed fictional Chinese retrieval evaluation set.

The set lives in ``examples/retrieval-eval`` (fictional Markdown only, safe to
commit). This runner builds a throw-away database from that corpus, confirms
every paragraph exactly the way a user would, then scores each question
through the real ``Database.search_memories`` pipeline for every selected
provider (``none`` lexical baseline, ``hash`` local fallback, local
``ollama``). The user's Vault, database, audit log and review states are never
touched; the only files written are the temporary database and the optional
report under ``data/diagnostics``.

Usage:
    .venv\\Scripts\\python.exe scripts/retrieval_eval_set.py
    .venv\\Scripts\\python.exe scripts/retrieval_eval_set.py --provider none --provider hash --provider ollama --json data/diagnostics/retrieval-eval-set.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.db import Database  # noqa: E402
from backend.app.embedding import (  # noqa: E402
    EmbeddingError,
    HashEmbeddingProvider,
    OllamaEmbeddingProvider,
)
from backend.app.vault import scan_vault  # noqa: E402


DEFAULT_QUESTIONS = ROOT / "examples" / "retrieval-eval" / "questions.json"
CATEGORIES = ("intent", "paraphrase", "lexical", "negative")
PROVIDER_CHOICES = ("none", "hash", "ollama")
PROVIDER_LABELS = {"none": "词法基线", "hash": "hash-local", "ollama": "ollama"}
SHANGHAI = timezone(timedelta(hours=8))


class EvalSetError(ValueError):
    """The committed evaluation set is malformed or no longer matches the corpus."""


def _digest_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def corpus_digest(corpus_dir: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(path for path in corpus_dir.rglob("*") if path.is_file())
    for path in files:
        digest.update(path.relative_to(corpus_dir).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def load_eval_set(path: Path = DEFAULT_QUESTIONS) -> dict:
    """Parse and validate the question file; fail loudly on drift."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise EvalSetError(f"评测集无法读取或不是合法 JSON：{path}") from error
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise EvalSetError("评测集 schema 必须为 1")
    corpus = payload.get("corpus")
    if not isinstance(corpus, str) or not corpus.strip() or Path(corpus).is_absolute() or ".." in Path(corpus).parts:
        raise EvalSetError("corpus 必须是评测集目录内的相对路径")
    limit = payload.get("limit", 5)
    if not isinstance(limit, int) or not 1 <= limit <= 20:
        raise EvalSetError("limit 必须是 1 到 20 之间的整数")
    questions = payload.get("questions")
    if not isinstance(questions, list) or not questions:
        raise EvalSetError("questions 必须是非空列表")
    seen: set[str] = set()
    for index, item in enumerate(questions, start=1):
        if not isinstance(item, dict):
            raise EvalSetError(f"第 {index} 个问题不是对象")
        identifier = item.get("id")
        if not isinstance(identifier, str) or not identifier.strip() or identifier in seen:
            raise EvalSetError(f"第 {index} 个问题的 id 缺失或重复")
        seen.add(identifier)
        if item.get("category") not in CATEGORIES:
            raise EvalSetError(f"问题 {identifier} 的 category 不在 {CATEGORIES} 内")
        question = item.get("question")
        if not isinstance(question, str) or not question.strip():
            raise EvalSetError(f"问题 {identifier} 的 question 不能为空")
        expected = item.get("expected")
        if not isinstance(expected, list) or any(not isinstance(snippet, str) or not snippet.strip() for snippet in expected):
            raise EvalSetError(f"问题 {identifier} 的 expected 必须是非空字符串列表（负例用空列表）")
        if item["category"] == "negative" and expected:
            raise EvalSetError(f"负例 {identifier} 的 expected 必须为空")
        if item["category"] != "negative" and not expected:
            raise EvalSetError(f"问题 {identifier} 必须给出 expected 片段")
    return payload


def corpus_directory(payload: dict, questions_path: Path = DEFAULT_QUESTIONS) -> Path:
    corpus = (Path(questions_path).resolve().parent / payload["corpus"]).resolve()
    if not corpus.is_dir():
        raise EvalSetError(f"语料目录不存在：{corpus}")
    return corpus


def prepare_database(corpus_dir: Path, database_path: Path, provider=None) -> tuple[Database, dict]:
    """Scan the fictional vault and confirm every paragraph; temp files only."""
    database = Database(database_path, embedding_provider=provider)
    database.initialize()
    result = scan_vault(corpus_dir, database)
    if result["errors"]:
        raise EvalSetError("虚构语料扫描失败：" + "；".join(result["errors"]))
    candidates = database.list_memories("candidate")
    for memory in candidates:
        database.update_memory_status(memory["id"], "active")
    stats = {
        "documents": len(database.list_documents()),
        "memories": len(database.list_memories("active")),
        "scan": {key: result[key] for key in ("scanned", "changed", "skipped", "excluded")},
    }
    return database, stats


def resolve_expectations(database: Database, payload: dict) -> dict[str, dict]:
    """Map every expected paragraph prefix to exactly one active memory."""
    active = database.list_memories("active")
    resolved: dict[str, dict] = {}
    for item in payload["questions"]:
        for snippet in item["expected"]:
            if snippet in resolved:
                continue
            matches = [memory for memory in active if memory["content"].startswith(snippet)]
            if len(matches) != 1:
                raise EvalSetError(
                    f"期望片段在语料中匹配到 {len(matches)} 条记忆（必须恰好 1 条）：{snippet}"
                )
            memory = matches[0]
            resolved[snippet] = {
                "memory_id": int(memory["id"]),
                "title": memory["title"],
                "source_path": memory["source_path"],
                "snippet": memory["content"][:40],
            }
    return resolved


def _public_memory(row: dict) -> dict:
    return {
        "memory_id": int(row["id"]),
        "title": row["title"],
        "source_path": row.get("source_path"),
        "snippet": (row.get("content") or "")[:40],
        "lexical_score": row.get("lexical_score"),
        "semantic_score": row.get("semantic_score"),
        "score": row.get("score"),
    }


def evaluate_question(database: Database, item: dict, expected: dict[str, dict], limit: int) -> dict:
    preview = database.preview_memories(item["question"], limit)
    rows = preview["evidence"]
    counts = preview["counts"]
    positions = {int(row["id"]): index + 1 for index, row in enumerate(rows)}
    hits: list[dict] = []
    misses: list[str] = []
    expected_ids: set[int] = set()
    for snippet in item["expected"]:
        reference = expected[snippet]
        expected_ids.add(reference["memory_id"])
        rank = positions.get(reference["memory_id"])
        if rank:
            hits.append({"snippet": snippet, "memory_id": reference["memory_id"], "rank": rank})
        else:
            misses.append(snippet)
    count = len(item["expected"])
    first_rank = min((hit["rank"] for hit in hits), default=None)
    return {
        "id": item["id"],
        "category": item["category"],
        "question": item["question"],
        "limit": limit,
        "expected_count": count,
        "expected": [expected[snippet] for snippet in item["expected"]],
        "retrieved": [_public_memory(row) for row in rows],
        "retrieved_count": len(rows),
        "matched_total": counts["matched_total"],
        "usable_total": counts["usable_total"],
        "hits": hits,
        "misses": misses,
        "hit_count": len(hits),
        "recall": round(len(hits) / count, 4) if count else 1.0,
        "full_hit": bool(count and len(hits) == count),
        "mrr": round(1 / first_rank, 4) if first_rank else 0.0,
        "unexpected_count": len([row for row in rows if int(row["id"]) not in expected_ids]),
        "clean": (not item["expected"]) and not rows,
        "note": item.get("note", ""),
    }


def _summary(entries: list[dict]) -> dict:
    positive = [entry for entry in entries if entry["expected_count"] > 0]
    negatives = [entry for entry in entries if entry["expected_count"] == 0]

    def group_metrics(group: list[dict]) -> dict:
        if not group:
            return {"questions": 0}
        return {
            "questions": len(group),
            "hit_any": sum(1 for entry in group if entry["hit_count"] > 0),
            "full_hit": sum(1 for entry in group if entry["full_hit"]),
            "mean_recall": round(sum(entry["recall"] for entry in group) / len(group), 4),
            "mrr": round(sum(entry["mrr"] for entry in group) / len(group), 4),
        }

    metrics = group_metrics(positive)
    if positive:
        metrics["hit_any_rate"] = round(metrics["hit_any"] / len(positive), 4)
        metrics["full_hit_rate"] = round(metrics["full_hit"] / len(positive), 4)
    by_category = {
        category: group_metrics([entry for entry in positive if entry["category"] == category])
        for category in CATEGORIES
        if any(entry["category"] == category for entry in positive)
    }
    clean = sum(1 for entry in negatives if entry["clean"])
    return {
        "positive": metrics,
        "by_category": by_category,
        "negative": {
            "questions": len(negatives),
            "clean": clean,
            "clean_rate": round(clean / len(negatives), 4) if negatives else None,
        },
    }


def run_provider(payload: dict, corpus_dir: Path, provider_name: str, provider, workdir: Path) -> dict:
    if provider is not None:
        try:
            provider.embed(["检索评测预热"])
        except EmbeddingError as error:
            return {
                "requested": provider_name,
                "provider": getattr(provider, "name", provider_name),
                "available": False,
                "error": str(error),
                "questions": [],
                "summary": None,
            }
    database, stats = prepare_database(corpus_dir, workdir / f"{provider_name}.sqlite3", provider=provider)
    expected = resolve_expectations(database, payload)
    limit = int(payload.get("limit", 5))
    entries = _evaluate_all(database, payload, expected, limit)
    return {
        "requested": provider_name,
        "provider": getattr(provider, "name", None),
        "available": True,
        "error": None,
        "corpus": stats,
        "questions": entries,
        "summary": _summary(entries),
    }


def _evaluate_all(database: Database, payload: dict, expected: dict[str, dict], limit: int) -> list[dict]:
    return [evaluate_question(database, item, expected, limit) for item in payload["questions"]]


def sweep_thresholds(
    payload: dict, corpus_dir: Path, provider, workdir: Path, thresholds: list[float]
) -> dict:
    """Re-score the same questions under different semantic-only admission gates.

    The blend weight and ordering are untouched; only candidates with zero
    lexical hits are re-gated. Vectors are computed once and reused.
    """
    database, stats = prepare_database(corpus_dir, workdir / "threshold-sweep.sqlite3", provider=provider)
    expected = resolve_expectations(database, payload)
    limit = int(payload.get("limit", 5))
    original = Database.SEMANTIC_ONLY_THRESHOLD
    steps: list[dict] = []
    try:
        for threshold in thresholds:
            Database.SEMANTIC_ONLY_THRESHOLD = threshold
            entries = _evaluate_all(database, payload, expected, limit)
            summary = _summary(entries)
            negatives = [entry for entry in entries if entry["expected_count"] == 0]
            selected_negative = [row for entry in negatives for row in entry["retrieved"]]
            steps.append({
                "threshold": threshold,
                "positive": summary["positive"],
                "by_category": summary["by_category"],
                "negative": summary["negative"],
                "negative_selected": len(selected_negative),
                "negative_max_semantic": round(
                    max((row["semantic_score"] or 0.0) for row in selected_negative), 4
                ) if selected_negative else None,
            })
    finally:
        Database.SEMANTIC_ONLY_THRESHOLD = original
    return {
        "provider": getattr(provider, "name", "ollama"),
        "corpus": stats,
        "steps": steps,
    }


def compare_providers(baseline: dict, other: dict) -> dict:
    base_by_id = {entry["id"]: entry for entry in baseline["questions"]}
    improved: list[dict] = []
    declined: list[dict] = []
    for entry in other["questions"]:
        before = base_by_id.get(entry["id"])
        if before is None:
            continue
        if entry["expected_count"] == 0:
            if entry["clean"] and not before["clean"]:
                improved.append({"id": entry["id"], "recall_delta": 0.0, "mrr_delta": 0.0, "reason": "负例恢复干净"})
            elif before["clean"] and not entry["clean"]:
                declined.append({"id": entry["id"], "recall_delta": 0.0, "mrr_delta": 0.0, "reason": "负例出现误报"})
            continue
        recall_delta = round(entry["recall"] - before["recall"], 4)
        mrr_delta = round(entry["mrr"] - before["mrr"], 4)
        record = {"id": entry["id"], "recall_delta": recall_delta, "mrr_delta": mrr_delta}
        if recall_delta > 0 or (recall_delta == 0 and mrr_delta > 0):
            improved.append(record)
        elif recall_delta < 0 or (recall_delta == 0 and mrr_delta < 0):
            declined.append(record)
    return {
        "baseline": baseline["requested"],
        "provider": other["requested"],
        "improved": improved,
        "declined": declined,
    }


def _resolve_provider(name: str, model: str):
    if name == "none":
        return None
    if name == "hash":
        return HashEmbeddingProvider()
    if name == "ollama":
        return OllamaEmbeddingProvider(model=model)
    raise EvalSetError(f"未知向量提供者：{name}")


def _print_provider(entry: dict) -> None:
    label = entry["provider"] or "词法基线（无向量）"
    if not entry["available"]:
        print(f"[{entry['requested']}] 不可用：{entry['error']}")
        return
    summary = entry["summary"]
    positive = summary["positive"]
    negative = summary["negative"]
    print(
        f"[{label}] 正例 {positive['questions']} 题 · 至少命中 {positive['hit_any']} · "
        f"全命中 {positive['full_hit']} · 平均召回 {positive['mean_recall']} · MRR {positive['mrr']}"
    )
    for category, metrics in summary["by_category"].items():
        print(
            f"    {category:<12} 全命中 {metrics['full_hit']}/{metrics['questions']} · "
            f"平均召回 {metrics['mean_recall']} · MRR {metrics['mrr']}"
        )
    if negative["questions"]:
        print(f"    负例干净 {negative['clean']}/{negative['questions']}")
    for entry_item in entry["questions"]:
        if entry_item["expected_count"] and not entry_item["full_hit"]:
            print(
                f"    未全命中：{entry_item['id']} {entry_item['hit_count']}/{entry_item['expected_count']}"
                f"（缺：{', '.join(entry_item['misses']) or '无'}）"
            )
        if entry_item["expected_count"] == 0 and not entry_item["clean"]:
            print(f"    负例误报：{entry_item['id']} 返回 {entry_item['retrieved_count']} 条")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", default=str(DEFAULT_QUESTIONS), help="评测集 JSON 路径")
    parser.add_argument(
        "--provider", action="append", choices=PROVIDER_CHOICES,
        help="可重复；默认 none 与 hash（离线）。ollama 需要本机服务。",
    )
    parser.add_argument("--model", default="bge-m3", help="ollama 向量模型名")
    parser.add_argument("--limit", type=int, default=0, help="每题返回条数；默认取评测集设置")
    parser.add_argument("--json", dest="json_path", default="", help="报告输出路径")
    parser.add_argument("--keep-workdir", default="", help="保留临时数据库目录（调试用）")
    parser.add_argument("--sweep", action="store_true", help="额外扫描“仅语义准入”阈值（使用本机 Ollama）")
    parser.add_argument("--sweep-thresholds", default="0.35,0.45,0.5,0.55,0.6", help="逗号分隔的阈值列表")
    args = parser.parse_args(argv)

    questions_path = Path(args.questions).resolve()
    providers = args.provider or ["none", "hash"]
    try:
        payload = load_eval_set(questions_path)
        corpus_dir = corpus_directory(payload, questions_path)
        if args.limit:
            if not 1 <= args.limit <= 20:
                raise EvalSetError("limit 必须是 1 到 20 之间的整数")
            payload["limit"] = args.limit
        corpus_hash = corpus_digest(corpus_dir)
    except EvalSetError as error:
        print(f"评测集无效：{error}")
        return 2

    thresholds: list[float] = []
    if args.sweep:
        try:
            thresholds = sorted({float(value) for value in args.sweep_thresholds.split(",") if value.strip()})
        except ValueError:
            print("阈值扫描参数无效：--sweep-thresholds 必须是逗号分隔的数字")
            return 2
        if not thresholds or any(not 0.0 <= value <= 1.0 for value in thresholds):
            print("阈值扫描参数无效：阈值必须在 0 到 1 之间")
            return 2

    print(f"评测集：{payload.get('name', questions_path.name)}")
    print(f"语料：{corpus_dir}（digest {corpus_hash[:16]}…）")
    print(f"问题：{len(payload['questions'])} 题 · Top-{payload.get('limit', 5)} · 提供者 {'、'.join(providers)}")
    print()

    reports: list[dict] = []
    keep = Path(args.keep_workdir).resolve() if args.keep_workdir else None
    manager = None
    if keep:
        keep.mkdir(parents=True, exist_ok=True)
        workdir = keep
    else:
        manager = tempfile.TemporaryDirectory(prefix="retrieval-eval-set-")
        workdir = Path(manager.name)
    try:
        for name in providers:
            try:
                provider = _resolve_provider(name, args.model)
            except EmbeddingError as error:
                print(f"[{name}] 配置无效：{error}")
                continue
            entry = run_provider(payload, corpus_dir, name, provider, workdir)
            reports.append(entry)
            _print_provider(entry)
        available = [entry for entry in reports if entry["available"]]
        if len(available) > 1:
            baseline = available[0]
            print()
            for entry in available[1:]:
                comparison = compare_providers(baseline, entry)
                print(
                    f"{entry['provider']} 相对 {baseline['provider']}：提升 {len(comparison['improved'])} 题 · "
                    f"回退 {len(comparison['declined'])} 题"
                )

        sweep_report = None
        if thresholds:
            try:
                sweep_provider = _resolve_provider("ollama", args.model)
                sweep_provider.embed(["检索评测预热"])
            except EmbeddingError as error:
                print(f"\n阈值扫描跳过：本机 Ollama 不可用（{error}）")
            else:
                sweep_report = sweep_thresholds(payload, corpus_dir, sweep_provider, workdir, thresholds)
                print(f"\n阈值扫描（{sweep_report['provider']} · 仅语义单独准入阈值）：")
                for step in sweep_report["steps"]:
                    positive = step["positive"]
                    paraphrase = step["by_category"].get("paraphrase", {})
                    negative = step["negative"]
                    leak = step["negative_max_semantic"]
                    print(
                        f"    {step['threshold']:.2f}  正例全命中 {positive.get('full_hit', 0)}/{positive.get('questions', 0)}"
                        f" · 改写 {paraphrase.get('full_hit', 0)}/{paraphrase.get('questions', 0)}"
                        f" · 负例干净 {negative.get('clean', 0)}/{negative.get('questions', 0)}"
                        f" · 负例最高入选相似度 {leak if leak is not None else '—'}"
                    )

        report = {
            "schema": 1,
            "checked_at": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
            "eval_set": {
                "name": payload.get("name"),
                "questions_path": str(questions_path),
                "questions_digest": _digest_bytes(questions_path.read_bytes()),
                "corpus": str(corpus_dir),
                "corpus_digest": corpus_hash,
                "limit": payload.get("limit", 5),
            },
            "providers": reports,
            "comparisons": [
                compare_providers(available[0], entry) for entry in available[1:]
            ] if len(available) > 1 else [],
            "threshold_sweep": sweep_report,
            "cloud_calls": 0,
            "personal_data": False,
            "notes": [
                "语料和问题均为虚构内容，可提交到版本库；运行只写临时数据库和可选报告。",
                "词法基线不加载任何向量；hash 为离线兜底；ollama 仅访问本机 127.0.0.1。",
            ],
        }
        if args.json_path:
            target = Path(args.json_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"\n报告：{target}")
    finally:
        if manager is not None:
            manager.cleanup()

    if not any(entry["available"] for entry in reports):
        print("没有任何提供者可用，未完成评测。")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
