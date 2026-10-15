"""Read-and-compare retrieval evaluation for a 薪火OS database.

Runs the same scoring pipeline the workbench uses, once without embeddings
(lexical only) and once with the selected local provider, so the effect of
semantic blending can be inspected question by question. Memory rows, review
states, conversations and audit events are never modified; the only write is
the derived ``memory_embeddings`` cache used by the semantic run.

Usage:
    .venv\\Scripts\\python.exe scripts/retrieval_eval.py --db data/torch_os.sqlite3 --question "我是谁？"
    .venv\\Scripts\\python.exe scripts/retrieval_eval.py --provider hash --json data/diagnostics/retrieval-eval.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.db import Database  # noqa: E402
from backend.app.embedding import EmbeddingError, HashEmbeddingProvider, OllamaEmbeddingProvider, provider_from_environment  # noqa: E402


DEFAULT_QUESTIONS = [
    "我是谁？",
    "我当前的主要目标是什么？",
    "我过去反复遇到什么问题？",
    "根据我的记录，我最近应该优先做什么？",
]


def resolve(args) -> object:
    if args.provider == "none":
        return None
    if args.provider == "hash":
        return HashEmbeddingProvider()
    if args.provider == "ollama":
        return OllamaEmbeddingProvider(model=args.model)
    return provider_from_environment()


def summarize(rows: list[dict], limit: int) -> list[dict]:
    return [{
        "memory_id": row["id"],
        "title": row["title"],
        "kind": row.get("kind"),
        "lexical_score": row.get("lexical_score"),
        "semantic_score": row.get("semantic_score"),
        "score": row.get("score"),
    } for row in rows[:limit]]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(ROOT / "data" / "torch_os.sqlite3"))
    parser.add_argument("--provider", choices=["auto", "hash", "ollama", "none"], default="auto")
    parser.add_argument("--model", default="bge-m3", help="Ollama embedding model for --provider ollama")
    parser.add_argument("--question", action="append", help="Question to evaluate; repeatable")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--json", dest="json_path", default="")
    args = parser.parse_args()

    path = Path(args.db)
    if not path.is_file():
        print(f"数据库不存在：{path}")
        return 2

    try:
        semantic_provider = resolve(args)
    except EmbeddingError as error:
        print(f"向量提供者不可用：{error}")
        return 2

    questions = args.question or DEFAULT_QUESTIONS
    lexical_db = Database(path, embedding_provider=None)
    report: dict[str, object] = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "database": str(path),
        "provider": getattr(semantic_provider, "name", None),
        "questions": [],
        "cloud_calls": 0,
        "memory_writes": 0,
    }
    semantic_error = None
    for question in questions:
        lexical = lexical_db.search_memories(question, args.limit)
        entry: dict[str, object] = {"question": question, "lexical": summarize(lexical, args.limit), "semantic": None}
        if semantic_provider is not None:
            semantic_db = Database(path, embedding_provider=semantic_provider)
            try:
                semantic = semantic_db.search_memories(question, args.limit)
                entry["semantic"] = summarize(semantic, args.limit)
            except EmbeddingError as error:
                semantic_error = str(error)
                entry["semantic"] = {"error": semantic_error}
        report["questions"].append(entry)
        print(f"\n问：{question}")
        print("  词法：" + ("；".join(f"[{row['memory_id']}] {row['title']}" for row in entry["lexical"]) or "（无匹配）"))
        if isinstance(entry["semantic"], list):
            print("  语义：" + ("；".join(f"[{row['memory_id']}] {row['title']}（语义 {row['semantic_score']}）" for row in entry["semantic"]) or "（无匹配）"))
        elif isinstance(entry["semantic"], dict):
            print(f"  语义：不可用（{entry['semantic'].get('error')}）")
    report["semantic_error"] = semantic_error

    if args.json_path:
        target = Path(args.json_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\n报告：{target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
