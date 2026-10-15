import json
import math
from pathlib import Path

from backend.app.db import Database
from backend.app.embedding import EmbeddingError, HashEmbeddingProvider


class ConceptProvider:
    """Test double: vector[0]=锻炼/运动, vector[1]=睡眠, vector[2]=专注."""

    name = "fake-concept"

    def __init__(self):
        self.calls = 0
        self.texts: list[str] = []

    def embed(self, texts):
        self.calls += 1
        self.texts.extend(texts)
        vectors = []
        for text in texts:
            vector = [
                1.0 if ("锻炼" in text or "运动" in text) else 0.0,
                1.0 if "睡眠" in text else 0.0,
                1.0 if "专注" in text else 0.0,
            ]
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            vectors.append([value / norm for value in vector])
        return vectors


class BrokenProvider:
    name = "fake-broken"

    def embed(self, texts):
        raise EmbeddingError("本地向量服务不可用")


class GradedProvider:
    """Deterministic stand-in with controllable cosine similarity per paragraph."""

    name = "fake-graded"

    def embed(self, texts):
        vectors = []
        for text in texts:
            if "如何提高外语水平" in text or "外语学习方法" in text:
                vectors.append([1.0, 0.0])
            elif "英文播客" in text:
                vectors.append([0.8, 0.6])
            elif "公园散步" in text:
                vectors.append([0.42, math.sqrt(1 - 0.42 ** 2)])
            else:
                vectors.append([0.0, 1.0])
        return vectors


def insert_memory(database: Database, title: str, content: str) -> int:
    database.initialize()
    document_id, _ = database.upsert_document(f"{title}.md", title, content, "a" * 64, {"source_kind": "user-authored"})
    database.replace_document_memories(document_id, [{
        "kind": "note", "title": title, "content": content, "source_quote": content, "confidence": "explicit",
    }])
    memory = database.list_memories("candidate")[0]
    database.update_memory_status(memory["id"], "active")
    return memory["id"]


def test_lexical_only_when_no_provider_is_configured(tmp_path):
    database = Database(tmp_path / "app.sqlite3")
    insert_memory(database, "运动记录", "规律运动会改善睡眠质量")
    results = database.search_memories("锻炼对身体好", 6)
    assert results == []
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0] == 0


def test_semantic_provider_adds_concept_matches_and_keeps_lexical_first(tmp_path):
    database = Database(tmp_path / "app.sqlite3", embedding_provider=ConceptProvider())
    semantic_id = insert_memory(database, "运动记录", "规律运动会改善睡眠质量")
    lexical_id = insert_memory(database, "锻炼打卡", "今天完成锻炼计划")
    results = database.search_memories("锻炼对身体好", 6)
    ids = [row["id"] for row in results]
    assert ids == [lexical_id, semantic_id]  # lexical+semantic ranks above semantic-only
    by_id = {row["id"]: row for row in results}
    assert by_id[semantic_id]["lexical_score"] == 0
    assert by_id[semantic_id]["semantic_score"] >= 0.35
    assert by_id[lexical_id]["lexical_score"] > 0
    assert "score" in by_id[lexical_id]
    with database.connect() as connection:
        cached = connection.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0]
        statuses = {row["id"]: (row["status"], row["revision"]) for row in connection.execute("SELECT id,status,revision FROM memories")}
    assert cached == 2
    assert statuses[semantic_id][0] == "active" and statuses[lexical_id][0] == "active"


def test_semantic_only_hits_below_annotation_line_are_low_confidence(tmp_path):
    database = Database(tmp_path / "app.sqlite3", embedding_provider=GradedProvider())
    high_id = insert_memory(database, "播客记录", "每天通勤听英文播客")
    low_id = insert_memory(database, "散步记录", "周末去公园散步")
    lexical_id = insert_memory(database, "学习方法", "外语学习方法总结")

    results = database.search_memories("如何提高外语水平", 6)
    by_id = {row["id"]: row for row in results}
    assert by_id[high_id]["low_confidence"] is False
    assert by_id[low_id]["low_confidence"] is True
    assert by_id[low_id]["semantic_score"] < Database.LOW_CONFIDENCE_SEMANTIC
    assert by_id[lexical_id]["low_confidence"] is False
    assert by_id[lexical_id]["lexical_score"] > 0


def test_embedding_cache_reuses_unchanged_content_and_refreshes_on_edit(tmp_path):
    provider = ConceptProvider()
    database = Database(tmp_path / "app.sqlite3", embedding_provider=provider)
    memory_id = insert_memory(database, "运动记录", "规律运动会改善睡眠质量")
    database.search_memories("睡眠", 6)
    assert provider.calls == 2  # one batch for memories, one for the query
    database.search_memories("睡眠", 6)
    assert provider.calls == 3  # cache hit: only the query embedding is new
    with database.connect() as connection:
        connection.execute("UPDATE memories SET content=?, revision=revision+1 WHERE id=?", ("更新后的内容", memory_id))
    database.search_memories("睡眠", 6)
    assert provider.calls == 5  # content change invalidates the cache entry
    with database.connect() as connection:
        row = connection.execute("SELECT content_hash FROM memory_embeddings WHERE memory_id=?", (memory_id,)).fetchone()
    assert row["content_hash"]


def test_provider_switch_reindexes_without_touching_review_state(tmp_path):
    database = Database(tmp_path / "app.sqlite3", embedding_provider=ConceptProvider())
    memory_id = insert_memory(database, "运动记录", "规律运动会改善睡眠质量")
    database.search_memories("睡眠", 6)
    with database.connect() as connection:
        before = connection.execute("SELECT provider,revision FROM memory_embeddings m JOIN memories x ON x.id=m.memory_id WHERE m.memory_id=?", (memory_id,)).fetchone()
    assert before["provider"] == "fake-concept"
    database.embedding_provider = HashEmbeddingProvider()
    database.search_memories("睡眠", 6)
    with database.connect() as connection:
        after = connection.execute("SELECT provider FROM memory_embeddings WHERE memory_id=?", (memory_id,)).fetchone()
        memory = connection.execute("SELECT status,revision FROM memories WHERE id=?", (memory_id,)).fetchone()
    assert after["provider"] == "hash-local"
    assert memory["status"] == "active" and memory["revision"] == 2


def test_embedding_failure_degrades_to_lexical_without_raising(tmp_path):
    database = Database(tmp_path / "app.sqlite3", embedding_provider=BrokenProvider())
    insert_memory(database, "睡眠记录", "睡眠时长记录")
    results = database.search_memories("睡眠", 6)
    assert [row["title"] for row in results] == ["睡眠记录"]
    assert "semantic_score" not in results[0]
    preview = database.preview_memories("睡眠", 6)
    assert preview["evidence"][0]["title"] == "睡眠记录"
    assert preview["counts"]["selected"] == 1


def test_preview_invariants_hold_with_semantics(tmp_path):
    database = Database(tmp_path / "app.sqlite3", embedding_provider=ConceptProvider())
    insert_memory(database, "运动记录", "规律运动会改善睡眠质量")
    insert_memory(database, "睡眠记录", "睡眠时长记录")
    preview = database.preview_memories("锻炼对身体好", 6)
    counts = preview["counts"]
    excluded = preview["excluded"]
    assert counts["active_total"] == counts["selected"] + excluded["source_unavailable"] + excluded["not_personal"] + excluded["stale_source"] + excluded["expired"] + excluded["not_matching"] + excluded["over_limit"]
    assert counts["usable_total"] == counts["matched_total"] + excluded["not_matching"]
    assert counts["matched_total"] == counts["selected"] + excluded["over_limit"]
