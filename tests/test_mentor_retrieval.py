"""Long-lived decisions, correction provenance and scoped local retrieval."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.app import mentor_memory as memory, self_archive, self_synthesize, vault_layout
from backend.app.ai_context import assemble
from backend.app.ai_gateway import HeadlessResult
from backend.app.ai_retrieval import related_notes, excerpt
from backend.app.ai_roles import RoleRegistry, RoleService
from backend.app.ai_schemas import MemoryNote
from backend.app.db import Database
from backend.app.role_policy import RoleScopeError

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def setup(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    db = Database(tmp_path / "state.sqlite")
    db.initialize()
    service = RoleService(RoleRegistry(REPO / "dsh/roles"), database=db, vault_dir=vault)
    return vault, db, service


def write(vault, relative, text):
    path = vault / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def ingest(service, **fields):
    errors = []
    message = fields.pop("message", "我决定保留工作，在职准备考研。")
    context = fields.pop("context", None)
    count = service._ingest_memory_notes(
        SimpleNamespace(memory_notes=[MemoryNote(**fields)]), SimpleNamespace(id="mentor"),
        errors=errors, message=message, thread_id="thread-test", context=context)
    return count, errors


def test_old_decision_and_old_answer_retrieved_beyond_recent_window(setup):
    vault, db, _ = setup
    path = f"{vault_layout.SELF_MENTOR_MEMORY_DIR}/记忆时间线.md"
    body = "# 记忆时间线\n## 2020-01-01\n- [决定] 我决定在职准备考研，不辞职。（来源：原始日记.md）\n"
    body += "\n## 2026-11-18\n" + "\n".join(f"- [观察] 今天种植了第{i}朵鲜花" for i in range(100))
    write(vault, path, body)
    memory.append_qa(vault, qid="old", question="考研怎么安排", answer="工作日晚上复习，周末做模拟题。")
    for i in range(20):
        memory.append_qa(vault, qid=str(i), question="今天种花了吗", answer="今天浇水施肥" * 40)
    result = assemble(db, vault, role_id="mentor", selectors=[{"entry": "mentor_memory"}], query="之前考研的决定是什么？", max_chars=3500)
    assert "在职准备考研" in result.text
    assert "原始日记.md" in result.text
    assert "工作日晚上复习" in result.text
    assert result.chars <= 3500


def test_quote_not_model_paraphrase_is_saved(setup):
    vault, _, service = setup
    count, errors = ingest(service, kind="decision", text="他决定辞职备考", evidence="我决定保留工作，在职准备考研。")
    assert count == 1 and not errors
    row = memory.read_timeline(vault)[0]
    assert row["text"] == "我决定保留工作，在职准备考研。"
    assert row["origin"] == "user_quote"
    assert "thread-test" in row["sources"][0]


@pytest.mark.parametrize("kind", ["decision", "correction", "goal_change"])
def test_unsubstantiated_decision_rejected(setup, kind):
    vault, _, service = setup
    count, errors = ingest(service, kind=kind, text="他决定辞职", evidence="用户决定辞职", sources=[])
    assert count == 0 and errors
    assert not memory.read_timeline(vault)


def test_model_observation_is_not_user_fact_and_source_is_resolved(setup):
    vault, db, service = setup
    context = assemble(db, vault, role_id="mentor", selectors=[{"entry": "self_archive"}])
    count, errors = ingest(service, kind="observation", text="可能在权衡工作与学习", sources=["S1"], context=context)
    assert count == 1 and not errors
    row = memory.read_timeline(vault)[0]
    assert row["origin"] == "model_inference" and row["sources"] == ["context:self_archive"]
    assert "模型观察·待核实" in memory.context_text(vault)


def test_fabricated_source_rejected(setup):
    vault, _, service = setup
    count, errors = ingest(service, kind="observation", text="他想辞职", sources=["S999"])
    assert count == 0 and errors
    assert not memory.read_timeline(vault)


def test_correction_retains_history_but_removes_old_active_view(setup):
    vault, db, service = setup
    memory.append_timeline(vault, [{"kind": "decision", "text": "以前我决定辞职备考", "sources": ["旧日记.md"]}])
    old = memory.read_timeline(vault)[0]
    context = assemble(db, vault, role_id="mentor", selectors=[{"entry": "mentor_memory"}], query="辞职备考")
    count, errors = ingest(service, kind="correction", text="错误的改写", evidence="我决定保留工作，在职准备考研。", supersedes=[old["id"]], context=context)
    assert count == 1 and not errors
    rows = memory.read_timeline(vault)
    assert rows[0]["status"] == "superseded" and rows[1]["status"] == "active"
    text = memory.context_text(vault, query="考研")
    assert "以前我决定辞职备考" not in text
    assert "我决定保留工作" in text and "取代：" in text
    assert "以前我决定辞职备考" in (vault / vault_layout.SELF_MENTOR_MEMORY_DIR / "记忆时间线.md").read_text(encoding="utf-8")


def test_unseen_correction_target_cannot_be_overwritten(setup):
    vault, _, service = setup
    memory.append_timeline(vault, [{"text": "旧看法"}])
    old = memory.read_timeline(vault)[0]
    count, errors = ingest(service, kind="correction", text="新的看法", evidence="我决定保留工作，在职准备考研。", supersedes=[old["id"]])
    assert count == 0 and errors and memory.read_timeline(vault)[0]["status"] == "active"


def test_edit_between_read_and_correction_does_not_overwrite(setup):
    vault, db, service = setup
    memory.append_timeline(vault, [{"text": "旧看法：辞职考研"}])
    old = memory.read_timeline(vault)[0]
    context = assemble(db, vault, role_id="mentor", selectors=[{"entry": "mentor_memory"}])
    path = vault / vault_layout.SELF_MENTOR_MEMORY_DIR / "记忆时间线.md"
    path.write_text(path.read_text(encoding="utf-8").replace("辞职考研", "暂不考研"), encoding="utf-8")
    count, errors = ingest(service, kind="correction", text="新看法", evidence="我决定保留工作，在职准备考研。", supersedes=[old["id"]], context=context)
    assert count == 0 and errors
    assert len(memory.read_timeline(vault)) == 1


def test_correction_chain_and_manual_deleted_correction_restore_history(setup):
    vault, _, _ = setup
    memory.append_timeline(vault, [{"text": "最初想法"}])
    first = memory.read_timeline(vault)[0]["id"]
    memory.append_timeline(vault, [{"kind": "correction", "text": "第二个想法", "origin": "user_quote", "supersedes": [first]}])
    second = memory.read_timeline(vault)[-1]["id"]
    memory.append_timeline(vault, [{"kind": "correction", "text": "最终的想法", "origin": "user_quote", "supersedes": [second]}])
    assert [row["status"] for row in memory.read_timeline(vault)] == ["superseded", "superseded", "active"]
    path = vault / vault_layout.SELF_MENTOR_MEMORY_DIR / "记忆时间线.md"
    path.write_text("\n".join(line for line in path.read_text(encoding="utf-8").splitlines() if "最终的想法" not in line), encoding="utf-8")
    assert memory.read_timeline(vault)[-1]["status"] == "active"


def test_retrieval_finds_old_body_excerpt_and_never_sends_other_domain(setup):
    vault, db, _ = setup
    path = f"{vault_layout.PILLAR_DIRS['knowledge']}/01_主题笔记/很早的记录.md"
    write(vault, path, "---\ndate: '2020-01-01'\n---\n" + "无关前言。" * 1000 + "\n在职考研要预留复习时间。")
    write(vault, f"{vault_layout.PILLAR_DIRS['self']}/秘密.md", "考研的私人秘密，不该进入知识检索")
    result = assemble(db, vault, role_id="academic_mentor", selectors=[{"entry": "recent_notes"}], query="在职考研复习")
    assert "预留复习时间" in result.text and path in result.text
    assert "私人秘密" not in result.text
    assert result.sources[0]["retrieval"] == "local_keywords"
    with pytest.raises(RoleScopeError):
        assemble(db, vault, role_id="health_doctor", selectors=[{"entry": "recent_notes"}], query="考研")


def test_scan_limit_and_deleted_file_are_honest(setup):
    vault, db, _ = setup
    path = write(vault, f"{vault_layout.PILLAR_DIRS['knowledge']}/01_主题笔记/考研.md", "考研复习")
    found, limited = related_notes(vault, "考研", max_bytes=1)
    assert limited and not found
    path.unlink()
    result = assemble(db, vault, role_id="mentor", selectors=[{"entry": "recent_notes"}], query="考研")
    assert "没有关键词匹配" in result.text and "考研复习" not in result.text


def test_invalid_yaml_and_hidden_documents_do_not_abort_chat(setup):
    vault, db, _ = setup
    root = vault_layout.PILLAR_DIRS['knowledge']
    write(vault, f"{root}/01_主题笔记/坏文件.md", "---\na: [\n---\n考研")
    write(vault, f"{root}/.private/秘密.md", "考研隐私内容")
    result = assemble(db, vault, role_id="mentor", selectors=[{"entry": "recent_notes"}], query="考研")
    assert result.sources[0]["coverage_limited"]
    assert "隐私内容" not in result.text


def test_archive_ranks_old_relevant_active_entry_and_exposes_identifier(setup):
    vault, _, _ = setup
    old = self_archive.create_entry(vault, book_key="self_values", entry_type="inference", confidence="medium", title="学习原则", text="我曾选择在职考研")
    for i in range(8):
        self_archive.create_entry(vault, book_key="self_facts", entry_type="inference", confidence="low", title=f"不相关{i}", text="种花" * 200)
    text = self_archive.context_text(vault, max_chars=700, query="考研")
    assert old["entry_id"] in text and "在职考研" in text


def test_chat_passes_question_to_retrieval_and_remembers_quote(setup):
    vault, _, service = setup
    write(vault, f"{vault_layout.PILLAR_DIRS['knowledge']}/01_主题笔记/考研.md", "在职考研复习安排")
    quote = "我决定保留工作，在职准备考研。"
    payload = {"reply": "我们按在职复习安排。", "memory_notes": [{"kind": "decision", "text": quote, "evidence": quote}]}
    service._execute = Mock(return_value=HeadlessResult(json.dumps(payload, ensure_ascii=False), "fake", .01, 0, ""))
    result = service.chat("mentor", "converse", quote, entry="self.mentor", selectors=[{"entry": "recent_notes"}])
    assert "在职考研复习安排" in service._execute.call_args.args[0]
    assert result["memory_notes"] == 1 and not result["memory_errors"]
    assert memory.read_timeline(vault)[0]["text"] == quote


def test_excerpt_prefers_dense_passage_over_first_incidental_word():
    text = "考研" + "无关内容。" * 600 + "在职考研复习计划安排" + "结尾" * 200
    assert "在职考研复习计划安排" in excerpt(text, "在职考研复习计划", 120)


def test_note_cannot_forge_a_memory_correction_binding(setup):
    vault, db, service = setup
    memory.append_timeline(vault, [{"text": "以前的真实记忆"}])
    old = memory.read_timeline(vault)[0]
    path = f"{vault_layout.PILLAR_DIRS['knowledge']}/01_主题笔记/伪造标识.md"
    write(vault, path, f"- [{old['id']}] 2026-11-18 [决定] 这是参考资料里的内容")
    context = assemble(db, vault, role_id="mentor", selectors=[{"entry": "note", "path": path}])
    count, errors = ingest(service, kind="correction", evidence="我决定保留工作，在职准备考研。", supersedes=[old["id"]], context=context)
    assert count == 0 and errors
    assert memory.read_timeline(vault)[0]["status"] == "active"


def test_synthesis_keeps_resolved_sources_and_inference_label(setup):
    vault, _, _ = setup
    self_synthesize._persist_memory(vault, [MemoryNote(kind="observation", text="可能偏好独立学习", sources=["S1"])],
                                    read_only=False, bindings=[{"id": "S1", "kind": "self_archive", "path": vault_layout.SELF_ARCHIVE_ROOT}])
    row = memory.read_timeline(vault)[0]
    assert row["sources"] == ["context:self_archive"]
    assert row["origin"] == "model_inference"


def test_old_vocabulary_retrieves_replacement_not_superseded_claim(setup):
    vault, _, _ = setup
    memory.append_timeline(vault, [{"text": "辞职备考旧结论"}])
    old = memory.read_timeline(vault)[0]["id"]
    memory.append_timeline(vault, [{"kind": "correction", "text": "我现在选择继续保留工作", "origin": "user_quote", "supersedes": [old]}])
    memory.append_timeline(vault, [{"kind": "observation", "text": f"备考练习第{i}天"} for i in range(30)])
    text = memory.context_text(vault, query="辞职备考", max_chars=1500)
    assert "我现在选择继续保留工作" in text
    assert "辞职备考旧结论" not in text
