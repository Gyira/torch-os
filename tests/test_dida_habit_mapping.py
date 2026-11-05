"""滴答习惯纯映射测试。"""

from __future__ import annotations

import pytest

from backend.app.dida_client import RemoteHabit, RemoteHabitCheckin
from backend.app.dida_habit_mapping import (
    ABSENT_CHECKIN_HASH,
    build_remote_checkin_payload,
    build_remote_habit_payload,
    first_link_matches,
    local_habit_material,
    map_remote_habit,
    material_hash,
    parse_repeat_rule,
    remote_checkin_hash,
)


def make_habit(**overrides):
    values = dict(
        id="h" * 24, name="读书", color="#97e38b", icon_res="habit_daily_check_in",
        status=0, goal=1.0, step=0.0, unit="Count", habit_type="Boolean",
        repeat_rule="RRULE:FREQ=DAILY;INTERVAL=1", reminders=("20:00",),
        section_id="", target_days=21, target_start_date="20261101", sort_order=0,
        record_enable=False, total_check_ins=0, current_streak=0,
        completed_cycles=0, etag="e1", modified_time="",
        created_time="2026-11-01T00:00:00.000+0000", raw={},
    )
    values.update(overrides)
    return RemoteHabit(**values)


def test_repeat_rule_roundtrip_daily_interval_weekly_monthly():
    assert parse_repeat_rule("RRULE:FREQ=DAILY;INTERVAL=1")[0] == {"kind": "daily"}
    assert parse_repeat_rule("RRULE:FREQ=DAILY;INTERVAL=3")[0] == {
        "kind": "interval", "interval_days": 3}
    assert parse_repeat_rule("RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR")[0] == {
        "kind": "weekly", "weekdays": [0, 2, 4]}
    assert parse_repeat_rule("RRULE:FREQ=MONTHLY;BYMONTHDAY=1,15")[0] == {
        "kind": "monthly", "month_days": [1, 15]}


def test_parse_repeat_rule_tolerates_prefix_case_and_order():
    assert parse_repeat_rule("freq=weekly;byday=SU,MO;interval=1")[0] == {
        "kind": "weekly", "weekdays": [0, 6]}


def test_parse_repeat_rule_with_tt_times_is_unsupported():
    frequency, warnings = parse_repeat_rule("RRULE:FREQ=WEEKLY;TT_TIMES=5")
    assert frequency is None
    assert "habit_rrule_unsupported" in warnings


def test_parse_repeat_rule_extras_keep_base_rule():
    frequency, warnings = parse_repeat_rule("RRULE:FREQ=DAILY;INTERVAL=2;COUNT=10")
    assert frequency == {"kind": "interval", "interval_days": 2}
    assert "habit_rrule_extras_dropped" in warnings


def test_map_remote_habit_normalizes_and_clamps():
    mapped = map_remote_habit(make_habit(
        color="ff0000", reminders=("7:05", "08:00"), target_days=9999,
        target_start_date=20261101, repeat_rule="RRULE:FREQ=WEEKLY;BYDAY=MO,WE"))
    assert mapped.values["color"] == "#ff0000"
    assert mapped.values["reminder"] == "07:05"
    assert mapped.values["target_days"] == 3650
    assert mapped.values["frequency"] == {"kind": "weekly", "weekdays": [0, 2]}
    assert "habit_reminder_extra" in mapped.warnings
    assert "habit_target_days_clamped" in mapped.warnings


def test_first_link_matches_ignores_local_defaults():
    local = local_habit_material({
        "title": "读书", "color": "", "frequency": {"kind": "daily"},
        "start_date": "2026-11-05", "target_days": None, "reminder": "",
        "status": "active",
    })
    remote = {
        "title": "读书", "color": "#97e38b", "frequency": {"kind": "daily"},
        "start_date": "2026-11-01", "target_days": 21, "reminder": "20:00",
        "status": "active",
    }
    assert first_link_matches(local, remote, remote.keys())
    assert first_link_matches(local, {**remote, "frequency": {"kind": "weekly", "weekdays": [0]}},
                              remote.keys()) is False


def test_remote_hash_ignores_unmapped_fields():
    base = make_habit()
    changed = make_habit(icon_res="other", sort_order=99, section_id="section")
    assert map_remote_habit(base).remote_hash == map_remote_habit(changed).remote_hash


def test_build_remote_habit_payload_preserves_unmapped_fields_and_changes_selected_keys():
    habit = {
        "title": "晨跑", "color": "#00ff00", "frequency": {"kind": "weekly", "weekdays": [0, 2]},
        "start_date": "2026-11-01", "target_days": 30, "reminder": "07:00",
        "status": "active",
    }
    base = {
        "id": "h" * 24, "name": "跑步", "color": "#97e38b", "type": "Real",
        "goal": 5, "step": 1, "unit": "Count", "iconRes": "custom",
        "sectionId": "section-1", "sortOrder": -123, "etag": "e1",
        "repeatRule": "RRULE:FREQ=DAILY;INTERVAL=1;COUNT=3",
        "reminders": ["20:00"], "targetDays": 21, "targetStartDate": 20261101,
        "createdTime": "2026-01-01T00:00:00.000+0000",
    }
    payload = build_remote_habit_payload(
        habit, habit_id="h" * 24, base=base, changed_keys={"title", "frequency"})
    assert payload["name"] == "晨跑"
    assert payload["repeatRule"] == "RRULE:FREQ=WEEKLY;BYDAY=MO,WE"
    assert payload["type"] == "Real" and payload["goal"] == 5
    assert payload["iconRes"] == "custom" and payload["sectionId"] == "section-1"
    assert payload["sortOrder"] == -123 and payload["etag"] == "e1"
    assert "cue" not in payload and "priority" not in payload and "group" not in payload


def test_build_remote_habit_payload_keeps_original_repeat_rule_when_frequency_unchanged():
    habit = {
        "title": "跑步", "color": "", "frequency": {"kind": "daily"},
        "start_date": "2026-11-01", "target_days": None, "reminder": "",
        "status": "active",
    }
    base = {"id": "h" * 24, "repeatRule": "RRULE:FREQ=DAILY;INTERVAL=1;COUNT=3"}
    payload = build_remote_habit_payload(
        habit, habit_id="h" * 24, base=base, changed_keys={"color"})
    assert payload["repeatRule"] == "RRULE:FREQ=DAILY;INTERVAL=1;COUNT=3"


def test_checkin_payload_and_hash():
    payload = build_remote_checkin_payload(
        {"status": "done"}, checkin_id="c" * 24, habit_id="h" * 24,
        date_text="2026-11-03", base={"goal": 5})
    assert payload["status"] == 2 and payload["value"] == 5
    assert payload["checkinStamp"] == 20261103
    assert payload["checkinTime"]
    failed = build_remote_checkin_payload(
        {"status": "failed"}, checkin_id="c" * 24, habit_id="h" * 24,
        date_text="2026-11-03", base={"goal": 5})
    assert failed["status"] == 1 and failed["value"] == 0
    assert "checkinTime" not in failed
    with pytest.raises(ValueError):
        build_remote_checkin_payload(
            {"status": "skipped"}, checkin_id="c" * 24, habit_id="h" * 24,
            date_text="2026-11-03")
    assert remote_checkin_hash(None) == ABSENT_CHECKIN_HASH
    assert remote_checkin_hash({"status": 2}) != remote_checkin_hash({"status": 1})


def test_material_hash_is_stable_across_key_order():
    first = {"title": "读书", "status": "active"}
    second = {"status": "active", "title": "读书"}
    assert material_hash(first) == material_hash(second)
