r"""换机交接自检：路径、Vault、数据库与关键凭据是否就位（只读，不打印秘密）。

用法（在仓库根目录）：
    .\.venv\Scripts\python.exe scripts\check_handoff.py
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

problems: list[str] = []
warnings: list[str] = []


def line(ok: bool, label: str, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}{('  ' + detail) if detail else ''}")
    if not ok:
        problems.append(label)


def warn(label: str, detail: str = "") -> None:
    print(f"WARN  {label}{('  ' + detail) if detail else ''}")
    warnings.append(label)


def info(label: str, detail: str = "") -> None:
    print(f"INFO  {label}{('  ' + detail) if detail else ''}")


def db_counts(path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    try:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        for table in ("documents", "dida_task_links", "dida_project_links", "audit_events"):
            try:
                counts[table] = connection.execute(
                    f"SELECT count(*) FROM {table}").fetchone()[0]
            except sqlite3.Error:
                counts[table] = -1
        connection.close()
    except sqlite3.Error as error:
        warn(f"无法读取数据库 {path}", str(error))
    return counts


def main() -> int:
    print("薪火OS 换机自检\n" + "=" * 48)
    info("仓库", str(ROOT))
    line(sys.version_info >= (3, 12), "Python >= 3.12", sys.version.split()[0])

    try:
        from backend.app import config, vault_layout
    except Exception as error:  # noqa: BLE001 - 交接脚本要给出可读原因
        line(False, "导入后端配置", f"{type(error).__name__}: {error}")
        print("\n先完成依赖安装：双击 安装.cmd，或 pip install -r requirements.lock.txt")
        return 1

    vault = Path(config.VAULT_DIR)
    data_dir = Path(config.DATA_DIR)
    database = Path(config.DB_PATH)
    info("VAULT_DIR", str(vault))
    info("DATA_DIR", str(data_dir))
    info("数据库", str(database))

    line(vault.is_dir(), "Vault 目录存在")
    for name in ("00_人生路", "01_自我档案", "02_生活运营", "03_知识库"):
        line((vault / name).is_dir(), f"四库目录：{name}")
    line((vault / "00_人生路" / "08_任务与待办").is_dir(), "任务与待办目录")
    line((vault / "00_人生路" / "08_任务与待办" / "任务").is_dir(), "任务笔记目录")
    for legacy in ("00-System", "02-Life", "提示词"):
        if (vault / legacy).is_dir():
            warn(f"旧的 {legacy} 目录仍在", "确认里面是否还有未归档文件")
    journal = vault / "00_人生路" / "01_日记"
    line(journal.is_dir(), "日记目录存在", str(journal))
    line((vault / ".torch-os").is_dir(), "Vault 元数据目录 .torch-os 存在")

    layout = vault_layout.layout_status(vault)
    info("布局状态", str(layout.get("state")))
    if layout.get("state") != "v1":
        warn("布局不是 pillars-v1", "设置 →「库结构与迁移」里先预览，再决定是否迁移")

    line(database.is_file(), "数据库文件存在")
    if database.is_file():
        counts = db_counts(database)
        info("数据库计数", ", ".join(f"{key}={value}" for key, value in counts.items()))
        if counts.get("documents", 0) == 0 and counts.get("dida_task_links", 0) <= 33:
            warn("当前数据库看起来是旧库", "真实库通常在 <Vault>/.torch-os/torch_os.sqlite3")
    vault_db = vault / ".torch-os" / "torch_os.sqlite3"
    if vault_db.is_file() and vault_db.resolve() != database.resolve():
        vault_counts = db_counts(vault_db)
        info("Vault 内数据库计数", ", ".join(
            f"{key}={value}" for key, value in vault_counts.items()))
        if not database.is_file() or vault_counts.get("documents", 0) > counts.get("documents", 0):
            warn("你可能用错了数据库",
                 f"建议把 DATA_DIR 指向 {vault / '.torch-os'}，或复制其中最新的 torch_os.sqlite3")

    env_file = ROOT / ".env"
    line(env_file.is_file(), ".env 存在")
    if env_file.is_file():
        values = {}
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            key, separator, value = raw.partition("=")
            if separator:
                values[key.strip()] = value.strip()
        if values.get("VAULT_READ_ONLY", "").strip().lower() in {"1", "true", "yes", "on"}:
            warn("VAULT_READ_ONLY 已开启", "四库写入会被拒绝；确认这是你要的")
        dsh = shutil.which("dsh")
        npx = shutil.which("npx")
        if dsh:
            info("DeepSeek Harness", dsh)
        elif npx:
            info("DeepSeek Harness 未全局安装", "启动时会使用 npx（首次可能需要下载）")
        else:
            warn("未找到 dsh 或 npx", "安装 Node.js >=24.9 后执行 npm install -g @deepseek-ai/dsh")

    for label, path in (
        ("Dida 凭据", data_dir / "dida-credentials.json"),
        ("Vault 内 Dida 凭据", vault / ".torch-os" / "dida-credentials.json"),
    ):
        if path.is_file():
            info(label + "存在", f"{path.stat().st_size} B（内容不显示）")
    if not (data_dir / "dida-credentials.json").is_file() and not (vault / ".torch-os" / "dida-credentials.json").is_file():
        warn("没有找到 Dida 凭据", "如果不用滴答同步可以忽略")

    for label, path in (
        ("前端入口", ROOT / "frontend" / "index.html"),
        ("依赖锁文件", ROOT / "requirements.lock.txt"),
        ("虚拟环境", ROOT / ".venv" / "Scripts" / "python.exe"),
    ):
        line(path.is_file(), label)

    print("=" * 48)
    if problems:
        print(f"未通过 {len(problems)} 项；按上面的 FAIL 逐项修复。")
        return 1
    if warnings:
        print(f"自检通过，但有 {len(warnings)} 条提醒；不影响启动，请逐条确认。")
        return 0
    print("自检全部通过，可以启动。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
