import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from threading import Event, Thread

import pytest
from pydantic import ValidationError

from backend.app import goals
from backend.app.db import Database
from backend.app.goals import EntityNotFound, GoalCreate, GoalTaskService, GoalUpdate, TaskCreate, TaskUpdate


@pytest.fixture
def service(tmp_path: Path) -> GoalTaskService:
    return GoalTaskService(Database(tmp_path / "tasks.sqlite3"))


def test_reopen_clears_completion_and_recompletion_records_new_time(service, monkeypatch):
    task = service.create_task(TaskCreate(title="Finish a chapter"))
    monkeypatch.setattr(goals, "utc_now", lambda: "2026-10-03T01:00:00+00:00")
    completed = service.update_task(task["id"], TaskUpdate(status="completed"))
    assert completed["completed_at"] == "2026-10-03T01:00:00+00:00"
    monkeypatch.setattr(goals, "utc_now", lambda: "2026-10-03T02:00:00+00:00")
    reopened = service.update_task(task["id"], TaskUpdate(status="todo"))
    assert reopened["status"] == "todo"
    assert reopened["completed_at"] is None
    completed_again = service.update_task(task["id"], TaskUpdate(status="completed"))
    assert completed_again["completed_at"] == "2026-10-03T02:00:00+00:00"
    assert completed_again["updated_at"] == completed_again["completed_at"]


@pytest.mark.parametrize("status", ["todo", "in_progress", "cancelled"])
def test_leaving_completed_state_always_clears_timestamp(service, status):
    task = service.create_task(TaskCreate(title="Completion lifecycle", status="completed"))
    updated = service.update_task(task["id"], TaskUpdate(status=status))
    assert updated["status"] == status
    assert updated["completed_at"] is None


def test_completed_description_edit_preserves_time_and_does_not_write_completion_column(service, monkeypatch):
    task = service.create_task(TaskCreate(title="Completed item", status="completed"))
    original_completed_at = task["completed_at"]
    with service.database.connect() as connection:
        connection.execute("""CREATE TRIGGER prevent_unrequested_completion_write
            BEFORE UPDATE OF completed_at ON tasks BEGIN
            SELECT RAISE(ABORT, 'Completion timestamp must not be written for a description edit');
            END""")
    monkeypatch.setattr(goals, "utc_now", lambda: "2026-10-03T04:00:00+00:00")
    updated = service.update_task(task["id"], TaskUpdate(description="Add review notes"))
    assert updated["description"] == "Add review notes"
    assert updated["status"] == "completed"
    assert updated["completed_at"] == original_completed_at
    assert updated["updated_at"] == "2026-10-03T04:00:00+00:00"


def test_repeated_completed_status_is_idempotent_for_completion_time(service, monkeypatch):
    task = service.create_task(TaskCreate(title="Already done", status="completed"))
    monkeypatch.setattr(goals, "utc_now", lambda: "2026-10-03T04:00:00+00:00")
    updated = service.update_task(task["id"], TaskUpdate(status="completed", description="Verified"))
    assert updated["completed_at"] == task["completed_at"]
    assert updated["description"] == "Verified"


@pytest.mark.parametrize("fields", [
    {"status": "completed"},
    {"status": "completed", "description": "Imported item reviewed"},
    {"description": "Imported item reviewed"},
])
def test_imported_completion_without_date_stays_unknown_on_repeat_or_edit(service, monkeypatch, fields):
    task = service.create_task(TaskCreate(title="Imported checked item", status="completed"))
    with service.database.connect() as connection:
        connection.execute("UPDATE tasks SET completed_at=NULL WHERE id=?", (task["id"],))
        if "status" not in fields:
            connection.execute("""CREATE TRIGGER prevent_unrequested_unknown_completion_write
                BEFORE UPDATE OF completed_at ON tasks BEGIN
                SELECT RAISE(ABORT, 'Description edit must not write an unknown completion date');
                END""")
    monkeypatch.setattr(goals, "utc_now", lambda: "2026-10-03T04:00:00+00:00")
    updated = service.update_task(task["id"], TaskUpdate(**fields))
    assert updated["status"] == "completed"
    assert updated["completed_at"] is None
    assert updated["updated_at"] == "2026-10-03T04:00:00+00:00"
    if "description" in fields:
        assert updated["description"] == fields["description"]


@pytest.mark.parametrize("reopened_status", ["todo", "in_progress", "cancelled"])
def test_unknown_completed_item_gets_a_date_only_after_real_recompletion(service, monkeypatch, reopened_status):
    task = service.create_task(TaskCreate(title="Imported done without date", status="completed"))
    with service.database.connect() as connection:
        connection.execute("UPDATE tasks SET completed_at=NULL WHERE id=?", (task["id"],))
    monkeypatch.setattr(goals, "utc_now", lambda: "2026-10-03T04:00:00+00:00")
    reopened = service.update_task(task["id"], TaskUpdate(status=reopened_status))
    assert reopened["status"] == reopened_status
    assert reopened["completed_at"] is None
    monkeypatch.setattr(goals, "utc_now", lambda: "2026-10-03T05:00:00+00:00")
    completed = service.update_task(task["id"], TaskUpdate(status="completed"))
    assert completed["completed_at"] == "2026-10-03T05:00:00+00:00"
    monkeypatch.setattr(goals, "utc_now", lambda: "2026-10-03T06:00:00+00:00")
    repeated = service.update_task(task["id"], TaskUpdate(status="completed"))
    assert repeated["completed_at"] == completed["completed_at"]


def test_explicit_completion_repairs_a_stale_timestamp_from_an_old_todo_row(service, monkeypatch):
    task = service.create_task(TaskCreate(title="Legacy row"))
    with service.database.connect() as connection:
        connection.execute("UPDATE tasks SET completed_at='old-stale-time' WHERE id=?", (task["id"],))
    monkeypatch.setattr(goals, "utc_now", lambda: "2026-10-03T04:00:00+00:00")
    updated = service.update_task(task["id"], TaskUpdate(status="completed"))
    assert updated["completed_at"] == "2026-10-03T04:00:00+00:00"


def test_missing_parent_rejects_whole_update_without_mutation(service):
    parent = service.create_goal(GoalCreate(title="Existing parent"))
    task = service.create_task(TaskCreate(title="Keep this", goal_id=parent["id"]))
    with pytest.raises(EntityNotFound, match="goal not found"):
        service.update_task(task["id"], TaskUpdate(goal_id=parent["id"] + 1000, status="completed", description="Must not persist"))
    assert service.get_task(task["id"]) == task
    with service.database.connect() as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT COUNT(*) FROM audit_events WHERE event_type='task_updated'").fetchone()[0] == 0


def test_task_can_detach_from_parent_and_deleted_parent_keeps_task_history(service):
    parent = service.create_goal(GoalCreate(title="Study"))
    task = service.create_task(TaskCreate(title="Task", goal_id=parent["id"], status="completed"))
    detached = service.update_task(task["id"], TaskUpdate(goal_id=None))
    assert detached["goal_id"] is None
    assert detached["completed_at"] == task["completed_at"]
    service.update_task(task["id"], TaskUpdate(goal_id=parent["id"]))
    assert service.delete_goal(parent["id"])
    surviving = service.get_task(task["id"])
    assert surviving["goal_id"] is None
    assert surviving["completed_at"] == task["completed_at"]


def test_empty_update_is_read_only_and_unknown_task_is_reported(service):
    task = service.create_task(TaskCreate(title="Untouched"))
    assert service.update_task(task["id"], TaskUpdate()) == task
    with service.database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM audit_events WHERE event_type='task_updated'").fetchone()[0] == 0
    with pytest.raises(EntityNotFound, match="task not found"):
        service.update_task(task["id"] + 1000, TaskUpdate(description="Missing"))


def _pause_after_read(monkeypatch, database: Database, statement: str) -> tuple[Event, Event]:
    """Pause an actual read so a second SQLite writer can probe its lock."""
    read_finished = Event()
    release_read = Event()
    original_connect = database.connect

    class Cursor:
        def __init__(self, cursor):
            self.cursor = cursor

        def fetchone(self):
            row = self.cursor.fetchone()
            read_finished.set()
            if not release_read.wait(5):
                raise RuntimeError("Test did not release the database read")
            return row

        def __getattr__(self, name):
            return getattr(self.cursor, name)

    class Connection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, sql, parameters=()):
            cursor = self.connection.execute(sql, parameters)
            if sql == statement and not read_finished.is_set():
                return Cursor(cursor)
            return cursor

        def __getattr__(self, name):
            return getattr(self.connection, name)

    @contextmanager
    def connect():
        with original_connect() as connection:
            yield Connection(connection)

    monkeypatch.setattr(database, "connect", connect)
    return read_finished, release_read


def _start_worker(operation):
    outcome = {}

    def run():
        try:
            outcome["result"] = operation()
        except BaseException as error:
            outcome["error"] = error

    thread = Thread(target=run, daemon=True)
    thread.start()
    return thread, outcome


def test_description_edit_reserves_transaction_before_reading_task(service, monkeypatch):
    task = service.create_task(TaskCreate(title="Race target", status="completed"))
    read_finished, release_read = _pause_after_read(monkeypatch, service.database, "SELECT * FROM tasks WHERE id=?")
    thread, outcome = _start_worker(lambda: service.update_task(task["id"], TaskUpdate(description="Concurrent edit")))
    try:
        assert read_finished.wait(5)
        # BEGIN IMMEDIATE cannot succeed until the read-and-update transaction releases.
        # This would succeed when get_task() reads using a separate unreserved connection.
        with closing(sqlite3.connect(service.database.path, timeout=0)) as contender:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                contender.execute("BEGIN IMMEDIATE")
    finally:
        release_read.set()
        thread.join(5)
    assert not thread.is_alive()
    assert "error" not in outcome
    assert outcome["result"]["completed_at"] == task["completed_at"]
    reopened = service.update_task(task["id"], TaskUpdate(status="todo"))
    assert reopened["completed_at"] is None
    assert reopened["description"] == "Concurrent edit"


@pytest.mark.parametrize("operation", ["create", "update"])
def test_parent_validation_and_task_write_share_one_reserved_transaction(service, monkeypatch, operation):
    parent = service.create_goal(GoalCreate(title="Parent protected during validation"))
    existing = service.create_task(TaskCreate(title="Existing task"))
    read_finished, release_read = _pause_after_read(monkeypatch, service.database, "SELECT 1 FROM goals WHERE id=?")
    if operation == "create":
        action = lambda: service.create_task(TaskCreate(title="New task", goal_id=parent["id"]))
    else:
        action = lambda: service.update_task(existing["id"], TaskUpdate(goal_id=parent["id"]))
    thread, outcome = _start_worker(action)
    try:
        assert read_finished.wait(5)
        with closing(sqlite3.connect(service.database.path, timeout=0)) as contender:
            contender.execute("PRAGMA foreign_keys=ON")
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                contender.execute("DELETE FROM goals WHERE id=?", (parent["id"],))
    finally:
        release_read.set()
        thread.join(5)
    assert not thread.is_alive()
    assert "error" not in outcome
    assert outcome["result"]["goal_id"] == parent["id"]
    with service.database.connect() as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert service.delete_goal(parent["id"])
    assert service.get_task(outcome["result"]["id"])["goal_id"] is None


@pytest.mark.parametrize("model", [GoalCreate, GoalUpdate, TaskCreate, TaskUpdate])
@pytest.mark.parametrize("values", [{"title": None}, {"title": "   "}, {"description": None}, {"status": None}])
def test_null_and_blank_input_validation_remains_in_force(model, values):
    payload = {"title": "Default valid title", **values}
    with pytest.raises(ValidationError):
        model(**payload)
