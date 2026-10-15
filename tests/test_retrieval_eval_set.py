import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.db import Database  # noqa: E402
from backend.app.embedding import HashEmbeddingProvider  # noqa: E402
from scripts.retrieval_eval_set import (  # noqa: E402
    DEFAULT_QUESTIONS,
    EvalSetError,
    corpus_directory,
    load_eval_set,
    main,
    prepare_database,
    resolve_expectations,
    run_provider,
    sweep_thresholds,
)


class ConceptProvider:
    """Deterministic stand-in for a semantic model: exercise / sleep / reading."""

    name = "fake-concept-eval"
    GROUPS = (
        (("锻炼", "运动", "训练", "拉伸", "深蹲", "慢跑", "身体"), 0),
        (("睡",), 1),
        (("读", "书", "阅读"), 2),
    )

    def embed(self, texts):
        vectors = []
        for text in texts:
            values = [0.0, 0.0, 0.0]
            for words, index in self.GROUPS:
                if any(word in text for word in words):
                    values[index] = 1.0
            norm = math.sqrt(sum(value * value for value in values)) or 1.0
            vectors.append([value / norm for value in values])
        return vectors


def write_questions(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "questions.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_eval_set_is_well_formed_and_covers_every_memory(tmp_path):
    payload = load_eval_set(DEFAULT_QUESTIONS)
    corpus = corpus_directory(payload, DEFAULT_QUESTIONS)
    identifiers = [item["id"] for item in payload["questions"]]
    assert len(identifiers) == len(set(identifiers))
    assert {"intent", "paraphrase", "lexical", "negative"} <= {
        item["category"] for item in payload["questions"]
    }

    database, stats = prepare_database(corpus, tmp_path / "eval.sqlite3")
    assert stats["documents"] == 16
    assert stats["memories"] == 33
    assert stats["scan"]["excluded"] == 0

    resolved = resolve_expectations(database, payload)
    active_ids = {memory["id"] for memory in database.list_memories("active")}
    assert {reference["memory_id"] for reference in resolved.values()} == active_ids


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["questions"][1].update(id=payload["questions"][0]["id"]),
        lambda payload: payload["questions"][0].update(category="unknown"),
        lambda payload: payload["questions"][0].update(expected=[]),
        lambda payload: payload["questions"][-1].update(expected=["不应存在"]),
        lambda payload: payload.update(corpus="../vault"),
    ],
)
def test_invalid_eval_sets_are_rejected(tmp_path, mutate):
    payload = json.loads(json.dumps(load_eval_set(DEFAULT_QUESTIONS)))
    mutate(payload)
    with pytest.raises(EvalSetError):
        load_eval_set(write_questions(tmp_path, payload))


def test_eval_set_missing_file_is_rejected(tmp_path):
    with pytest.raises(EvalSetError):
        load_eval_set(tmp_path / "missing.json")


def test_offline_baseline_and_hash_are_deterministic(tmp_path):
    payload = load_eval_set(DEFAULT_QUESTIONS)
    corpus = corpus_directory(payload, DEFAULT_QUESTIONS)
    workdir = tmp_path / "work"
    workdir.mkdir()

    baseline = run_provider(payload, corpus, "none", None, workdir)
    assert baseline["available"] is True
    summary = baseline["summary"]
    assert summary["positive"]["questions"] == 36
    # Baseline recorded after the 0.43.0 sentences-v3 extraction (readable content).
    assert summary["positive"]["full_hit"] == 30
    assert summary["negative"]["questions"] == 6
    assert summary["negative"]["clean"] == 5
    missed = {
        entry["id"] for entry in baseline["questions"]
        if entry["expected_count"] and not entry["full_hit"]
    }
    assert missed == {
        "paraphrase-exercise", "paraphrase-sleep", "paraphrase-books",
        "paraphrase-buying", "intent-preferences", "lexical-reading-rhythm",
    }

    repeated = run_provider(payload, corpus, "none", None, workdir)
    assert repeated["summary"] == summary
    assert repeated["questions"] == baseline["questions"]

    hashed = run_provider(payload, corpus, "hash", HashEmbeddingProvider(), workdir)
    assert hashed["available"] is True
    assert hashed["summary"]["positive"]["full_hit"] == 29
    assert hashed["summary"]["negative"]["clean"] == 5


def test_concept_provider_recovers_paraphrase_cases(tmp_path):
    payload = load_eval_set(DEFAULT_QUESTIONS)
    corpus = corpus_directory(payload, DEFAULT_QUESTIONS)
    workdir = tmp_path / "work"
    workdir.mkdir()

    entry = run_provider(payload, corpus, "fake", ConceptProvider(), workdir)
    assert entry["available"] is True
    assert entry["provider"] == "fake-concept-eval"
    full_hits = {item["id"] for item in entry["questions"] if item["full_hit"]}
    assert {"paraphrase-exercise", "paraphrase-sleep", "paraphrase-books"} <= full_hits
    assert entry["summary"]["positive"]["full_hit"] == 34
    assert entry["summary"]["negative"]["clean"] == 5


def test_threshold_sweep_is_ordered_and_restores_gate(tmp_path):
    payload = load_eval_set(DEFAULT_QUESTIONS)
    corpus = corpus_directory(payload, DEFAULT_QUESTIONS)
    workdir = tmp_path / "work"
    workdir.mkdir()
    original = Database.SEMANTIC_ONLY_THRESHOLD

    sweep = sweep_thresholds(payload, corpus, HashEmbeddingProvider(), workdir, [0.0, 1.0])
    assert [step["threshold"] for step in sweep["steps"]] == [0.0, 1.0]
    low, high = sweep["steps"]
    assert low["negative_selected"] > 0
    # One weather question still matches lexically, so closing the semantic
    # gate alone cannot make every negative clean on this corpus.
    assert high["negative_selected"] == 1
    assert low["negative_selected"] > high["negative_selected"]
    assert low["positive"]["questions"] == 36
    assert Database.SEMANTIC_ONLY_THRESHOLD == original


def test_cli_writes_report_without_personal_data(tmp_path):
    target = tmp_path / "report.json"
    code = main(["--provider", "none", "--json", str(target)])
    assert code == 0
    report = json.loads(target.read_text(encoding="utf-8"))
    assert report["schema"] == 1
    assert report["cloud_calls"] == 0
    assert report["personal_data"] is False
    assert report["eval_set"]["corpus_digest"]
    assert len(report["providers"]) == 1
    assert report["providers"][0]["summary"]["negative"]["clean"] == 5
