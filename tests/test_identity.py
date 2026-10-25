"""Identity lives in the Vault: the file is the source of truth, the API is a form."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database
from backend.app.identity import greeting_for, read_identity, write_identity, IdentityWrite


def make_app(tmp_path, **kwargs):
    return create_app(
        Database(tmp_path / "identity.sqlite3"), tmp_path / "vault",
        security_environ={}, env_file=tmp_path / ".env", **kwargs)


def test_greeting_buckets_cover_the_whole_day():
    assert greeting_for(5) == greeting_for(10) == "早上好"
    assert greeting_for(11) == greeting_for(12) == "中午好"
    assert greeting_for(13) == greeting_for(17) == "下午好"
    assert greeting_for(18) == greeting_for(23) == greeting_for(0) == greeting_for(4) == "晚上好"
    with pytest.raises(ValueError):
        greeting_for(24)


def test_missing_file_reads_as_an_empty_identity(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    payload = read_identity(vault)
    assert payload["exists"] is False
    assert payload["revision"] == ""
    assert payload["name"] == "" and payload["motto"] == ""


def test_write_then_read_round_trips_and_keeps_markdown_readable(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    written = write_identity(vault, IdentityWrite(
        name="阿泽", motto="先完成，再完美", worldview="世界是概率的",
        lifeview="把日子过成可复盘的", values="诚实、节制、长期", notes="随手补充"))
    assert written["exists"] is True and written["name"] == "阿泽"
    path = vault / "01_自我档案" / "01_内核与三观" / "身份（identity）.md"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---") and "name: 阿泽" in text and "随手补充" in text

    again = read_identity(vault)
    assert again["motto"] == "先完成，再完美"
    assert again["values"] == "诚实、节制、长期"
    assert again["revision"] == written["revision"]


def test_stale_revision_is_rejected_and_file_survives(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    first = write_identity(vault, IdentityWrite(name="甲", revision=""))
    with pytest.raises(Exception) as error:
        write_identity(vault, IdentityWrite(name="乙", revision="deadbeef"))
    assert "重新读取" in str(error.value)
    assert read_identity(vault)["name"] == "甲"
    updated = write_identity(vault, IdentityWrite(name="乙", revision=first["revision"]))
    assert updated["name"] == "乙" and updated["revision"] != first["revision"]


def test_empty_identity_is_rejected(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    with pytest.raises(Exception) as error:
        write_identity(vault, IdentityWrite())
    assert "不能全部为空" in str(error.value)


def test_read_only_vault_refuses_writes(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    app = make_app(tmp_path)
    client = TestClient(app)
    client.get("/")
    assert client.get("/api/identity").json()["exists"] is False
    saved = client.put("/api/identity", json={"name": "阿泽", "motto": "", "worldview": "", "lifeview": "",
                                              "values": "", "notes": "", "revision": ""})
    assert saved.status_code == 200 and saved.json()["name"] == "阿泽"

    read_only_app = create_app(
        Database(tmp_path / "ro.sqlite3"), vault,
        security_environ={"VAULT_READ_ONLY": "1"}, env_file=tmp_path / ".env")
    read_only_client = TestClient(read_only_app)
    read_only_client.get("/")
    assert read_only_client.get("/api/identity").json()["name"] == "阿泽"
    blocked = read_only_client.put("/api/identity", json={"name": "乙", "motto": "", "worldview": "",
                                                          "lifeview": "", "values": "", "notes": "",
                                                          "revision": saved.json()["revision"]})
    assert blocked.status_code == 409
    assert Path(vault / "01_自我档案" / "01_内核与三观" / "身份（identity）.md").read_text(encoding="utf-8").find("阿泽") > 0
