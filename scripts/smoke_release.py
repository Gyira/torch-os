"""Read-only release smoke for a running 薪火OS service.

Checks health/version, the local session boundary, capability flags, and the
read endpoints of the habit, learning, body and backup modules. It creates no
records, does not touch personal data and never calls the model.

Usage:
    .venv\\Scripts\\python.exe scripts/smoke_release.py --base-url http://127.0.0.1:8765 --report data/diagnostics/release-0.4.1-smoke.json
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx


def _finalize(report: dict, failures: list[str], report_arg: str) -> str:
    """成功与失败都写盘：失败不落盘会让验收失去证据。"""
    report["ok"] = not failures
    report["failures"] = failures
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if report_arg:
        path = Path(report_arg)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    print(text)
    return text


def shanghai_monday() -> str:
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    return (today - timedelta(days=today.weekday())).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--report", default="")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    report: dict[str, object] = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base,
        "cloud_calls": 0,
        "records_created": 0,
    }
    failures: list[str] = []

    def check(name: str, response: httpx.Response, expected: int) -> None:
        report[f"{name}_status"] = response.status_code
        if response.status_code != expected:
            failures.append(f"{name}: expected {expected}, got {response.status_code}")

    try:
        with httpx.Client(base_url=base, timeout=15) as client:
            health = client.get("/api/health")
            check("health", health, 200)
            if health.status_code == 200:
                report["health"] = health.json()

            check("unauthenticated_habits", client.get("/api/habits"), 401)

            check("index", client.get("/"), 200)

            config = client.get("/api/config/status")
            check("config", config, 200)
            if config.status_code == 200:
                payload = config.json()
                report["version"] = payload.get("version")
                report["runtime_label"] = payload.get("runtime_label")
                report["capabilities"] = payload.get("capabilities")

            for name, url in (
                ("habits", "/api/habits"),
                ("subjects", "/api/learning/subjects"),
                ("weekly", f"/api/learning/weekly?week_start={shanghai_monday()}"),
            ):
                check(name, client.get(url), 200)

            check("habits_unknown_id", client.get("/api/habits/999999"), 404)
            check("habits_invalid_id", client.get("/api/habits/0"), 422)
            check("subjects_unknown_id", client.get("/api/learning/subjects/999999"), 404)
            check("subjects_invalid_id", client.get("/api/learning/subjects/0"), 422)

            shanghai_today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
            body = client.get(f"/api/body/entries/{shanghai_today.isoformat()}")
            check("body_today", body, 200)
            if body.status_code == 200:
                payload = body.json()
                if not {"date", "recorded", "revision"} <= set(payload):
                    failures.append("body: missing day payload fields")
            check("body_future", client.get(f"/api/body/entries/{(shanghai_today + timedelta(days=1)).isoformat()}"), 422)

            restored = client.get("/api/backups/restored")
            check("restored_copies", restored, 200)
            if restored.status_code == 200:
                payload = restored.json()
                report["restored_copies_total"] = payload.get("total")
                if not isinstance(payload.get("copies"), list) or not isinstance(payload.get("note"), str):
                    failures.append("restored_copies: missing inventory payload")
            check("restored_copies_invalid_name", client.get("/api/backups/restored/not-a-copy/verify"), 400)
            check("restored_copies_missing", client.get(f"/api/backups/restored/{'a' * 32}/verify"), 404)

            learning_monthly = client.get("/api/learning/monthly")
            check("learning_monthly", learning_monthly, 200)
            if learning_monthly.status_code == 200:
                payload = learning_monthly.json()
                report["learning_monthly_month"] = payload.get("month")
                totals = payload.get("totals") if isinstance(payload.get("totals"), dict) else {}
                if not isinstance(totals.get("studied_minutes"), int) or "previous_month" not in payload:
                    failures.append("learning_monthly: missing totals or previous-month comparison")
                if "不代表" not in (payload.get("warning") or ""):
                    failures.append("learning_monthly: missing non-evaluation warning")
            check("learning_monthly_invalid", client.get("/api/learning/monthly", params={"month": "2026-13"}), 422)
            check("learning_monthly_future", client.get("/api/learning/monthly", params={"month": "2099-01"}), 422)

    except Exception as error:
        # 失败也要留证据（与 browser_smoke.py 同一条规则，见 AGENTS.md）：
        # 0.96.0–0.96.3 的探针超时因为“只在成功时写报告”而长期静默。
        failures.append(f"请求失败：{type(error).__name__}: {error}")
        _finalize(report, failures, args.report)
        raise SystemExit(1)
    _finalize(report, failures, args.report)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
