"""Typed backend executors for role proposals; paths are owned by each module."""
from __future__ import annotations

from . import note_editor
from .habits import HabitCreate, HabitService
from .role_policy import RolePolicy
from .tasks import TaskCreate, create_task


def register_proposal_handlers(service, database, vault_dir, *, read_only=False):
    def checked(proposal):
        payload = dict(proposal["payload"])
        RolePolicy.for_role(proposal["role_id"], vault_dir).validate_action(
            proposal["kind"], payload, sources=proposal.get("sources"),
            thread_id=proposal.get("thread_id", ""))
        return payload

    def new_task(proposal, _edits):
        payload = checked(proposal)
        result = create_task(vault_dir, TaskCreate(
            title=str(payload.get("title") or proposal["title"]).strip(),
            list_name=str(payload.get("list_name") or ""),
            date=str(payload.get("date") or ""),
            priority=str(payload.get("priority") or "none"),
            tags=payload.get("tags") or [],
        ), database, read_only=read_only)
        return {"task": result.get("task"), "revision": result.get("revision")}

    def new_habit(proposal, _edits):
        payload = checked(proposal)
        result = HabitService(database, vault_dir=vault_dir, read_only=read_only).create(HabitCreate(
            title=str(payload.get("title") or proposal["title"]).strip(),
            cue=str(payload.get("cue") or ""),
            minimum_action=str(payload.get("minimum_action") or ""),
            **({"frequency": payload["frequency"]} if payload.get("frequency") is not None else {}),
            target_days=payload.get("target_days"),
            icon=str(payload.get("icon") or "🌱"), group=str(payload.get("group") or "默认"),
            reminder=str(payload.get("reminder") or ""),
        ))
        return {"habit": result}

    def new_note(proposal, _edits):
        payload = checked(proposal)
        note = note_editor.create_note(database, vault_dir,
            chapter=payload["chapter"], title=str(payload.get("title") or proposal["title"]),
            body=str(payload.get("body") or ""), tags=payload.get("tags") or [],
            source=str(payload.get("source") or ""), generated_by=proposal["role_id"],
            read_only=read_only)
        return {"note": note}

    def archive_note(proposal, _edits):
        payload = checked(proposal)
        note = note_editor.move_note(database, vault_dir, path=payload["path"],
            target_chapter=payload["target_chapter"],
            expected_revision=str(payload.get("expected_revision") or ""), read_only=read_only)
        return {"note": note}

    for kind, handler in {"new_task": new_task, "new_habit": new_habit,
                          "new_note": new_note, "archive_note": archive_note}.items():
        service.register(kind, handler)
