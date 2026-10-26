"""原生恋爱系统：数据库复用、导入、统计、画像、日志、AI 编排与丽洁追踪。"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import love as love_module
from backend.app import love_lijie
from backend.app.love import ChatParser, LoveStore, create_love_router

SAMPLE = """2026-05-20 10:00:00 我: 你好，我的电话是13800138000
2026-05-20 10:01:00 胡丽洁: 嗯嗯，今天做什么
2026-05-20 10:02:00 我: 明天见面吧
2026-05-21 09:00:00 胡丽洁: 好呀
2026-05-21 09:01:00 我: 那说定了
"""

AI_RESULT = {
    "relationship_stage": "稳定期",
    "emotional_tone": {"overall": "温和", "fluctuation": 0.2, "trend": "稳定"},
    "communication_patterns": {
        "initiative_balance": "双方接近",
        "response_style": "及时回应",
        "key_topics": ["见面", "日常"],
    },
    "key_events": [],
    "risk_warnings": [],
    "personalized_advice": ["保持直接沟通"],
    "partner_traits": [{
        "trait": "沟通风格", "value": "直接、回应快",
        "confidence": 0.9, "evidence": "每次都能很快接话",
    }],
}


def build_client(tmp_path: Path) -> tuple[TestClient, LoveStore]:
    store = LoveStore(tmp_path / "love.db", key_file=tmp_path / ".api_key.key")
    store.ensure_schema()
    app = FastAPI()
    app.include_router(create_love_router(store))
    return TestClient(app), store


def import_sample(client: TestClient, name: str = "测试项目") -> str:
    response = client.post("/api/love/import/text", json={
        "content": SAMPLE, "file_name": "sample.txt", "project_name": name,
    })
    assert response.status_code == 200, response.text
    return response.json()["project_id"]


def test_parser_handles_gbk_and_multiline() -> None:
    parser = ChatParser()
    raw = (
        "2026-05-20 22:19:08 '我'\n"
        "你已添加了🦋，以上是打招呼的消息。\n"
        "2026-05-20 22:20:00 '胡丽洁'\n"
        "嗨\n"
    ).encode("gb18030")
    parsed = parser.decode_and_parse(raw)
    assert len(parsed.messages) == 2
    assert parsed.senders == ["我", "胡丽洁"]
    assert parsed.messages[0].is_system is False or parsed.messages[0].sender == "我"
    # 单行与多行混排也要能解析。
    mixed = parser.parse(
        "2026-05-21 10:00:00 我: 早上好\n"
        "2026-05-21 10:01:00 '胡丽洁'\n"
        "早呀\n"
    )
    assert [message.content for message in mixed.messages] == ["早上好", "早呀"]


def test_import_stats_journal_profile_and_report(tmp_path: Path) -> None:
    client, store = build_client(tmp_path)
    assert client.get("/api/love/status").json()["available"] is True

    # 前端使用 multipart 上传；先预览再导入。
    preview = client.post(
        "/api/love/import/preview",
        files={"file": ("sample.txt", SAMPLE.encode("utf-8"), "text/plain")},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["message_count"] == 5
    assert preview.json()["senders"] == ["胡丽洁", "我"] or set(preview.json()["senders"]) == {"胡丽洁", "我"}
    uploaded = client.post(
        "/api/love/import",
        files={"file": ("sample.txt", SAMPLE.encode("gb18030"), "text/plain")},
        data={"project_name": "上传项目"},
    )
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["message_count"] == 5
    assert uploaded.json()["project_name"].startswith("上传项目")
    project_id = uploaded.json()["project_id"]

    projects = client.get("/api/love/projects").json()["projects"]
    assert [project["id"] for project in projects] == [project_id]
    assert projects[0]["message_count"] == 5

    overview = client.get(f"/api/love/projects/{project_id}/stats/overview").json()
    assert overview["total_messages"] == 5
    assert overview["sender_a"]["messages"] == 3
    assert overview["sender_b"]["messages"] == 2
    assert overview["sender_a"]["ratio"] + overview["sender_b"]["ratio"] == pytest.approx(1)

    trends = client.get(
        f"/api/love/projects/{project_id}/stats/trends?granularity=day"
    ).json()
    assert [row["date"] for row in trends["data"]] == ["2026-05-20", "2026-05-21"]

    timing = client.get(f"/api/love/projects/{project_id}/stats/timing").json()
    assert timing["median_reply_interval_seconds"] > 0
    assert sum(timing["reply_distribution"].values()) > 0

    media = client.get(f"/api/love/projects/{project_id}/stats/media").json()
    assert media["text_count"] == 5
    keywords = client.get(f"/api/love/projects/{project_id}/stats/keywords?top_n=20").json()
    assert isinstance(keywords["words"], list)

    # 日志：新增、修改、导出。
    created = client.post(f"/api/love/projects/{project_id}/journal", json={
        "date": "2026-05-20", "content": "今天聊得很开心", "mood": "happy",
    })
    assert created.status_code == 200, created.text
    entry_id = created.json()["id"]
    updated = client.put(
        f"/api/love/projects/{project_id}/journal/{entry_id}",
        json={"content": "今天聊得很开心，还约了见面"},
    )
    assert "约了见面" in updated.json()["content"]
    export = client.get(f"/api/love/projects/{project_id}/journal/export")
    assert export.status_code == 200
    assert export.content.startswith(b"\xef\xbb\xbf")
    assert "filename*=UTF-8''" in export.headers["content-disposition"]

    # 画像：手动新增、锁定、修改、删除。
    profile = client.post(f"/api/love/projects/{project_id}/profile/traits", json={
        "trait": "雷区", "value": "迟到", "evidence": "用户观察",
    })
    assert profile.status_code == 200, profile.text
    trait = next(item for item in profile.json()["traits"] if item["trait"] == "雷区")
    assert trait["locked"] is False
    locked = client.put(
        f"/api/love/projects/{project_id}/profile/traits/{'雷区'}/lock",
        json={"locked": True},
    )
    assert locked.json()["traits"][0]["locked"] is True
    changed = client.put(
        f"/api/love/projects/{project_id}/profile/traits/{'雷区'}",
        json={"value": "迟到与失约"},
    )
    assert changed.json()["traits"][0]["value"] == "迟到与失约"
    removed = client.delete(f"/api/love/projects/{project_id}/profile/traits/{'雷区'}")
    assert all(item["trait"] != "雷区" for item in removed.json()["traits"])

    # 报告：HTML 预览走单独文档；项目名必须转义。
    client.put(f"/api/love/projects/{project_id}", json={"name": "<b>测试</b>"})
    report = client.post(f"/api/love/projects/{project_id}/report")
    assert report.status_code == 200, report.text
    html = report.json()["html"]
    assert "&lt;b&gt;测试&lt;/b&gt;" in html
    assert "<b>测试</b>" not in html
    preview = client.get(f"/api/love/projects/{project_id}/report/preview")
    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("text/html")

    # 删除项目必须显式确认，并级联删除消息和日志。
    assert client.request(
        "DELETE", f"/api/love/projects/{project_id}", json={"confirm": False}
    ).status_code == 422
    deleted = client.request(
        "DELETE", f"/api/love/projects/{project_id}", json={"confirm": True}
    )
    assert deleted.status_code == 200
    with store.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0] == 0


def test_api_key_is_encrypted_and_analysis_runs_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, store = build_client(tmp_path)
    project_id = import_sample(client)

    # 没有 Key 时不允许启动分析，避免先把日志写成 running 再失败。
    assert client.post(f"/api/love/projects/{project_id}/analysis").status_code == 400

    plaintext = "sk-test-love-key-123456"
    saved = client.post("/api/love/settings/api-key", json={"key": plaintext})
    assert saved.status_code == 200, saved.text
    assert store.key_file.is_file() and len(store.key_file.read_bytes()) == 32
    with store.connect() as con:
        stored = con.execute("SELECT value FROM settings WHERE key='api_key'").fetchone()[0]
    assert plaintext not in stored
    assert client.get("/api/love/settings").json()["api_key_configured"] is True

    seen: list[list[dict[str, str]]] = []

    def fake_deepseek(settings, api_key: str, messages: list[dict[str, str]], **kwargs):
        assert api_key == plaintext
        seen.append(messages)
        return json.dumps(AI_RESULT, ensure_ascii=False), {
            "prompt_tokens": 12, "completion_tokens": 34,
        }

    monkeypatch.setattr(love_module, "_call_deepseek", fake_deepseek)
    started = client.post(f"/api/love/projects/{project_id}/analysis", json={"analysis_type": "deep"})
    assert started.status_code == 200, started.text
    log_id = started.json()["log_id"]

    status = client.get(f"/api/love/projects/{project_id}/analysis/status/{log_id}").json()
    assert status["status"] == "completed"
    assert status["result"]["relationship_stage"] == "稳定期"
    assert status["prompt_tokens"] == 12

    prompt = seen[0][1]["content"]
    assert "13800138000" not in prompt
    assert "胡丽洁" not in prompt
    assert "用户B" in prompt
    profile = client.get(f"/api/love/projects/{project_id}/profile").json()
    assert any(item["trait"] == "沟通风格" for item in profile["traits"])

    deleted = client.delete("/api/love/settings/api-key")
    assert deleted.status_code == 200
    assert client.get("/api/love/settings").json()["api_key_configured"] is False


def _excel_serial(day: date) -> int:
    return (day - date(1899, 12, 30)).days


def _make_lijie_fixture(tmp_path: Path) -> tuple[Path, Path]:
    import openpyxl

    excel_dir = tmp_path / "xlsx"
    excel_dir.mkdir()
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "高1-14组"
    sheet.cell(row=3, column=5, value="2026年5月份交路表")
    days = [date(2026, 5, 20), date(2026, 5, 21), date(2026, 5, 22),
            date(2026, 6, 20), date(2026, 6, 21), date(2026, 6, 22)]
    trains = ["G1", "休", "G2", "G3", "G4", "G5"]
    for index, (day, train) in enumerate(zip(days, trains)):
        column = 10 + index
        sheet.cell(row=7, column=column, value=day.day)
        sheet.cell(row=8, column=column, value=_excel_serial(day))
        sheet.cell(row=10, column=column, value=train)
    sheet.cell(row=9, column=3, value="高1组")
    sheet.cell(row=10, column=5, value="胡丽洁")
    excel_path = excel_dir / "5月份交路.xlsx"
    workbook.save(excel_path)

    timetable_path = tmp_path / "train_timetable.json"
    timetable = {}
    for code in ("G1", "G2", "G3", "G4", "G5"):
        timetable[code] = {
            "train_code": code, "from_station": "南昌西", "to_station": "北京西",
            "depart_time": "10:00", "arrive_time": "11:00", "duration_minutes": 60,
            "stops": [
                {"station": "南昌西", "arrive": None, "depart": "10:00"},
                {"station": "武汉", "arrive": "10:30", "depart": "10:32"},
                {"station": "北京西", "arrive": "11:00", "depart": None},
            ],
        }
    timetable_path.write_text(json.dumps(timetable, ensure_ascii=False), encoding="utf-8")
    return excel_dir, timetable_path


def test_lijie_schedule_fallback_and_realtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    excel_dir, timetable_path = _make_lijie_fixture(tmp_path)
    monkeypatch.setattr(love_lijie, "_weather", lambda cities: {})

    app = FastAPI()
    app.include_router(love_lijie.create_lijie_router(excel_dir, timetable_path))
    client = TestClient(app)

    exact = client.get("/api/love/lijie/day?date=2026-05-20").json()
    assert exact["found"] is True
    assert exact["is_rest"] is False
    assert [train["train_code"] for train in exact["trains"]] == ["G1"]
    assert exact["group"] == "高1组"

    rest = client.get("/api/love/lijie/day?date=2026-05-21").json()
    assert rest["found"] is True and rest["is_rest"] is True

    fallback = client.get("/api/love/lijie/day?date=2026-07-20").json()
    assert fallback["found"] is True
    assert "06月" in fallback["fallback_note"]
    assert [train["train_code"] for train in fallback["trains"]] == ["G3"]

    # 运行中：10:33 已离开武汉，正在前往北京西。
    monkeypatch.setattr(love_lijie, "_now_minutes", lambda: 10 * 60 + 33)
    realtime = love_lijie._realtime(exact["trains"])
    assert realtime["status"] == "running"
    assert realtime["current_station"] == "武汉"
    assert realtime["next_station"] == "北京西"

    # 有车次但时刻表缺这趟车：不能误报“今日休息”。
    missing = love_lijie._realtime([{
        "train_code": "G999", "from_station": "未知", "to_station": "未知",
        "depart_time": "未知", "arrive_time": "未知",
        "duration_minutes": 0, "stops": [],
    }])
    assert missing["status"] == "unknown"
    assert "时刻表" in missing["message"]

    # 时刻表导入接口：只接受结构正确的 UTF-8 JSON。
    response = client.post(
        "/api/love/lijie/timetable",
        files={"file": ("timetable.json", timetable_path.read_bytes(), "application/json")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["trains"] == 5
