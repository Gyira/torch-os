"""Vault audit is read-only and reports structure issues without content."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database
from backend.app.model_gateway import ModelGateway


def build_client(tmp_path, vault_name="vault"):
    vault = tmp_path / vault_name
    database = Database(tmp_path / f"{vault_name}.sqlite3")
    gateway = ModelGateway(provider="local", base_url="", model="", api_key="", timeout=1)
    app = create_app(database, vault, gateway, security_environ={})
    client = TestClient(app)
    client.get("/")
    return client, vault, database


def write(vault: Path, relative: str, text: str) -> Path:
    path = vault / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_vault_audit_reports_structure_issues_without_content_or_writes(tmp_path):
    client, vault, database = build_client(tmp_path)
    write(vault, "01-Identity/profile.md",
          "---\nid: a1\ntype: fact\ntitle: 正常档案\n---\n我是示例用户，内容不应出现在报告里。\n")
    write(vault, "02-Goals/goals.md", "今年目标是……\n")
    write(vault, "03-Learning/study.md", "---\nid: a2\ntype: note\n---\n缺少 title。\n")
    write(vault, "04-Health/health.md", "---\nid: a3\ntype: experience\ntitle: 空正文\n---\n")
    write(vault, "05-Work/one.md", "---\nid: dup\ntype: note\ntitle: 重复一\n---\n内容一。\n")
    write(vault, "05-Work/two.md", "---\nid: dup\ntype: note\ntitle: 重复二\n---\n内容二。\n")
    write(vault, "06-Beliefs/bad.md", "---\nid: [unclosed\ntype: belief\n---\n坏掉的 YAML。\n")
    (vault / "relationships").mkdir(parents=True, exist_ok=True)
    (vault / "relationships" / "secret.md").write_text("关系分区不应被检查。", encoding="utf-8")
    (vault / "07-Preferences").mkdir(parents=True, exist_ok=True)
    (vault / "07-Preferences" / "binary.md").write_bytes(b"\xff\xfe\x00\x01not utf8")

    before = {path: path.read_bytes() for path in vault.rglob("*") if path.is_file()}
    with database.connect() as connection:
        audit_before = connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]

    report = client.get("/api/vault/audit").json()
    assert report["checked_files"] == 8
    assert report["ok_files"] == 1
    counts = report["counts"]
    assert counts["missing_frontmatter"] == 1
    assert counts["invalid_frontmatter"] == 1
    assert counts["missing_fields"] == 1
    assert counts["duplicate_id"] == 2
    assert counts["empty_body"] == 1
    assert counts["decode_failed"] == 1
    assert counts["too_large"] == 0 and counts["unreadable"] == 0

    codes = {(item["path"], item["code"]) for item in report["issues"]}
    assert ("02-Goals/goals.md", "missing_frontmatter") in codes
    assert ("03-Learning/study.md", "missing_fields") in codes
    assert ("04-Health/health.md", "empty_body") in codes
    assert ("05-Work/one.md", "duplicate_id") in codes
    assert ("05-Work/two.md", "duplicate_id") in codes
    assert ("06-Beliefs/bad.md", "invalid_frontmatter") in codes
    assert ("07-Preferences/binary.md", "decode_failed") in codes

    text = json.dumps(report, ensure_ascii=False)
    assert "内容不应出现在报告里" not in text
    assert "关系分区不应被检查" not in text
    assert all("secret.md" not in item["path"] for item in report["issues"])

    after = {path: path.read_bytes() for path in vault.rglob("*") if path.is_file()}
    assert before == after, "audit must not change any file"
    with database.connect() as connection:
        audit_after = connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    assert audit_before == audit_after, "audit must not write audit events"


def test_vault_audit_handles_an_empty_vault_and_requires_the_local_session(tmp_path):
    client, _, _ = build_client(tmp_path)
    report = client.get("/api/vault/audit").json()
    assert report["checked_files"] == 0 and report["ok_files"] == 0 and report["issues"] == []
    assert "不包含任何正文内容" in report["note"]

    anonymous = TestClient(client.app)
    assert anonymous.get("/api/vault/audit").status_code == 401
