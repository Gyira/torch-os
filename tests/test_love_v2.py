"""恋爱系统 v2：新目录、记忆本子、微信聊天、排班覆盖、相册、八字、大师。"""

from __future__ import annotations

import io
import json
import sqlite3
import time
from datetime import datetime as dt
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import backend.app.love_chat as love_chat
from backend.app.application import create_app
from backend.app.db import Database
from backend.app.love import SCHEMA_SQL
from backend.app.love_bazi import create_bazi_router
from backend.app.love_chat import _connect as chat_connect
from backend.app.love_chat import ensure_chat_schema
from backend.app.love_gallery import create_gallery_router
from backend.app.love_layout import ensure_love_layout, love_paths, LOVE_ROOT
from backend.app.love_master import create_master_router, ensure_master_schema
from backend.app.love_migrate import migrate_love_v2
from backend.app.weflow import WeFlowMessage, WeFlowPull, WeFlowSession


def make_app(tmp_path: Path) -> tuple[TestClient, Path]:
    database = Database(tmp_path / "torch_os.sqlite3")
    vault = tmp_path / "vault"
    app = create_app(
        database, vault, session_token="test-token", security_environ={},
        env_file=tmp_path / ".env", love_legacy_migration=False,
    )
    return TestClient(app), vault


def test_love_layout_and_markdown_memory(tmp_path: Path) -> None:
    client, vault = make_app(tmp_path)
    headers = {"x-api-token": "test-token"}
    root = vault / LOVE_ROOT
    assert (root / "01_恋爱记忆" / "恋爱日志").is_dir()
    assert client.get("/api/love/memory/overview", headers=headers).status_code == 200
    protected = client.post(
        "/api/notebooks/delete", headers=headers,
        json={"pillar": "love", "key": "love_journal", "confirm": True},
    )
    assert protected.status_code == 422, protected.text
    assert (root / "01_恋爱记忆" / "恋爱日志").is_dir()

    # 恋爱大师的三个固定产物本子随应用启动创建，同样是默认本子，不能删。
    overview = client.get("/api/love/memory/overview", headers=headers).json()
    for name in ("关系画像", "深度分析", "阶段报告"):
        row = next(item for item in overview["notebooks"] if item["title"] == name)
        assert row["protected"] is True
        blocked = client.post(
            "/api/notebooks/delete", headers=headers,
            json={"pillar": "love", "key": row["key"], "confirm": True},
        )
        assert blocked.status_code == 422, blocked.text
        assert (root / "01_恋爱记忆" / name).is_dir()

    created = client.post(
        "/api/notebooks", headers=headers,
        json={"pillar": "love", "name": "旅行"},
    )
    assert created.status_code == 200, created.text
    key = created.json()["key"]
    assert key == "custom:love:旅行"

    note = client.post(
        "/api/writing/notes", headers=headers,
        json={"chapter": key, "title": "第一次旅行", "body": "海边的风", "date": "2026-10-01"},
    )
    assert note.status_code == 200, note.text
    payload = note.json()
    assert payload["pillar"] == "love"
    path = payload["path"]

    overview = client.get("/api/love/memory/overview", headers=headers).json()
    assert overview["total"] == 1
    assert any(item["key"] == key and item["count"] == 1 for item in overview["notebooks"])

    moved = client.post(
        "/api/writing/notes/move", headers=headers,
        json={"path": path, "target_chapter": "love_journal"},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["chapter"] == "love_journal"

    exported = client.get("/api/love/memory/export", headers=headers)
    assert exported.status_code == 200
    assert "第一次旅行" in exported.text

    # 普通自建本子不受保护，仍可删除。
    removed = client.post(
        "/api/notebooks/delete", headers=headers,
        json={"pillar": "love", "key": key, "confirm": True},
    )
    assert removed.status_code == 200, removed.text


def _fake_weflow(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        def __init__(self, token: str, port: int = 5031, *, timeout: float = 30.0):
            self.token = token
            self.port = port

        def health(self):
            return {"status": "ok"}

        def sessions(self, keyword: str = "", *, limit: int = 200, include_groups: bool = False):
            return [WeFlowSession("wxid_partner", "对象", "private", "wechat", 2, 0)]

        def pull(self, session_id: str, *, since: int = 0, offset: int = 0, limit: int = 5000):
            if offset > 0:
                return WeFlowPull(owner_id="me", members={}, messages=[], has_more=False)
            return WeFlowPull(
                owner_id="me", members={},
                messages=[
                    WeFlowMessage("1", "me", "我", dt(2026, 10, 31, 20, 0), 0, "你好", True, False),
                    WeFlowMessage("2", "wxid_partner", "对象", dt(2026, 11, 1, 9, 0), 0, "早安", False, False),
                ],
                has_more=False,
            )

        def pull_with_media(self, session_id: str, *, since: int = 0, offset: int = 0,
                            limit: int = 500):
            return self.pull(session_id, since=since, offset=offset, limit=limit)

        def download_media(self, relative_path: str, *, max_bytes: int = 0):
            return b""

        def close(self):
            return None

    monkeypatch.setattr(love_chat, "WeFlowClient", FakeClient)


def test_wechat_chat_single_contact_calendar_and_export(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_weflow(monkeypatch)
    client, _vault = make_app(tmp_path)
    headers = {"x-api-token": "test-token"}
    saved = client.put(
        "/api/love/chat/settings", headers=headers,
        json={"port": 5031, "token": "token"},
    )
    assert saved.status_code == 200, saved.text
    bound = client.post(
        "/api/love/chat/bind", headers=headers,
        json={"session_id": "wxid_partner", "me_sender": "我"},
    )
    assert bound.status_code == 200, bound.text
    started = client.post(
        "/api/love/chat/sync", headers=headers,
        json={"mode": "apply", "full": True},
    )
    assert started.status_code == 200, started.text
    log_id = started.json()["log_id"]
    log = {}
    for _ in range(50):
        time.sleep(0.1)
        log = client.get(f"/api/love/chat/sync/{log_id}", headers=headers).json()
        if log.get("status") in {"completed", "failed"}:
            break
    assert log["status"] == "completed", log
    assert log["total"] == 2
    assert log["fetched"] == 2
    assert log["progress_percent"] == 100
    calendar = client.get(
        "/api/love/chat/calendar?month=2026-11", headers=headers).json()
    assert calendar["days"][0]["state"] == "synced"
    day = client.get("/api/love/chat/day?date=2026-11-01", headers=headers).json()
    assert day["count"] == 1
    assert day["messages"][0]["content"] == "早安"
    exported = client.get("/api/love/chat/export?date=all&fmt=txt", headers=headers)
    assert exported.status_code == 200
    assert "早安" in exported.text


def test_existing_message_gets_media_on_resync_and_emoji_kind(tmp_path: Path) -> None:
    db = tmp_path / "chat.db"
    ensure_chat_schema(db)
    message = WeFlowMessage(
        "p1", "wxid_partner", "对象", dt(2026, 11, 1, 10, 0), 5, "[表情]", False, False,
        media_path="http://127.0.0.1:5031/api/v1/media/wxid/emojis/a.jpg",
    )
    assert love_chat._media_kind(message) == "emoji"
    with chat_connect(db) as con:
        con.execute(
            "INSERT INTO messages(platform_message_id, sender_id, sender, is_send, "
            "message_type, content, day, ts, content_hash, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("p1", "wxid_partner", "对象", 0, 5, "[表情]", "2026-11-01", 1,
             love_chat.content_hash(message), "x"),
        )
        assert love_chat._insert_message(con, message, media_relative="2026/11/a.jpg") == "matched"
        row = con.execute(
            "SELECT media_path, media_kind FROM messages WHERE platform_message_id='p1'"
        ).fetchone()
        assert row["media_path"] == "2026/11/a.jpg"
        assert row["media_kind"] == "emoji"


def test_sync_log_progress_percent_rate_and_eta() -> None:
    started = (dt.now(love_chat.DAY) - love_chat.timedelta(seconds=10)).isoformat(timespec="seconds")
    payload = love_chat._sync_log_json({
        "id": 1, "mode": "apply", "status": "running", "total": 100,
        "fetched": 50, "inserted": 40, "matched": 0, "duplicates": 10,
        "media_failed": 0, "warnings": "", "error": "",
        "started_at": started, "finished_at": "",
    })
    assert payload["progress_percent"] == 50.0
    assert 4.5 <= payload["rate_per_second"] <= 5.5
    assert 9 <= payload["eta_seconds"] <= 11


def test_schedule_manual_override(tmp_path: Path) -> None:
    client, _vault = make_app(tmp_path)
    headers = {"x-api-token": "test-token"}
    saved = client.put(
        "/api/love/schedule/override", headers=headers,
        json={"date": "2026-11-01", "status": "work", "duty": "G1"},
    )
    assert saved.status_code == 200, saved.text
    month = client.get("/api/love/schedule/month?month=2026-11", headers=headers).json()
    entry = month["days"][0]
    assert entry["override"] is True
    assert entry["work"] is True
    assert entry["duty"] == "G1"
    cleared = client.delete(
        "/api/love/schedule/override?date=2026-11-01", headers=headers)
    assert cleared.status_code == 200
    assert cleared.json()["removed"] is True


def test_gallery_upload_thumbnail_move_delete(tmp_path: Path) -> None:
    paths = love_paths(tmp_path)
    ensure_love_layout(tmp_path)
    app = FastAPI()
    app.include_router(create_gallery_router(paths))
    client = TestClient(app)
    image = Image.new("RGB", (320, 240), (200, 120, 140))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    uploaded = client.post(
        "/api/love/gallery/upload",
        files={"files": ("photo.png", buffer.getvalue(), "image/png")},
        data={"album_id": "1"},
    )
    assert uploaded.status_code == 200, uploaded.text
    media = uploaded.json()["saved"][0]
    assert media["width"] == 320
    assert client.get(media["thumb_url"]).status_code == 200
    assert client.get(media["file_url"]).status_code == 200
    album = client.post("/api/love/gallery/albums", json={"name": "旅行"})
    assert album.status_code == 200, album.text
    album_id = album.json()["id"]
    moved = client.patch(
        f"/api/love/gallery/media/{media['id']}", json={"album_id": album_id})
    assert moved.status_code == 200, moved.text
    assert moved.json()["album_id"] == album_id
    deleted = client.delete(f"/api/love/gallery/media/{media['id']}")
    assert deleted.status_code == 200


class _FakeBaziRole:
    def run_once(self, role_id, mode, prompt, *, entry="", selectors=None):
        return {
            "structured": {
                "him": {"pattern": "测试格局"},
                "her": {"pattern": "测试格局二"},
                "hehun": {
                    "kicker": "合", "summary": "摘要", "verdict": "良配",
                    "score": "80", "points": [], "review": "口径",
                    "years": [], "advice": [], "boundary": "参考",
                },
            },
            "text": "ok",
        }


def test_bazi_chart_generate_history(tmp_path: Path) -> None:
    paths = love_paths(tmp_path)
    app = FastAPI()
    app.include_router(create_bazi_router(paths, role_service=_FakeBaziRole()))
    client = TestClient(app)
    payload = {
        "him": {"name": "男", "birth_date": "1995-08-15", "birth_time": "14:30", "place": "上海"},
        "her": {"name": "女", "birth_date": "1996-03-02", "birth_time": "08:00", "place": "北京"},
    }
    chart = client.post("/api/love/bazi/chart", json=payload)
    assert chart.status_code == 200, chart.text
    assert len(chart.json()["chart"]["him"]["pillars"]) == 4
    generated = client.post("/api/love/bazi/generate", json=payload)
    assert generated.status_code == 200, generated.text
    report = generated.json()
    assert report["result"]["hehun"]["verdict"] == "良配"
    assert client.get("/api/love/bazi/history").json()["items"][0]["id"] == report["id"]
    assert client.get(f"/api/love/bazi/reports/{report['id']}/html").status_code == 200
    assert client.delete(f"/api/love/bazi/reports/{report['id']}").status_code == 200


class _FakeMasterRole:
    def readiness(self):
        return True, ""

    def run_once(self, role_id, mode, prompt, *, entry="", selectors=None):
        return {
            "structured": {
                "summary": "画像摘要",
                "traits": [{"trait": "沟通", "value": "直接", "confidence": 0.8, "evidence": "聊天"}],
                "needs": ["被听见"], "communication_style": "先说感受",
                "care_tips": ["晚安"], "risks": [],
            },
            "text": "ok",
        }


def test_love_master_overview_and_portrait_run(tmp_path: Path) -> None:
    paths = love_paths(tmp_path)
    ensure_love_layout(tmp_path)
    database = Database(tmp_path / "torch_os.sqlite3")
    database.initialize()
    ensure_chat_schema(paths.chat_db)
    ensure_master_schema(paths)
    with chat_connect(paths.chat_db) as con:
        con.execute(
            "INSERT INTO contact (id, session_id, display_name, me_sender, partner_sender,"
            " watermark, history_start_day, last_sync_day, last_sync_at, last_sync_status,"
            " last_error, created_at, updated_at) VALUES"
            " (1, 'x', '对象', '我', '对象', 0, '2026-11-01', '2026-11-01', '', 'ok', '',"
            " '2026-11-01', '2026-11-01')")
        con.execute(
            "INSERT INTO messages (sender, is_send, message_type, content, day, ts,"
            " content_hash, created_at) VALUES"
            " ('对象', 0, 0, '早安', '2026-11-01', 1793494800, 'h1', '2026-11-01')")
        con.commit()
    app = FastAPI()
    app.include_router(create_master_router(
        paths, database, role_service=_FakeMasterRole()))
    client = TestClient(app)
    overview = client.get("/api/love/master/overview")
    assert overview.status_code == 200, overview.text
    assert overview.json()["chat"]["total"] == 1
    started = client.post("/api/love/master/run", json={"skill": "portrait", "days": 30})
    assert started.status_code == 200, started.text
    run_id = started.json()["run_id"]
    run = {}
    for _ in range(60):
        time.sleep(0.1)
        run = client.get(f"/api/love/master/runs/{run_id}").json()
        if run.get("status") in {"completed", "failed"}:
            break
    assert run["status"] == "completed", run
    assert run["result"]["structured"]["summary"] == "画像摘要"
    note = run["result"].get("note") or {}
    assert note.get("notebook") == "关系画像"
    note_path = tmp_path / str(note.get("path") or "")
    assert note_path.is_file()
    note_text = note_path.read_text(encoding="utf-8")
    assert "画像摘要" in note_text and "被听见" in note_text
    memory_root = tmp_path / "02_生活运营" / "01_恋爱系统" / "01_恋爱记忆"
    for name in ("关系画像", "深度分析", "阶段报告"):
        assert (memory_root / name).is_dir()


class _FakeJournalRole:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def readiness(self):
        return True, ""

    def run_once(self, role_id, mode, prompt, *, entry="", selectors=None):
        self.prompts.append(prompt)
        return {
            "structured": {
                "title": "恋爱日记", "body": "今天和小林聊到很晚。", "mood": "love",
            },
            "text": "ok",
        }


def _wait_master_run(client: TestClient, run_id: int) -> dict:
    run: dict = {}
    for _ in range(100):
        time.sleep(0.1)
        run = client.get(f"/api/love/master/runs/{run_id}").json()
        if run.get("status") in {"completed", "failed"}:
            break
    assert run.get("status") == "completed", run
    return run


def test_love_master_journal_batch_keeps_real_names_and_can_backfill_all(
        tmp_path: Path) -> None:
    paths = love_paths(tmp_path)
    ensure_love_layout(tmp_path)
    database = Database(tmp_path / "torch_os.sqlite3")
    database.initialize()
    ensure_chat_schema(paths.chat_db)
    with chat_connect(paths.chat_db) as con:
        con.execute(
            "INSERT INTO contact (id, session_id, display_name, me_sender, partner_sender,"
            " watermark, history_start_day, last_sync_day, last_sync_at, last_sync_status,"
            " last_error, created_at, updated_at) VALUES"
            " (1, 'x', '小林', '阿杰', '小林', 0, '2026-10-30', '2026-11-01', '', 'ok', '',"
            " '2026-10-30', '2026-11-01')")
        for index, (day, is_send, content) in enumerate([
            ("2026-10-30", 0, "小林说：我的电话是13800138000，记得存一下"),
            ("2026-10-31", 1, "明天见"),
            ("2026-11-01", 0, "晚安，小林"),
        ]):
            con.execute(
                "INSERT INTO messages (sender, is_send, message_type, content, day, ts,"
                " content_hash, created_at) VALUES (?, ?, 0, ?, ?, ?, ?, ?)",
                ("阿杰" if is_send else "小林", is_send, content, day,
                 1793462400 + index * 86400, f"h{index}", day))
        con.commit()
    role = _FakeJournalRole()
    app = FastAPI()
    app.include_router(create_master_router(paths, database, role_service=role))
    client = TestClient(app)
    assert client.get("/api/love/master/overview").json()["journal_pending"] == 3

    # 先补最近 1 天：原文不脱敏，对方用真实称呼「小林」。
    started = client.post("/api/love/master/run", json={
        "skill": "journal_batch", "max_days": 1,
    })
    assert started.status_code == 200, started.text
    run = _wait_master_run(client, started.json()["run_id"])
    assert [item["date"] for item in run["result"]["created"]] == ["2026-11-01"]
    prompt = json.loads(role.prompts[-1])
    assert prompt["messages"][0]["who"] == "小林"
    assert "晚安，小林" in prompt["messages"][0]["content"]

    # max_days=0 表示全量：已有草稿的日期跳过，其余日期全部补上。
    started = client.post("/api/love/master/run", json={
        "skill": "journal_batch", "max_days": 0,
    })
    assert started.status_code == 200, started.text
    run = _wait_master_run(client, started.json()["run_id"])
    assert sorted(item["date"] for item in run["result"]["created"]) == [
        "2026-10-30", "2026-10-31",
    ]
    raw_prompt = next(json.loads(prompt) for prompt in role.prompts
                      if '"2026-10-30"' in prompt)
    raw = raw_prompt["messages"][0]
    assert raw["who"] == "小林"
    assert "13800138000" in raw["content"]
    assert "[电话号码已隐藏]" not in raw["content"]
    drafts = list((paths.root / "01_恋爱记忆" / "AI 草稿").glob("*.md"))
    assert len(drafts) == 3
    assert client.get("/api/love/master/overview").json()["journal_pending"] == 0


def test_legacy_love_migration(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    legacy_dir = vault / ".torch-os" / "love"
    (legacy_dir / "xlsx").mkdir(parents=True)
    database = Database(vault / ".torch-os" / "torch_os.sqlite3")
    database.initialize()
    legacy = legacy_dir / "love_analysis.db"
    with sqlite3.connect(legacy) as con:
        con.executescript(SCHEMA_SQL)
        now = "2026-10-31 20:00:00"
        con.execute(
            "INSERT INTO projects (id,name,partner_a,partner_b,message_count,"
            "date_range_start,date_range_end,created_at,updated_at)"
            " VALUES ('p1','旧项目','我','对象',1,'2026-10-31 20:00:00',"
            "'2026-10-31 21:00:00',?,?)", (now, now))
        con.execute(
            "INSERT INTO messages (id,project_id,timestamp,sender,content,is_system,"
            "content_hash,created_at) VALUES ('m1','p1','2026-10-31 21:00:00',"
            "'对象','晚安',0,'h1',?)", (now,))
        con.execute(
            "INSERT INTO journal_entries (id,project_id,date,content,mood,created_at,"
            "updated_at) VALUES ('j1','p1','2026-10-31','今天很甜','happy',?,?)",
            (now, now))
        con.commit()
    report = migrate_love_v2(vault, database, database_path=database.path)
    assert report["status"] == "completed", report
    root = vault / LOVE_ROOT
    assert (root / "02_微信聊天" / "chat.db").is_file()
    with sqlite3.connect(root / "02_微信聊天" / "chat.db") as con:
        assert con.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
        assert con.execute("SELECT display_name FROM contact").fetchone()[0] == "对象"
    notes = list((root / "01_恋爱记忆" / "恋爱日志").glob("*.md"))
    assert notes and "今天很甜" in notes[0].read_text(encoding="utf-8")
    assert (root / "_归档").is_dir()
    again = migrate_love_v2(vault, database, database_path=database.path)
    assert again.get("reused") is True
    with sqlite3.connect(root / "02_微信聊天" / "chat.db") as con:
        assert con.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
    assert len(list((root / "01_恋爱记忆" / "恋爱日志").glob("*.md"))) == 1
