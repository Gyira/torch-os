"""Goals and tasks domain module.

The module is intentionally independent from ``main.py``.  A caller can use
``GoalTaskService`` directly (for jobs or tests), or mount the router returned
by ``create_goals_router`` in an existing FastAPI application.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .db import Database, utc_now


GOAL_STATUSES = ("active", "paused", "completed", "archived")
TASK_STATUSES = ("todo", "in_progress", "completed", "cancelled")
TASK_PRIORITIES = ("low", "medium", "high", "urgent")

GoalStatus = Literal["active", "paused", "completed", "archived"]
TaskStatus = Literal["todo", "in_progress", "completed", "cancelled"]
TaskPriority = Literal["low", "medium", "high", "urgent"]


class DomainInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

    @model_validator(mode='before')
    @classmethod
    def reject_nonnullable_nulls(cls, values):
        if isinstance(values, dict):
            for field, value in values.items():
                if value is None and field not in {'goal_id', 'due_date', 'target_date'}:
                    raise ValueError(f'{field} cannot be null')
        return values


class GoalCreate(DomainInput):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10_000)
    category: str = Field(default="", max_length=80)
    status: GoalStatus = "active"
    target_date: date | None = None
    progress: int = Field(default=0, ge=0, le=100)
    metadata: dict[str, Any] = Field(default_factory=dict)


class GoalUpdate(DomainInput):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10_000)
    category: str | None = Field(default=None, max_length=80)
    status: GoalStatus | None = None
    target_date: date | None = None
    progress: int | None = Field(default=None, ge=0, le=100)
    metadata: dict[str, Any] | None = None


class GoalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str
    category: str
    status: GoalStatus
    target_date: date | None
    progress: int
    metadata: dict[str, Any]
    created_at: str
    updated_at: str


class TaskCreate(DomainInput):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10_000)
    goal_id: int | None = Field(default=None, gt=0)
    status: TaskStatus = "todo"
    priority: TaskPriority = "medium"
    due_date: date | None = None
    tags: list[str] = Field(default_factory=list, max_length=30)
    sort_order: int = Field(default=0, ge=0)


class TaskUpdate(DomainInput):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10_000)
    goal_id: int | None = Field(default=None, gt=0)
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    due_date: date | None = None
    tags: list[str] | None = Field(default=None, max_length=30)
    sort_order: int | None = Field(default=None, ge=0)


class TaskReorderItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int = Field(gt=0)
    updated_at: str = Field(min_length=1, max_length=64)


class TaskReorder(DomainInput):
    model_config = ConfigDict(extra="forbid")

    items: list[TaskReorderItem] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def unique_items(self):
        if len({item.id for item in self.items}) != len(self.items):
            raise ValueError("任务 id 不能重复")
        return self


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    goal_id: int | None
    title: str
    description: str
    status: TaskStatus
    priority: TaskPriority
    due_date: date | None
    completed_at: str | None
    tags: list[str]
    sort_order: int
    created_at: str
    updated_at: str


class EntityNotFound(ValueError):
    """Raised by the service when a requested goal or task does not exist."""


def _decode_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _goal_row(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["metadata"] = _decode_json(item.pop("metadata_json", None), {})
    return item


def _task_row(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["tags"] = _decode_json(item.pop("tags_json", None), [])
    return item


class GoalTaskService:
    """SQLite-backed CRUD service shared by API routes and future workers."""

    def __init__(self, database: Database):
        self.database = database
        # ``initialize`` is idempotent and keeps this module usable on its own.
        self.database.initialize()

    def create_goal(self, payload: GoalCreate) -> dict[str, Any]:
        now = utc_now()
        with self.database.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO goals
                (title, description, category, status, target_date, progress, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    payload.title.strip(),
                    payload.description,
                    payload.category.strip(),
                    payload.status,
                    payload.target_date.isoformat() if payload.target_date else None,
                    payload.progress,
                    json.dumps(payload.metadata, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            row = connection.execute("SELECT * FROM goals WHERE id=?", (cursor.lastrowid,)).fetchone()
        self.database.audit("goal_created", {"goal_id": int(cursor.lastrowid)})
        return _goal_row(row)

    def get_goal(self, goal_id: int) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM goals WHERE id=?", (goal_id,)).fetchone()
        return _goal_row(row) if row else None

    def list_goals(self, status: str | None = None, category: str | None = None) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("status=?")
            params.append(status)
        if category is not None:
            clauses.append("category=?")
            params.append(category)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM goals{where} ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'paused' THEN 1 ELSE 2 END, updated_at DESC",
                params,
            ).fetchall()
        return [_goal_row(row) for row in rows]

    def update_goal(self, goal_id: int, payload: GoalUpdate) -> dict[str, Any]:
        if not self.get_goal(goal_id):
            raise EntityNotFound("goal not found")
        values = payload.model_dump(exclude_unset=True)
        if not values:
            return self.get_goal(goal_id)  # type: ignore[return-value]
        assignments: list[str] = []
        params: list[Any] = []
        for field in ("title", "description", "category", "status", "target_date", "progress", "metadata"):
            if field not in values:
                continue
            value = values[field]
            if field == "title":
                value = value.strip()
            elif field == "category":
                value = value.strip()
            elif field == "target_date":
                value = value.isoformat() if value else None
            elif field == "metadata":
                value = json.dumps(value or {}, ensure_ascii=False)
            assignments.append(f"{field if field not in {'metadata'} else 'metadata_json'}=?")
            params.append(value)
        assignments.append("updated_at=?")
        params.extend([utc_now(), goal_id])
        with self.database.connect() as connection:
            connection.execute(f"UPDATE goals SET {', '.join(assignments)} WHERE id=?", params)
            row = connection.execute("SELECT * FROM goals WHERE id=?", (goal_id,)).fetchone()
        self.database.audit("goal_updated", {"goal_id": goal_id, "fields": sorted(values)})
        return _goal_row(row)

    def delete_goal(self, goal_id: int) -> bool:
        with self.database.connect() as connection:
            result = connection.execute("DELETE FROM goals WHERE id=?", (goal_id,))
        if result.rowcount:
            self.database.audit("goal_deleted", {"goal_id": goal_id})
            return True
        return False

    def create_task(self, payload: TaskCreate) -> dict[str, Any]:
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if payload.goal_id is not None and not connection.execute(
                "SELECT 1 FROM goals WHERE id=?", (payload.goal_id,)
            ).fetchone():
                raise EntityNotFound("goal not found")
            now = utc_now()
            cursor = connection.execute(
                """INSERT INTO tasks
                (goal_id, title, description, status, priority, due_date, completed_at, tags_json, sort_order, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    payload.goal_id,
                    payload.title.strip(),
                    payload.description,
                    payload.status,
                    payload.priority,
                    payload.due_date.isoformat() if payload.due_date else None,
                    now if payload.status == "completed" else None,
                    json.dumps(payload.tags, ensure_ascii=False),
                    payload.sort_order,
                    now,
                    now,
                ),
            )
            row = connection.execute("SELECT * FROM tasks WHERE id=?", (cursor.lastrowid,)).fetchone()
        self.database.audit("task_created", {"task_id": int(cursor.lastrowid), "goal_id": payload.goal_id})
        return _task_row(row)

    def get_task(self, task_id: int) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return _task_row(row) if row else None

    def list_tasks(
        self,
        goal_id: int | None = None,
        status: str | None = None,
        due_before: date | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if goal_id is not None:
            clauses.append("goal_id=?")
            params.append(goal_id)
        if status is not None:
            clauses.append("status=?")
            params.append(status)
        if due_before is not None:
            clauses.append("due_date IS NOT NULL AND due_date<=?")
            params.append(due_before.isoformat())
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM tasks{where} ORDER BY CASE status WHEN 'in_progress' THEN 0 WHEN 'todo' THEN 1 ELSE 2 END, due_date IS NULL, due_date, sort_order, updated_at DESC",
                params,
            ).fetchall()
        return [_task_row(row) for row in rows]

    def update_task(self, task_id: int, payload: TaskUpdate) -> dict[str, Any]:
        values = payload.model_dump(exclude_unset=True)
        assignments: list[str] = []
        params: list[Any] = []
        for field in ("title", "description", "goal_id", "status", "priority", "due_date", "tags", "sort_order"):
            if field not in values:
                continue
            value = values[field]
            if field == "title":
                value = value.strip()
            elif field == "due_date":
                value = value.isoformat() if value else None
            elif field == "tags":
                value = json.dumps(value or [], ensure_ascii=False)
            assignments.append(f"{field if field != 'tags' else 'tags_json'}=?")
            params.append(value)
        with self.database.connect() as connection:
            # Reserve the write transaction before reading status or validating the parent.
            # Otherwise a concurrent reopen can be overwritten using stale completion state.
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not current:
                raise EntityNotFound("task not found")
            if not values:
                return _task_row(current)
            if "goal_id" in values and values["goal_id"] is not None and not connection.execute(
                "SELECT 1 FROM goals WHERE id=?", (values["goal_id"],)
            ).fetchone():
                raise EntityNotFound("goal not found")
            now = utc_now()
            if "status" in values:
                completed_at = None
                if values["status"] == "completed":
                    # Imported checked items may have no known completion date.
                    # Only a real status transition can establish a new timestamp.
                    completed_at = current["completed_at"] if current["status"] == "completed" else now
                assignments.append("completed_at=?")
                params.append(completed_at)
            assignments.append("updated_at=?")
            params.extend([now, task_id])
            connection.execute(f"UPDATE tasks SET {', '.join(assignments)} WHERE id=?", params)
            row = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        self.database.audit("task_updated", {"task_id": task_id, "fields": sorted(values)})
        return _task_row(row)

    def reorder_tasks(self, payload: TaskReorder) -> dict[str, Any]:
        """Renumber the provided non-cancelled tasks in one transaction.

        ``updated_at`` is the conflict token: any task whose row changed since
        the client read it aborts the whole batch before the first write.
        """
        task_ids = [item.id for item in payload.items]
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            placeholders = ",".join("?" for _ in task_ids)
            rows = {row["id"]: row for row in connection.execute(
                f"SELECT * FROM tasks WHERE id IN ({placeholders})", task_ids
            ).fetchall()}
            for item in payload.items:
                row = rows.get(item.id)
                if row is None:
                    raise EntityNotFound("task not found")
                if row["status"] == "cancelled":
                    raise HTTPException(409, "已取消的任务不参与手动排序")
                if row["updated_at"] != item.updated_at:
                    raise HTTPException(409, "任务在其它页面有更新，请刷新后重新排序")
            now = utc_now()
            for position, item in enumerate(payload.items, start=1):
                connection.execute(
                    "UPDATE tasks SET sort_order=?, updated_at=? WHERE id=?",
                    (position * 10, now, item.id),
                )
        self.database.audit("tasks_reordered", {"count": len(task_ids)})
        return {"updated": len(task_ids), "ids": task_ids}

    def delete_task(self, task_id: int) -> bool:
        with self.database.connect() as connection:
            result = connection.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        if result.rowcount:
            self.database.audit("task_deleted", {"task_id": task_id})
            return True
        return False


def _validate_status(value: str | None, choices: tuple[str, ...], name: str) -> None:
    if value is not None and value not in choices:
        raise HTTPException(status_code=400, detail=f"不支持的{name}状态")


def create_goals_router(database: Database, prefix: str = "/api") -> APIRouter:
    """Create a router bound to a database instance.

    The factory avoids global state, making it straightforward to mount in the
    main application or exercise in a focused API test.
    """

    service = GoalTaskService(database)
    router = APIRouter(prefix=prefix, tags=["goals"])

    @router.get("/goals", response_model=list[GoalRead])
    def get_goals(status: str | None = Query(default=None), category: str | None = Query(default=None)):
        _validate_status(status, GOAL_STATUSES, "目标")
        return service.list_goals(status=status, category=category)

    @router.post("/goals", response_model=GoalRead, status_code=201)
    def post_goal(payload: GoalCreate):
        return service.create_goal(payload)

    @router.get("/goals/{goal_id}", response_model=GoalRead)
    def get_goal(goal_id: int):
        result = service.get_goal(goal_id)
        if not result:
            raise HTTPException(status_code=404, detail="目标不存在")
        return result

    @router.patch("/goals/{goal_id}", response_model=GoalRead)
    def patch_goal(goal_id: int, payload: GoalUpdate):
        try:
            return service.update_goal(goal_id, payload)
        except EntityNotFound as exc:
            raise HTTPException(status_code=404, detail="目标不存在") from exc

    @router.delete("/goals/{goal_id}", status_code=204)
    def remove_goal(goal_id: int):
        if not service.delete_goal(goal_id):
            raise HTTPException(status_code=404, detail="目标不存在")

    @router.get("/tasks", response_model=list[TaskRead])
    def get_tasks(
        goal_id: int | None = Query(default=None, gt=0),
        status: str | None = Query(default=None),
        due_before: date | None = Query(default=None),
    ):
        _validate_status(status, TASK_STATUSES, "任务")
        return service.list_tasks(goal_id=goal_id, status=status, due_before=due_before)

    @router.post("/tasks", response_model=TaskRead, status_code=201)
    def post_task(payload: TaskCreate):
        try:
            return service.create_task(payload)
        except EntityNotFound as exc:
            raise HTTPException(status_code=404, detail="目标不存在") from exc

    @router.post("/tasks/reorder")
    def reorder_tasks(payload: TaskReorder):
        try:
            return service.reorder_tasks(payload)
        except EntityNotFound as exc:
            raise HTTPException(status_code=404, detail="任务不存在") from exc

    @router.get("/tasks/{task_id}", response_model=TaskRead)
    def get_task(task_id: int):
        result = service.get_task(task_id)
        if not result:
            raise HTTPException(status_code=404, detail="任务不存在")
        return result

    @router.patch("/tasks/{task_id}", response_model=TaskRead)
    def patch_task(task_id: int, payload: TaskUpdate):
        try:
            return service.update_task(task_id, payload)
        except EntityNotFound as exc:
            detail = "目标不存在" if str(exc) == "goal not found" else "任务不存在"
            raise HTTPException(status_code=404, detail=detail) from exc

    @router.delete("/tasks/{task_id}", status_code=204)
    def remove_task(task_id: int):
        if not service.delete_task(task_id):
            raise HTTPException(status_code=404, detail="任务不存在")

    return router


__all__ = [
    "EntityNotFound",
    "GoalCreate",
    "GoalRead",
    "GoalTaskService",
    "GoalUpdate",
    "TaskCreate",
    "TaskRead",
    "TaskUpdate",
    "create_goals_router",
]
