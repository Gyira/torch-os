"""Markdown-backed sources replacing the retired goals/learning/body backends."""

from __future__ import annotations

from datetime import date

from backend.app import body_source, goal_notes, pillar_notes, structured_notes, task_notes, task_reports


def test_body_source_aggregates_health_month_rows_by_day(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    structured_notes.upsert_row(vault, "health", "2026-09", {
        "date": "2026-09-30", "weight_kg": "69.1", "sleep_minutes": "420",
    })
    structured_notes.upsert_row(vault, "health", "2026-10", {
        "date": "2026-10-03", "weight_kg": "68.5", "sleep_minutes": "450",
    })
    structured_notes.upsert_row(vault, "health", "2026-10", {
        "date": "2026-10-03", "weight_kg": "68.4", "exercise_minutes": "30",
    })
    structured_notes.upsert_row(vault, "health", "2026-10", {
        "date": "2026-10-04", "note": "只有备注，不算身体记录",
    })

    rows = body_source.entries_between(vault, date(2026, 9, 1), date(2026, 10, 31))
    assert [row["date"] for row in rows] == ["2026-09-30", "2026-10-03"]
    assert rows[1] == {
        "date": "2026-10-03", "recorded": True, "weight_kg": 68.4,
        "sleep_minutes": 450, "exercise_minutes": 30, "revision": 0, "updated_at": None,
    }
    assert body_source.day_entry(vault, "2026-10-04")["recorded"] is False
    all_rows = body_source.all_entries(vault)
    assert [row["date"] for row in all_rows] == ["2026-09-30", "2026-10-03"]


def test_goal_notes_list_and_resolve_only_goal_chapters(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    annual = pillar_notes.create_note(
        vault, pillar="life", chapter="goal_year", title="年度目标", note_type="goal",
        body="今年完成一件事。")
    monthly = pillar_notes.create_note(
        vault, pillar="life", chapter="goal_month", title="十月目标", note_type="goal")
    pillar_notes.create_note(
        vault, pillar="life", chapter="journal", title="不是目标", note_type="journal")

    rows = goal_notes.list_goals(vault)
    assert [row["title"] for row in rows] == ["年度目标", "十月目标"]
    assert [row["chapter"] for row in rows] == ["goal_year", "goal_month"]
    assert goal_notes.resolve_goal(vault, annual["path"])["title"] == "年度目标"
    assert goal_notes.resolve_goal(vault, monthly["path"])["title"] == "十月目标"
    assert goal_notes.resolve_goal(vault, "00_人生路/01_日记/2026-10-22.md") is None
    assert goal_notes.resolve_goal(vault, "03_知识库/00_收件箱/不存在.md") is None
    assert goal_notes.count_active(vault) == 2


def test_task_reports_read_markdown_notes_and_keep_goal_and_import_key(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    base = {"priority": "none", "created": "2026-10-01"}
    open_note = task_notes.create_task_note(vault, "工作", "写方案", {
        **base, "date": "2026-10-20", "priority": "high",
        "goal": "00_人生路/06_目标与计划/01_年度目标/年度目标.md",
        "import_key": "a" * 64,
    })
    task_notes.create_task_note(vault, "工作", "以后再说", {**base, "date": "2026-12-01"})
    done_note = task_notes.create_task_note(vault, "工作", "已做完", {
        **base, "done": True, "done_on": "2026-10-03",
    })
    note = task_notes.read_task_note(vault, open_note["path"])
    assert note["goal"].endswith("年度目标.md")
    assert note["import_key"] == "a" * 64

    assert sorted(row["title"] for row in task_reports.open_tasks(vault)) == sorted(["写方案", "以后再说"])
    assert [row["title"] for row in task_reports.due_on(vault, "2026-10-20")] == ["写方案"]
    assert [row["title"] for row in task_reports.overdue(vault, "2026-10-22")] == ["写方案"]
    assert [row["title"] for row in task_reports.completed_between(vault, "2026-10-01", "2026-10-31")] == ["已做完"]
    assert [row["title"] for row in task_reports.next_open(vault, 1)] == ["写方案"]
    report = task_reports.report_task(note)
    assert report["status"] == "todo"
    assert report["due_date"] == "2026-10-20"
    assert report["goal"].endswith("年度目标.md")
    assert report["key"] == open_note["key"]
    assert done_note["done"] is True
