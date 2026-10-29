"""主动提问：一天一个「关于我」的问题，答了就写进自我档案。

诉求（2026-10-27）：导师要能主动问，而不是等我起话头；但问什么不能随机——
题库固定在代码里，选哪道由库里的信号（身份缺哪项、哪章还空、最近的项目）决定。
跳过冷却 7 天，「别再问这个」永久生效，答完当天不再打扰。
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from backend.app import questions
from backend.app.application import create_app
from backend.app.db import Database

SHANGHAI = ZoneInfo("Asia/Shanghai")
CLI = Path(__file__).resolve().parents[1] / "scripts" / "question_draft.py"


def make_client(tmp_path: Path) -> tuple[TestClient, Path]:
    vault = tmp_path / "vault"
    vault.mkdir(parents=True)
    app = create_app(Database(tmp_path / "q.sqlite3"), vault,
                     security_environ={}, env_file=tmp_path / ".env")
    client = TestClient(app)
    client.get("/")
    return client, vault


def write_identity(vault: Path, **fields: str) -> None:
    path = vault / "01_自我档案" / "01_内核与三观" / "身份（identity）.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", "type: identity"] + [f"{key}: {value}" for key, value in fields.items()]
    path.write_text("\n".join(lines) + "\n---\n\n", encoding="utf-8")


def test_the_first_question_asks_for_a_name(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    question = questions.next_question(vault)
    assert question is not None
    assert question["id"] == "identity.name"
    assert question["kind"] == "field"


def seed_queue(vault: Path, *, prompt: str = "如果没考上，你打算怎么办？", field: str = "worldview",
               chapter: str = "", why: str = "考研规划文档只写目标、没写退路",
               created: datetime | None = None, priority: int = 120) -> Path:
    directory = vault / questions.QUEUE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"q{len(list(directory.glob('*.md')))}.md"
    stamp = (created or datetime.now(SHANGHAI)).isoformat(timespec="seconds")
    lines = ["---", f"prompt: {prompt}"]
    if field:
        lines.append(f"field: {field}")
    if chapter:
        lines.append(f"chapter: {chapter}")
    lines += [f"why: {why}", f"priority: {priority}", f"created: {stamp}", "source: dsh", "---", ""]
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return path


def test_a_queued_question_beats_the_static_bank(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    seed_queue(vault, chapter="meaning", field="",
               prompt="三年后你想停在哪一步？", why="意义与目标还是空的")

    state = questions.question_state(vault)

    assert state["question"]["id"].startswith("queue:")
    assert state["question"]["queued"] is True
    assert state["question"]["prompt"] == "三年后你想停在哪一步？"
    assert "意义与目标" in state["question"]["why"]
    assert state["question"]["target"] == "meaning"


def test_answering_a_queued_question_consumes_the_card(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    seed_queue(vault)

    result = questions.answer_question(vault, "queue:q0", "世界大体是可被理解的，但运气占三成。")

    assert result["target"] == "memory"
    note = vault / result["path"]
    assert "世界大体是可被理解的" in note.read_text(encoding="utf-8")
    assert not (vault / questions.QUEUE_DIR / "q0.md").exists()
    assert (vault / questions.QUEUE_DIR / "已用" / "q0.md").is_file()


def test_skipping_a_queued_question_also_consumes_it(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    seed_queue(vault, prompt="第一个", field="values")
    seed_queue(vault, prompt="第二个", field="lifeview")

    questions.skip_question(vault, "queue:q0")

    assert (vault / questions.QUEUE_DIR / "已用" / "q0.md").is_file()
    assert questions.question_state(vault)["question"]["prompt"] == "第二个"


def test_stale_or_broken_queue_items_fall_back_to_the_bank(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    seed_queue(vault, created=datetime.now(SHANGHAI) - timedelta(days=20))
    (vault / questions.QUEUE_DIR / "broken.md").write_text("---\nprompt: 没有目标的卡\n---\n",
                                                           encoding="utf-8")

    state = questions.question_state(vault)

    assert state["question"]["id"] == "identity.name", "过期和缺字段的卡都不该被问到"
    assert state["question"]["queued"] is False


def test_the_cli_writes_a_card_with_its_reason(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    result = subprocess.run(
        [sys.executable, str(CLI), "add", "--vault", str(vault),
         "--target", "worldview", "--why", "考研文档只写目标没有退路",
         "--prompt", "如果没考上，你打算怎么办？"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert result.returncode == 0, result.stderr
    items = questions.load_queue(vault)
    assert len(items) == 1
    assert items[0]["field"] == "worldview"
    assert "没有退路" in items[0]["why"]


def test_the_cli_refuses_a_question_without_a_reason(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    missing = subprocess.run(
        [sys.executable, str(CLI), "add", "--vault", str(vault),
         "--target", "worldview", "--prompt", "随便问问？"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert missing.returncode == 2
    assert "--why" in missing.stderr

    bad_target = subprocess.run(
        [sys.executable, str(CLI), "add", "--vault", str(vault), "--target", "journal",
         "--why", "因为我想", "--prompt", "今天怎么样？"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert bad_target.returncode == 2


def test_answering_a_field_question_writes_a_memory_note_not_identity(tmp_path: Path) -> None:
    client, vault = make_client(tmp_path)
    result = client.post("/api/questions/answer", json={"id": "identity.name", "text": "小林"})
    assert result.status_code == 200, result.text
    payload = result.json()
    assert payload["target"] == "memory"
    note = vault / payload["path"]
    assert "小林" in note.read_text(encoding="utf-8")
    assert not (vault / "01_自我档案/01_内核与三观/身份（identity）.md").exists()
    assert questions.load_state(vault)["answered"]["identity.name"]


def test_one_answer_per_day_then_the_card_goes_away(tmp_path: Path) -> None:
    client, vault = make_client(tmp_path)
    assert client.get("/api/questions/next").json()["question"]["id"] == "identity.name"
    client.post("/api/questions/answer", json={"id": "identity.name", "text": "小林"})

    payload = client.get("/api/questions/next").json()
    assert payload["question"] is None
    assert payload["answered_today"] is True
    assert payload["exhausted"] is False


def test_i_can_ask_for_another_question_on_demand(tmp_path: Path) -> None:
    """「再问一个」绕过一天一个的上限，但不重复问我已经答过/拉黑过的。"""
    client, _vault = make_client(tmp_path)
    client.post("/api/questions/answer", json={"id": "identity.name", "text": "小林"})

    forced = client.get("/api/questions/next?force=1").json()
    assert forced["question"] is not None
    assert forced["question"]["id"] != "identity.name"
    assert forced["answered_today"] is True


def test_everything_declined_is_reported_as_exhausted(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    for item in questions.BANK:
        questions.skip_question(vault, item["id"], forever=True)

    state = questions.question_state(vault)
    assert state["question"] is None
    assert state["exhausted"] is True
    assert questions.question_state(vault, force=True)["question"] is None, "拉黑了就不再问"


def test_memory_questions_land_in_their_chapter(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    result = questions.answer_question(vault, "memory.seed", "2019 年独自去外地读书，第一次自己扛完所有事。")
    assert result["target"] == "memory"
    note = vault / result["path"]
    body = note.read_text(encoding="utf-8")
    assert "2019 年独自去外地读书" in body
    assert "## 我的回答" in body
    assert "## 当时在问什么" in body
    assert result["path"].startswith("01_自我档案/02_记忆与自传/")
    assert body.split("---", 2)[2].lstrip().startswith("> "), "第一行是摘要，档案时间线要用"


def test_a_field_filled_elsewhere_is_never_overwritten(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    write_identity(vault, name="原来的名字")
    result = questions.answer_question(vault, "identity.name", "小林")
    assert result["target"] == "memory", "字段已有内容时改成留一条记忆，不覆盖"
    identity = (vault / "01_自我档案/01_内核与三观/身份（identity）.md").read_text(encoding="utf-8")
    assert "原来的名字" in identity


def test_skipping_cools_down_but_does_not_ban(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    questions.skip_question(vault, "identity.name")
    assert questions.next_question(vault)["id"] == "identity.worldview"

    # 把冷却时间往前拨 8 天，它应该重新回到候选里。
    state = questions.load_state(vault)
    state["skipped"]["identity.name"] = (datetime.now(SHANGHAI) - timedelta(days=8)).isoformat()
    questions.save_state(vault, state)
    assert questions.next_question(vault)["id"] == "identity.name"


def test_declining_is_permanent(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    questions.skip_question(vault, "identity.name", forever=True)
    state = questions.load_state(vault)
    assert "identity.name" in state["declined"]
    assert questions.next_question(vault)["id"] != "identity.name"


def test_project_followup_only_appears_when_a_project_exists(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    signals = questions._signals(vault)
    followup = next(item for item in questions.BANK if item["id"] == "project.followup")
    assert followup["when"](signals) is False

    project = vault / "00_人生路/05_项目/01_项目计划/考研规划.md"
    project.parent.mkdir(parents=True)
    project.write_text("---\ntype: plan\ntitle: 考研规划\n---\n\n正文\n", encoding="utf-8")
    assert followup["when"](questions._signals(vault)) is True


def test_state_file_stays_inside_torch_os(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    questions.skip_question(vault, "identity.name")
    state_path = vault / ".torch-os" / "questions.json"
    assert state_path.is_file()
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["version"] == questions.STATE_VERSION


def test_bad_input_is_rejected(tmp_path: Path) -> None:
    client, _vault = make_client(tmp_path)
    assert client.post("/api/questions/answer", json={"id": "nope", "text": "x"}).status_code == 404
    assert client.post("/api/questions/answer", json={"id": "identity.name", "text": "   "}).status_code == 422


def test_every_bank_entry_is_complete() -> None:
    for item in questions.BANK:
        assert item["id"] and item["prompt"] and item["hint"]
        assert item["kind"] in {"field", "memory"}
        assert (item["kind"] == "field") == bool(item.get("field"))
        assert (item["kind"] == "memory") == bool(item.get("chapter"))
        assert callable(item["when"])
    ids = [item["id"] for item in questions.BANK]
    assert len(ids) == len(set(ids)), "问题 id 不能重复"
