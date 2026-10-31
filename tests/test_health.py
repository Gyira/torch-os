"""健康系统：Markdown 权威源、评分、趋势、洞察、检验与私人医生报告。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import health, health_score, health_store
from backend.app.db import Database

DAY = "2026-10-31"


def build(tmp_path: Path, *, role_service=None, read_only: bool = False):
    vault = tmp_path / "vault"
    vault.mkdir()
    database = Database(tmp_path / "torch.sqlite3")
    database.initialize()
    app = FastAPI()
    app.include_router(health.create_health_router(
        database, vault, role_service=role_service, read_only=read_only))
    return TestClient(app), vault, database


def seed_profile(vault: Path) -> dict:
    return health_store.write_profile(vault, {
        "gender": "男", "birth_date": "1993-05-01", "height_cm": 178,
        "goals": {"weight_kg": 72, "sleep_minutes": 450, "steps": 8000,
                  "water_ml": 2000, "weekly_training": 4},
        "medical_history": "无", "allergies": "青霉素",
    })


def test_day_is_stored_in_health_system_markdown(tmp_path):
    _client, vault, _database = build(tmp_path)
    data = health_store.patch_day(vault, DAY, {
        "sleep": {"bed": "23:40", "wake": "07:10", "deep_min": 80, "quality": 4},
        "body": {"weight_kg": 73.4},
        "mind": {"mood": 4, "stress": 2},
        "note": "今天状态不错。",
    })
    path = vault / "02_生活运营" / "02_健康系统" / "01_每日健康" / f"{DAY}.md"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "schema: health-day-v1" in text
    assert "weight_kg: 73.4" in text
    assert data["sections"]["sleep"]["duration_min"] == 450
    assert data["sections"]["body"]["weight_kg"] == 73.4
    assert "健康指数" in text
    assert "今天状态不错。" in text


def test_section_validation_rejects_unknown_and_out_of_range(tmp_path):
    _client, vault, _database = build(tmp_path)
    with pytest.raises(Exception) as excinfo:
        health_store.patch_day(vault, DAY, {"body": {"weight_kg": 900}})
    assert "不能大于" in str(excinfo.value)
    with pytest.raises(Exception) as excinfo:
        health_store.patch_day(vault, DAY, {"sleep": {"nope": 1}})
    assert "没有指标" in str(excinfo.value)
    with pytest.raises(Exception):
        health_store.patch_day(vault, "2026-02-30", {"mind": {"mood": 3}})


def test_revision_conflict_never_overwrites(tmp_path):
    _client, vault, _database = build(tmp_path)
    first = health_store.patch_day(vault, DAY, {"mind": {"mood": 3}})
    second = health_store.patch_day(vault, DAY, {"mind": {"mood": 4}},
                                    expected_revision=first["revision"])
    assert second["sections"]["mind"]["mood"] == 4
    with pytest.raises(Exception) as excinfo:
        health_store.patch_day(vault, DAY, {"mind": {"mood": 1}},
                               expected_revision=first["revision"])
    assert "别处修改" in str(excinfo.value)
    assert health_store.read_day(vault, DAY)["sections"]["mind"]["mood"] == 4


def test_meals_are_summed_and_water_is_kept(tmp_path):
    _client, vault, _database = build(tmp_path)
    health_store.patch_day(vault, DAY, {"nutrition": {"water_ml": 1500, "caffeine_mg": 80}})
    health_store.add_meal(vault, DAY, {"meal_type": "早餐", "name": "燕麦鸡蛋",
                                       "calories": 420, "protein_g": 24, "carbs_g": 50})
    data = health_store.add_meal(vault, DAY, {"meal_type": "午餐", "name": "鸡胸米饭",
                                              "calories": 650, "protein_g": 45, "carbs_g": 70})
    totals = health_store.nutrition_totals(data)
    assert totals["calories"] == 1070
    assert totals["protein_g"] == 69
    assert totals["water_ml"] == 1500
    assert len(data["meals"]) == 2
    with pytest.raises(Exception):
        health_store.delete_meal(vault, DAY, "missing")


def test_training_volume_and_rpe(tmp_path):
    _client, vault, _database = build(tmp_path)
    data = health_store.add_training(vault, DAY, {
        "kind": "力量", "minutes": 45, "rpe": 7, "soreness": 2, "mood_after": 4,
        "exercises": [
            {"name": "杠铃卧推", "exercise_id": "0025",
             "sets": [{"reps": 8, "weight_kg": 60}, {"reps": 8, "weight_kg": 60}]},
            {"name": "引体向上", "sets": [{"reps": 6}]},
        ],
    })
    session = data["training"][0]
    assert session["volume_kg"] == 960
    assert health_store.training_totals(data)["minutes"] == 45
    with pytest.raises(Exception):
        health_store.add_training(vault, DAY, {"kind": "力量"})


def test_profile_write_and_read(tmp_path):
    _client, vault, _database = build(tmp_path)
    profile = seed_profile(vault)
    assert profile["exists"] is True
    assert profile["height_cm"] == 178
    assert profile["goals"]["weight_kg"] == 72
    assert (vault / "02_生活运营" / "02_健康系统" / "02_身体档案"
            / "身体档案（profile）.md").is_file()
    target = health_score.targets(profile)
    assert target["sleep_minutes"] == 450
    assert target["protein_g"] and target["calories"]


def test_sleep_score_and_health_score_exclude_missing_dimensions(tmp_path):
    _client, vault, _database = build(tmp_path)
    profile = seed_profile(vault)
    sleep = health_score.sleep_score({"duration_min": 480, "deep_min": 110,
                                      "rem_min": 100, "latency_min": 10, "quality": 4})
    assert 85 <= sleep["score"] <= 100
    day = health_store.patch_day(vault, DAY, {"sleep": {"duration_min": 480}})
    score = health_score.score_day(day, profile, [day])
    assert score["total"] is not None
    assert score["breakdown"]["sleep"] is not None
    assert score["breakdown"]["nutrition"] is None
    assert "营养" in score["insufficient"]
    assert score["derived"].get("bmi") is None  # 没有体重就不发明 BMI


def test_bmi_and_blood_pressure_levels():
    assert health_score.bmi(73.4, 178) == 23.2
    assert health_score.bmi_band(23.2) == "正常"
    assert health_score.blood_pressure_level(118, 76) == "正常"
    assert health_score.blood_pressure_level(146, 94) == "1 级高血压"
    assert health_score.blood_pressure_level(185, 112) == "3 级高血压"
    assert health_score.blood_pressure_level(85, 55) == "偏低"


def test_labs_flag_abnormal_and_sort(tmp_path):
    _client, vault, _database = build(tmp_path)
    health_store.save_labs(vault, [
        {"date": DAY, "category": "血脂", "test": "低密度脂蛋白", "value": 4.1,
         "unit": "mmol/L", "ref_min": 0, "ref_max": 3.4},
        {"date": "2026-10-01", "category": "血脂", "test": "高密度脂蛋白", "value": 1.3,
         "unit": "mmol/L", "ref_min": 1.0, "ref_max": 2.0},
    ])
    saved = health_store.read_labs(vault)
    assert saved["exists"] is True
    assert [row["date"] for row in saved["rows"]] == ["2026-10-01", DAY]
    abnormal = [row for row in saved["rows"] if row["abnormal"]]
    assert len(abnormal) == 1 and abnormal[0]["test"] == "低密度脂蛋白"
    assert (vault / "02_生活运营" / "02_健康系统" / "03_体检与检验"
            / "检验记录（labs）.md").is_file()


def test_summary_reads_health_system_not_monthly_table(tmp_path):
    _client, vault, _database = build(tmp_path)
    seed_profile(vault)
    health_store.patch_day(vault, "2026-10-30", {"body": {"weight_kg": 74.0},
                                                 "activity": {"steps": 9000}})
    health_store.patch_day(vault, DAY, {"body": {"weight_kg": 73.4},
                                        "sleep": {"duration_min": 450},
                                        "mind": {"mood": 4}})
    text, stats = health_store.summary(vault, days=30, end=DAY)
    assert stats["days"] == 2
    assert "73.4" in text and "平均" in text
    assert "身高" in text


def test_overview_api_shape(tmp_path):
    client, vault, _database = build(tmp_path)
    seed_profile(vault)
    health_store.patch_day(vault, DAY, {"body": {"weight_kg": 73.4},
                                        "activity": {"steps": 8200},
                                        "sleep": {"duration_min": 460}})
    response = client.get("/api/health/overview", params={"day": DAY})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["day"] == DAY
    assert payload["today"]["sections"]["body"]["weight_kg"] == 73.4
    assert payload["today"]["score"]["total"] is not None
    assert len(payload["week"]) == 7
    assert payload["week"][-1]["steps"] == 8200
    assert payload["targets"]["steps"] == 8000
    assert any(item["label"] == "饮水" for item in payload["missing"])
    assert payload["catalog"]["groups"][0]["fields"]


def test_day_patch_api_and_meal_endpoints(tmp_path):
    client, vault, _database = build(tmp_path)
    response = client.patch(f"/api/health/day/{DAY}", json={
        "changes": {"vitals": {"systolic": 128, "diastolic": 84, "resting_hr": 58},
                    "mind": {"mood": 5, "stress": 1}}})
    assert response.status_code == 200, response.text
    record = response.json()["record"]
    assert record["sections"]["vitals"]["resting_hr"] == 58
    assert record["abnormal"], "血压 128/84 应提示正常高值"
    meal = client.post(f"/api/health/day/{DAY}/meals", json={
        "meal_type": "早餐", "name": "豆浆油条", "calories": 500, "protein_g": 15})
    assert meal.status_code == 200, meal.text
    assert meal.json()["record"]["nutrition"]["calories"] == 500
    meal_id = meal.json()["record"]["meals"][0]["id"]
    removed = client.delete(f"/api/health/day/{DAY}/meals/{meal_id}")
    assert removed.status_code == 200
    assert removed.json()["record"]["meals"] == []


def test_trends_and_custom_metrics(tmp_path):
    client, vault, _database = build(tmp_path)
    health_store.patch_day(vault, DAY, {"body": {"weight_kg": 73.4},
                                        "custom": {"腰臀比": 0.86}})
    profile = health_store.read_profile(vault)
    health_store.write_profile(vault, {
        **{key: profile[key] for key in ("height_cm", "gender", "birth_date")},
        "custom_metrics": [{"label": "腰臀比", "unit": "", "min": 0, "max": 2}],
    })
    response = client.get("/api/health/trends", params={
        "metrics": f"body.weight_kg,custom.腰臀比,score.total", "days": 30, "end": DAY})
    assert response.status_code == 200, response.text
    payload = response.json()
    keys = [item["key"] for item in payload["metrics"]]
    assert keys == ["body.weight_kg", "custom.腰臀比", "score.total"]
    weight = payload["metrics"][0]
    assert weight["points"] == [{"date": DAY, "value": 73.4}]
    assert weight["stats"]["latest"] == 73.4
    assert any(item["key"] == "custom.腰臀比" for item in payload["available"])


def test_insights_wait_for_enough_samples(tmp_path):
    _client, vault, _database = build(tmp_path)
    seed_profile(vault)
    for offset in range(3):
        health_store.patch_day(vault, health_store.shift_day(DAY, -offset),
                               {"sleep": {"duration_min": 400}, "mind": {"mood": 3}})
    days = health_store.list_days(vault, start=health_store.shift_day(DAY, -30), end=DAY)
    assert health_score.insights(days, health_store.read_profile(vault)) == []


def test_insight_mark_read_is_stored_outside_vault(tmp_path):
    client, vault, database = build(tmp_path)
    response = client.post("/api/health/insights/read", json={"id": "sleep_training:a:b"})
    assert response.status_code == 200
    assert response.json()["read"] == ["sleep_training:a:b"]
    state = health_store.read_insight_state(database)
    assert state["read"] == ["sleep_training:a:b"]
    assert (Path(database.path).parent / "health" / "insights.json").is_file()
    assert not (vault / "health").exists()


def test_exercises_are_local_and_searchable(tmp_path):
    client, _vault, _database = build(tmp_path)
    response = client.get("/api/health/exercises", params={"q": "卧推"})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["items"], "动作库应命中卧推"
    assert payload["items"][0]["name"]
    assert payload["facets"]["total"] > 1000


def test_private_doctor_report_writes_health_report_chapter(tmp_path):
    class FakeRole:
        def run(self, role_id, mode, *, context_text="", user_text=""):
            assert role_id == "health_doctor" and mode == "report"
            assert "健康" in context_text or context_text
            return SimpleNamespace(text=json.dumps({
                "title": "健康周报-测试",
                "summary": "睡眠稳定。",
                "sections": [{"heading": "事实", "body": "记录了 1 天。"}],
                "recommendations": ["保持 7 小时睡眠"],
                "note": {"title": "健康周报-测试", "chapter": "health_report",
                         "markdown": "## 事实\n\n记录了 1 天。\n"},
            }, ensure_ascii=False), seconds=0.2)

    client, vault, _database = build(tmp_path, role_service=FakeRole())
    seed_profile(vault)
    health_store.patch_day(vault, DAY, {"sleep": {"duration_min": 450}})
    response = client.post("/api/health/report", json={"kind": "week", "confirm": True})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["note"]["path"].startswith("02_生活运营/02_健康系统/00_健康报告/")
    assert (vault / payload["note"]["path"]).is_file()
    assert payload["stats"]["days"] >= 1
    reports = client.get("/api/health/reports").json()["reports"]
    assert any(item["title"] == "健康周报-测试" for item in reports)


def test_read_only_mode_rejects_writes_with_409(tmp_path):
    client, _vault, _database = build(tmp_path, read_only=True)
    response = client.patch(f"/api/health/day/{DAY}", json={"changes": {"mind": {"mood": 3}}})
    assert response.status_code == 409
    assert "只读" in response.json()["detail"]
