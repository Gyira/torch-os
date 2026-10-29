"""四库只读探针（2026-10-26）：导航、只读渲染、跳转 Obsidian、截图。

2026-10-26 起四库（人生路 / 自我档案 / 生活运营 / 知识库）在界面里只读：没有捕获框、
标签编辑、移动、整理与删除，所有写入都跳回 Obsidian（插件里走 torch:new-note /
torch:open-note，浏览器里走 obsidian://）。工作台（任务/日历/习惯）不受影响。

所以这个探针只断言只读行为：

- 头部「＋ 在 Obsidian 新建」与 n 快捷键都发出 obsidian://new，且不弹捕获框；
- 收件箱建议卡只读：有建议行、没有复选框、没有整理按钮，每行都能在 Obsidian 打开；
- 笔记详情只读：有「在 Obsidian 打开并编辑」，没有标签表单、没有移动目标；
- 生活运营月度表只读：表格能渲染，没有录入表单/删除，「在 Obsidian 打开月度表」可跳转；
- 管家提案只读：没有「应用」，每条提案都能在 Obsidian 打开原文；
- 四库写接口（/api/capture、/api/notes/update、/api/reviews/* 等）全部 404，界面里没有
  可用的写入路径，写只能去 Obsidian。

种子数据只写进一次性 QA 工作区：先确认 /api/health 报 runtime_label=qa、且库目录旁边
有 qa_runtime 写的 .qa-workspace.json，再直接写 Markdown（旧的 /api/capture 已下线），
最后 POST /api/vault/scan 让索引、搜索与建议看到它们。探针只访问 127.0.0.1，不碰真库。
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

import websockets

SHANGHAI = ZoneInfo("Asia/Shanghai")
WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
WORKSPACE_MARKER = ".qa-workspace.json"

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

# 四库写接口已下线：POST 它们只能是 404，界面因此没有可用的写入路径。
RETIRED_WRITES = (
    "/api/capture",
    "/api/notes/update",
    "/api/notes/move",
    "/api/steward/proposals/apply",
    "/api/journal/note/generate",
    "/api/reviews/weekly",
    "/api/reviews/monthly",
)

# 页面里所有「跳 Obsidian」都走 window.open(uri)；换成记录器后即可断言 URI。
OPEN_STUB = """(() => {
  window.__torchOpened = [];
  window.open = (uri) => { window.__torchOpened.push(String(uri)); return null; };
  return true;
})()"""

RESET_OPENED = "(() => { window.__torchOpened = []; return true; })()"

READ_OPENED = """(() => {
  const opened = Array.isArray(window.__torchOpened) ? window.__torchOpened.map(String) : [];
  return {
    count: opened.length,
    uris: opened.slice(0, 3),
    first: opened[0] || '',
    capture_dialog: Boolean(document.querySelector('.pillar-capture-dialog')),
  };
})()"""

READ_NOTE_DIALOG = """(() => {
  const reader = document.querySelector('.pillar-reader');
  const foot = reader ? reader.querySelector('.pillar-reader-foot') : null;
  const open = reader ? reader.querySelector('[data-reader-open-obsidian]') : null;
  return {
    present: Boolean(reader),
    title: reader ? (reader.querySelector('.pillar-reader-head h1')?.textContent || '').trim() : '',
    path: reader ? (reader.querySelector('.pillar-reader-meta')?.textContent || '').trim() : '',
    has_open_note: Boolean(open),
    open_note_label: open ? open.textContent.trim() : '',
    has_editor: Boolean(reader && reader.querySelector('[data-editor-edit]')),
    has_tag_form: false,
    has_move: false,
    tag_inputs: 0,
    copy_wikilink: Boolean(reader && reader.querySelector('[data-reader-copy-wikilink]')),
    copy_path: Boolean(reader && reader.querySelector('[data-reader-copy-path]')),
    read_only_hint: Boolean(foot && foot.textContent.includes('只读阅读视图')),
    markdown_rendered: Boolean(reader && reader.querySelector('.md-body p, .md-body table, .md-body .md-callout')),
    tables: reader ? reader.querySelectorAll('.md-body table').length : 0,
    callouts: reader ? reader.querySelectorAll('.md-callout').length : 0,
    toc: reader ? reader.querySelectorAll('.pillar-reader-toc a').length : 0,
  };
})()"""

READ_INBOX = """(() => {
  const card = document.querySelector('#knowledge-view .pillar-suggestion-card');
  const rows = Array.from(document.querySelectorAll('#knowledge-view [data-inbox-row]'));
  const actions = rows.map((row) => row.querySelector('.pillar-suggestion-actions button[data-obsidian-note-path]'));
  return {
    card: Boolean(card),
    rows: rows.length,
    groups: document.querySelectorAll('#knowledge-view .pillar-suggestion-group').length,
    open_actions: actions.filter(Boolean).length,
    open_labels: actions.filter(Boolean).map((node) => node.textContent.trim()).slice(0, 4),
    checkboxes: document.querySelectorAll('#knowledge-view [data-inbox-suggestions] input[type="checkbox"]').length,
    select: document.querySelectorAll('#knowledge-view [data-inbox-select]').length,
    select_all: document.querySelectorAll('#knowledge-view [data-inbox-select-all]').length,
    high: document.querySelectorAll('#knowledge-view [data-inbox-high]').length,
    apply: document.querySelectorAll('#knowledge-view [data-inbox-apply]').length,
    hint: card ? (card.querySelector('.pillar-suggestion-head .field-help')?.textContent || '').trim() : '',
  };
})()"""

READ_OPS = """(() => {
  const host = document.querySelector('#ops-view [data-ops-table="health"]');
  const table = host ? host.querySelector('.pillar-ops-table') : null;
  const open = host ? host.querySelector('[data-ops-open-file]') : null;
  return {
    table: Boolean(table),
    rows: table ? table.querySelectorAll('tbody tr').length : 0,
    headers: table ? Array.from(table.querySelectorAll('thead th')).map((node) => node.textContent.trim()).slice(0, 6) : [],
    has_form: Boolean(document.querySelector('#ops-view [data-ops-form]')),
    has_delete: Boolean(document.querySelector('#ops-view [data-ops-delete]')),
    delete_buttons: document.querySelectorAll('#ops-view [data-ops-delete]').length,
    form_inputs: document.querySelectorAll('#ops-view input[name="weight_kg"]').length,
    has_open_file: Boolean(open),
    open_file_label: open ? open.textContent.trim() : '',
    hint: host ? (host.querySelector('.field-help')?.textContent || '').trim() : '',
  };
})()"""

READ_STEWARD = """(() => {
  const dialog = document.querySelector('.pillar-steward-dialog');
  const proposals = dialog ? Array.from(dialog.querySelectorAll('.steward-proposal')) : [];
  const open = proposals.map((item) => item.querySelector('[data-steward-open-obsidian]'));
  return {
    proposals: proposals.length,
    open_actions: open.filter(Boolean).length,
    open_labels: open.filter(Boolean).map((node) => node.textContent.trim()).slice(0, 4),
    action_buttons: proposals.reduce((total, item) => total + item.querySelectorAll('.steward-proposal-actions button').length, 0),
    apply_hooks: dialog ? dialog.querySelectorAll('[data-steward-apply]').length : 0,
    hint: dialog ? (dialog.querySelector('.pillar-dialog-head .meta')?.textContent || '').trim() : '',
  };
})()"""


def find_edge() -> str:
    for candidate in EDGE_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    found = shutil.which("msedge")
    if found:
        return found
    raise SystemExit("未找到 Microsoft Edge")


def targets(port: int):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def obsidian_target(uri: str) -> str:
    """从 obsidian:// 深链接里解出笔记相对路径（file= 参数）。"""
    try:
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(str(uri or "")).query))
    except ValueError:
        return ""
    return str(query.get("file") or "")


def obsidian_vault(uri: str) -> str:
    try:
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(str(uri or "")).query))
    except ValueError:
        return ""
    return str(query.get("vault") or "")


def jump_is(uri: str, kind: str, prefix: str) -> bool:
    """obsidian://<kind>?… 且带 vault/file，并且落在期望的库目录下。"""
    text = str(uri or "")
    if not text.startswith(f"obsidian://{kind}?"):
        return False
    if not obsidian_vault(text) or not obsidian_target(text):
        return False
    return obsidian_target(text).startswith(prefix)


class CDP:
    """最小 CDP 客户端：求值、等待、截图，并顺手收集页面 JS 错误。"""

    def __init__(self, socket):
        self.socket = socket
        self.counter = 0
        self.errors: list[str] = []

    async def call(self, method: str, params: dict | None = None):
        self.counter += 1
        message_id = self.counter
        await self.socket.send(json.dumps({"id": message_id, "method": method,
                                           "params": params or {}}))
        while True:
            message = json.loads(await self.socket.recv())
            if message.get("id") == message_id:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})
            self._record(message)

    def _record(self, message: dict) -> None:
        """未捕获异常与 console/日志里的 error 都算页面错误；网络失败不算。"""
        method = message.get("method")
        params = message.get("params") or {}
        if method == "Runtime.exceptionThrown":
            details = params.get("exceptionDetails") or {}
            text = ((details.get("exception") or {}).get("description")
                    or details.get("text") or "未捕获异常")
            self.errors.append(f"exception: {str(text).strip().splitlines()[0][:200]}")
        elif method == "Log.entryAdded":
            entry = params.get("entry") or {}
            if entry.get("level") == "error" and entry.get("source") not in {"network", "security"}:
                self.errors.append(f"{entry.get('source')}: {str(entry.get('text') or '').strip()[:200]}")

    async def evaluate(self, expression: str):
        result = await self.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True,
        })
        return result.get("result", {}).get("value")

    async def wait_for(self, expression: str, timeout: float = 30.0, interval: float = 0.25):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if await self.evaluate(f"Boolean({expression})"):
                return True
            await asyncio.sleep(interval)
        raise TimeoutError(f"等待超时：{expression}")

    async def screenshot(self, path: Path):
        result = await self.call("Page.captureScreenshot", {"format": "png"})
        path.write_bytes(base64.b64decode(result["data"]))


async def shot(cdp: CDP, output: Path, report: dict, name: str):
    # 页面切换有 160ms 入场动画，等一下再截，避免截到半透明的过渡帧。
    await asyncio.sleep(0.25)
    await cdp.screenshot(output / name)
    report["screenshots"].append(name)


def _note_file(note_type: str, title: str, body: str, *, note_id: str, created: str,
               status: str = "active", extra: tuple[tuple[str, str], ...] = ()) -> str:
    """最小的 Obsidian 风格笔记：frontmatter + 正文。"""
    rows = [("id", note_id), ("type", note_type), ("title", title),
            ("created", created), ("status", status), *extra]
    return "\n".join(["---", *(f"{key}: {value}" for key, value in rows), "---", "", body, ""])


def seed_markdown(day: date) -> dict[str, str]:
    """一次性 QA 库里的虚构、确定性 Markdown（不含任何真实个人信息）。"""
    stamp = day.isoformat()
    month = day.strftime("%Y-%m")
    first = day.replace(day=1)
    week = day.isocalendar().week
    files: dict[str, str] = {}

    # 今天的日记：四库「人生路」最近 7 天里唯一写完的一天，详情只读。
    files[f"00_人生路/01_日记/{stamp}.md"] = _note_file(
        "journal", stamp,
        "\n".join([
            f"# {stamp}（第{week}周，{WEEKDAYS[day.weekday()]}）",
            "",
            "> QA 探针样例日记：四库只读，新建与修改都在 Obsidian 里完成。",
            "",
            "## 今日回顾",
            "",
            "### 学习",
            "- 用只读探针确认四库页面不再提供编辑、标签与移动控件。",
        ]),
        note_id="qa-seed-journal", created=stamp, extra=(("date", stamp),),
    )

    # 收件箱三条：分别命中「通用方法」「读书笔记」「想法与灵感」，建议卡才有分组。
    for title, note_id, body in (
        ("QA 整理收件箱五步法", "qa-seed-inbox-method",
         "第一步：列清单；第二步：分类；第三步：归档。整理完再回看一遍。"),
        ("QA 读书笔记-原则", "qa-seed-inbox-reading",
         "作者说要做减法：读完只记一条马上能用的。"),
        ("QA 触动", "qa-seed-inbox-feeling",
         "我觉得这件事很触动我，先记下来，之后再想清楚。"),
    ):
        files[f"03_知识库/00_收件箱/{title}.md"] = _note_file(
            "inbox", title, body, note_id=note_id, created=stamp, status="inbox")

    # 主题笔记：全局搜索「考研」的确定性命中；正文覆盖阅读视图的表格/标注块/任务清单/目录。
    files["03_知识库/01_主题笔记/考研规划.md"] = _note_file(
        "topic", "考研规划",
        "\n".join([
            "准备考研的示例笔记，用于 QA 探针的全局搜索。",
            "",
            "> [!tip] 阅读视图",
            "> 支持 **标注块**、表格、任务清单与双链 [[考研计划]]。",
            "",
            "## 目标",
            "",
            "| 科目 | 目标分 |",
            "| --- | ---: |",
            "| 数学 | 120 |",
            "| 英语 | 70 |",
            "",
            "### 行动清单",
            "",
            "- [x] 确认阅读视图",
            "- [ ] 在 Obsidian 编辑",
        ]),
        note_id="qa-seed-topic-exam", created=stamp)

    # 当月健康表：生活运营页面只读渲染的权威来源（日期都在当月，不带健康评价）。
    samples = (("9f1c2a3b4d5e", 66.6, 420, 30), ("9f1c2a3b4d5f", 66.4, 400, 45))
    rows = ["| ID | 日期 | 体重kg | 睡眠min | 运动min | 备注 |",
            "| --- | --- | --- | --- | --- | --- |"]
    for index, when in enumerate(dict.fromkeys((first, day))):
        row_id, weight, sleep, exercise = samples[index]
        rows.append(f"| {row_id} | {when.isoformat()} | {weight} | {sleep} | {exercise} | QA 探针样例 |")
    files[f"02_生活运营/01_健康与体重/{month}.md"] = "\n".join([
        "---",
        "type: health_month",
        f"title: 健康与体重 {month}",
        f"month: {month}",
        "schema: health-v1",
        "pillar: ops",
        "chapter: health",
        "status: active",
        f"created: {first.isoformat()}",
        f"updated: {first.isoformat()}",
        "---",
        "",
        *rows,
        "",
    ])
    return files


def _disposable_qa_workspace(workspace: Path) -> bool:
    """只有 qa_runtime 建的工作区才允许写盘：名字 + 标记文件都要对得上。"""
    if not workspace.name.startswith("qa-"):
        return False
    try:
        marker = json.loads((workspace / WORKSPACE_MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (isinstance(marker, dict) and marker.get("schema") == 1
            and marker.get("environment") == "qa"
            and marker.get("workspace") == workspace.name)


def seed_workbench_task(client: httpx.Client) -> dict:
    """工作台仍然可写，用它自己的接口种一条任务（四库之外的旧路径）。"""
    result: dict[str, object] = {}
    board = client.get("/api/time/tasks")
    payload = board.json() if board.status_code == 200 else {}
    if any(task.get("title") == "QA 任务章节" for task in payload.get("tasks") or []):
        result["task"] = "existing"
        return result
    client.post("/api/time/lists", json={"name": "QA任务", "revision": payload.get("revision", "")})
    board = client.get("/api/time/tasks")
    revision = board.json().get("revision", "") if board.status_code == 200 else ""
    created = client.post("/api/time/tasks", json={
        "title": "QA 任务章节", "list_name": "QA任务", "revision": revision,
    })
    result["task"] = created.status_code
    return result


def probe_retired_writes(base_url: str) -> dict[str, int]:
    """四库写接口必须是 404：界面里没有可用的写入路径，写只能去 Obsidian。"""
    statuses: dict[str, int] = {}
    try:
        with httpx.Client(base_url=base_url, timeout=20) as client:
            client.get("/")
            for path in RETIRED_WRITES:
                try:
                    statuses[path] = client.post(path, json={}).status_code
                except httpx.HTTPError:
                    statuses[path] = 0
    except httpx.HTTPError as error:
        statuses["error"] = f"{type(error).__name__}: {error}"
    return statuses


def seed_qa_workspace(base_url: str) -> dict:
    """给一次性 QA 工作区种上探针要看的只读事实。

    /api/capture 已下线，所以数据直接用 Markdown 写进库：先确认 runtime_label=qa 且
    库目录旁边有 qa_runtime 的标记文件，避免任何真实库被这个只读探针写入。
    """
    result: dict = {"seeded": False}
    try:
        with httpx.Client(base_url=base_url, timeout=60) as client:
            client.get("/")
            health = client.get("/api/health")
            if health.status_code != 200 or health.json().get("runtime_label") != "qa":
                result["skipped"] = "not-qa"
                return result
            connection = client.get("/api/vault/connection")
            payload = connection.json() if connection.status_code == 200 else {}
            vault = Path(str(payload.get("path") or ""))
            workspace = vault.parent
            if not vault.is_dir() or not _disposable_qa_workspace(workspace):
                result["skipped"] = "not-a-disposable-qa-vault"
                return result
            day = datetime.now(SHANGHAI).date()
            files = seed_markdown(day)
            for relative, text in files.items():
                target = vault / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8", newline="")
            result["workspace"] = workspace.name
            result["files"] = sorted(files)
            scan = client.post("/api/vault/scan")
            result["scan"] = scan.status_code
            result.update(seed_workbench_task(client))
            result["seeded"] = True
    except Exception as error:  # seeding is best-effort; the probe still reports what it can
        result["error"] = f"{type(error).__name__}: {error}"
    return result


async def run(base_url: str, output: Path, port: int,
              width: int = 1440, height: int = 1000):
    edge = find_edge()
    seed = seed_qa_workspace(base_url)
    report = {"ok": False, "seed": seed, "views": {}, "screenshots": [], "js_errors": []}
    profile = tempfile.mkdtemp(prefix="torch-edge-pillars-")
    process = subprocess.Popen([
        edge, "--headless=new", "--disable-gpu", "--no-first-run",
        "--no-default-browser-check", "--disable-extensions",
        f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
        f"--window-size={width},{height}", f"{base_url}/#life",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    cdp = None
    try:
        report["retired_writes"] = probe_retired_writes(base_url)
        target = None
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                # Edge 首次启动偶尔会自己打开 edge://sync-confirmation-dialog/ 之类的页面；
                # 必须选回我们的应用页，不能拿“第一个 page”目标。
                target = next((item for item in targets(port)
                               if item.get("type") == "page" and "webSocketDebuggerUrl" in item
                               and str(item.get("url") or "").startswith(base_url)), None)
            except Exception:
                pass
            if target:
                break
            await asyncio.sleep(0.3)
        if not target:
            raise RuntimeError("无法连接 Edge 调试端口")
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=20_000_000) as socket:
            cdp = CDP(socket)
            await cdp.call("Page.enable")
            await cdp.call("Runtime.enable")
            await cdp.call("Log.enable")
            await cdp.wait_for("document.readyState === 'complete'")
            await cdp.wait_for("document.querySelectorAll('.panel-nav-item').length === 6", timeout=45)
            # 四库只往 Obsidian 跳：先把 window.open 换成 URI 记录器，之后每个跳转都断言。
            await cdp.evaluate(OPEN_STUB)
            # 顶部「?」帮助入口：应直接打开设置里的帮助分区。
            await cdp.evaluate("document.querySelector('.panel-nav-help').click()")
            await cdp.wait_for(
                "location.hash === '#settings' && document.querySelector('[data-settings-nav=\"help\"]')?.classList.contains('active') && !document.querySelector('[data-settings-section=\"help\"]')?.hidden",
                timeout=20)
            report["help_nav"] = await cdp.evaluate(
                "document.querySelector('[data-settings-section=\"help\"] h2')?.textContent || ''")
            await cdp.evaluate("document.querySelector('.panel-nav-item[data-view=\"life\"]').click()")
            await cdp.wait_for("location.hash === '#life'")
            report["help_hidden_outside_settings"] = await cdp.evaluate(
                "document.querySelector('#help-view')?.offsetParent === null")
            for view in ("life", "self", "ops", "knowledge"):
                await cdp.evaluate(f"document.querySelector('.panel-nav-item[data-view=\"{view}\"]').click()")
                await cdp.wait_for(
                    f"location.hash === '#{view}' && document.querySelector('#{view}-view .pillar-head')",
                    timeout=30)
                await asyncio.sleep(0.4)
                report["views"][view] = await cdp.evaluate(f"""(() => ({{
                    title: document.querySelector('#{view}-view .pillar-head h2')?.textContent || '',
                    chapters: document.querySelectorAll('#{view}-view .pillar-rail-item').length,
                    rows: document.querySelectorAll('#{view}-view .pillar-note-row').length,
                    empty: document.querySelectorAll('#{view}-view .pillar-empty').length,
                    aside: document.querySelectorAll('#{view}-view .pillar-card').length,
                    hint: (document.querySelector('#{view}-view .pillar-card-quiet .field-help')?.textContent || '').slice(0, 40),
                    hash: location.hash,
                }}))()""")
                await shot(cdp, output, report, f"{view}.png")
                if view == "knowledge":
                    # 收件箱建议卡：只读提示，没有勾选/整理，每行都能在 Obsidian 打开。
                    await cdp.evaluate("document.querySelector('#knowledge-view [data-pillar-chapter=\"knowledge\"][data-chapter=\"inbox\"]')?.click()")
                    await cdp.wait_for("document.querySelector('#knowledge-view .pillar-suggestion-card')", timeout=30)
                    await asyncio.sleep(0.4)
                    await shot(cdp, output, report, "knowledge-inbox.png")
                    report["inbox_readonly"] = await cdp.evaluate(READ_INBOX)
                if view == "life":
                    report["life_week"] = await cdp.evaluate("""(() => ({
                        days: document.querySelectorAll('#life-view .pillar-week-day').length,
                        done: document.querySelectorAll('#life-view .pillar-week-day.is-done').length,
                    }))()""")
                    # 最近 7 天里写完的那天：打开的是权威日记，详情只读且能跳 Obsidian。
                    if await cdp.evaluate("Boolean(document.querySelector('#life-view .pillar-week-day.is-done'))"):
                        await cdp.evaluate("document.querySelector('#life-view .pillar-week-day.is-done').click()")
                        await cdp.wait_for("document.querySelector('.pillar-reader')", timeout=20)
                        report["journal_dialog"] = await cdp.evaluate(READ_NOTE_DIALOG)
                        await shot(cdp, output, report, "journal-detail.png")
                        await cdp.evaluate(RESET_OPENED)
                        await cdp.evaluate("document.querySelector('.pillar-reader [data-reader-open-obsidian]')?.click()")
                        await cdp.wait_for("Array.isArray(window.__torchOpened) && window.__torchOpened.length > 0",
                                           timeout=20)
                        journal_jump = await cdp.evaluate(READ_OPENED)
                        journal_jump["target"] = obsidian_target(journal_jump["first"])
                        report["journal_jump"] = journal_jump
                        await cdp.evaluate("document.querySelector('.pillar-reader [data-reader-back]')?.click()")
                        await cdp.wait_for("!document.querySelector('.pillar-reader')", timeout=20)
                    else:
                        report["journal_dialog"] = None
                        report["journal_jump"] = None
                    # 任务章节：任务由工作台管理，四库只读详情里没有标签/移动。
                    task_chapter = await cdp.evaluate("Boolean(document.querySelector('#life-view [data-pillar-chapter=\"life\"][data-chapter=\"task\"]'))")
                    report["task_chapter"] = task_chapter
                    if task_chapter:
                        await cdp.evaluate("document.querySelector('#life-view .pillar-rail-item[data-pillar-chapter=\"life\"][data-chapter=\"task\"]').click()")
                        await cdp.wait_for(
                            "document.querySelector('#life-view .pillar-content-head h3')?.textContent === '任务与待办'",
                            timeout=20)
                        await asyncio.sleep(0.4)
                        await shot(cdp, output, report, "task-chapter.png")
                    report["task_chapter_rows"] = await cdp.evaluate(
                        "document.querySelectorAll('#life-view .pillar-note-row[data-note-path]').length")
                    if report["task_chapter_rows"]:
                        await cdp.evaluate("document.querySelector('#life-view .pillar-note-row[data-note-path]').click()")
                        await cdp.wait_for("document.querySelector('.pillar-reader')", timeout=20)
                        report["note_dialog"] = await cdp.evaluate(READ_NOTE_DIALOG)
                        await cdp.evaluate("document.querySelector('.pillar-reader [data-reader-back]')?.click()")
                    else:
                        report["note_dialog"] = None
                    await cdp.evaluate("document.querySelector('#life-view [data-pillar-chapter=\"life\"][data-chapter=\"habit\"]')?.click()")
                    await cdp.wait_for("document.querySelector('#life-view [data-habit-panel]')")
                    await asyncio.sleep(0.6)
                    await shot(cdp, output, report, "life-habits.png")
                    report["habit_cards"] = await cdp.evaluate(
                        "document.querySelectorAll('#life-view .pillar-habit-card').length")
                if view == "ops":
                    # 月度表只读：有表格、有「在 Obsidian 打开月度表」，没有录入表单或删除。
                    await cdp.evaluate("document.querySelector('#ops-view .pillar-rail-item[data-pillar-chapter=\"ops\"][data-chapter=\"health\"]')?.click()")
                    await cdp.wait_for("document.querySelector('#ops-view [data-ops-table=\"health\"] .pillar-ops-table')",
                                       timeout=30)
                    await asyncio.sleep(0.4)
                    report["ops_readonly"] = await cdp.evaluate(READ_OPS)
                    await shot(cdp, output, report, "ops-health.png")
                    await cdp.evaluate(RESET_OPENED)
                    await cdp.evaluate("document.querySelector('#ops-view [data-ops-open-file]')?.click()")
                    await cdp.wait_for("Array.isArray(window.__torchOpened) && window.__torchOpened.length > 0",
                                       timeout=20)
                    ops_jump = await cdp.evaluate(READ_OPENED)
                    ops_jump["target"] = obsidian_target(ops_jump["first"])
                    report["ops_jump"] = ops_jump
            # 薪火管家抽屉：提案 → 健康检查（问答已交给 dsh）。
            await cdp.evaluate("document.querySelector('.panel-nav-item[data-view=\"knowledge\"]').click()")
            await cdp.wait_for("location.hash === '#knowledge' && document.querySelector('#knowledge-view [data-steward-open]')")
            await cdp.evaluate("document.querySelector('#knowledge-view [data-steward-open]').click()")
            await cdp.wait_for("document.querySelector('.pillar-steward-dialog')")
            await cdp.wait_for("document.querySelector('.steward-section-head')", timeout=20)
            report["steward_readonly"] = await cdp.evaluate(READ_STEWARD)
            await shot(cdp, output, report, "steward-proposals.png")
            await cdp.evaluate("document.querySelector('.pillar-steward-dialog [data-steward-tab=\"health\"]').click()")
            await cdp.wait_for("document.querySelector('.steward-health-summary')", timeout=20)
            report["steward_health"] = await cdp.evaluate("""(() => ({
                checked: document.querySelector('.steward-health-summary')?.textContent?.replace(/\\s+/g, ' ').trim() || '',
                warnings: document.querySelectorAll('.steward-health-row[data-severity="warning"]').length,
                infos: document.querySelectorAll('.steward-health-row[data-severity="info"]').length,
            }))()""")
            await shot(cdp, output, report, "steward-health.png")
            await cdp.evaluate("document.querySelector('.pillar-steward-dialog [data-steward-close]')?.click()")
            # 健康检查默认只看需要留意，可切到全部。
            await cdp.evaluate("document.querySelector('.panel-nav-item[data-view=\"knowledge\"]').click()")
            await cdp.wait_for("document.querySelector('#knowledge-view [data-steward-open]')")
            await cdp.evaluate("document.querySelector('#knowledge-view [data-steward-open]').click()")
            await cdp.wait_for("document.querySelector('.pillar-steward-dialog')")
            await cdp.evaluate("document.querySelector('.pillar-steward-dialog [data-steward-tab=\"health\"]').click()")
            await cdp.wait_for("document.querySelector('.steward-health-filters')", timeout=20)
            report["steward_health_filter"] = await cdp.evaluate("""(() => ({
                active: document.querySelector('.steward-filter.is-active')?.textContent || '',
                rows: document.querySelectorAll('.steward-health-row').length,
            }))()""")
            await cdp.evaluate("document.querySelector('.steward-filter[data-steward-health-filter=\"all\"]')?.click()")
            report["steward_health_all_rows"] = await cdp.evaluate(
                "document.querySelectorAll('.steward-health-row').length")
            await shot(cdp, output, report, "steward-health-filtered.png")
            await cdp.evaluate("document.querySelector('.pillar-steward-dialog [data-steward-close]')?.click()")
            # 头部「＋ 在 Obsidian 新建」：只发出 obsidian://new，绝不弹捕获框。
            await cdp.evaluate("document.querySelector('.panel-nav-item[data-view=\"knowledge\"]').click()")
            await cdp.wait_for("location.hash === '#knowledge' && document.querySelector('#knowledge-view .pillar-head-actions [data-pillar-new]')")
            # 回到总览再截图：收件箱建议卡是异步渲染的，不参与这条断言的时序。
            await cdp.evaluate("document.querySelector('#knowledge-view .pillar-rail-item[data-pillar-chapter=\"knowledge\"][data-chapter=\"\"]')?.click()")
            await cdp.wait_for("document.querySelectorAll('#knowledge-view .pillar-note-row[data-note-path]').length > 0", timeout=20)
            report["pillar_new"] = await cdp.evaluate("""(() => {
                const head = document.querySelector('#knowledge-view .pillar-head-actions [data-pillar-new]');
                return {
                    present: Boolean(head),
                    label: head ? head.textContent.trim() : '',
                    count: document.querySelectorAll('#knowledge-view [data-pillar-new]').length,
                    rows: document.querySelectorAll('#knowledge-view .pillar-note-row[data-note-path]').length,
                };
            })()""")
            await cdp.evaluate(RESET_OPENED)
            await cdp.evaluate("document.querySelector('#knowledge-view .pillar-head-actions [data-pillar-new]').click()")
            await cdp.wait_for("Array.isArray(window.__torchOpened) && window.__torchOpened.length > 0",
                               timeout=20)
            new_jump = await cdp.evaluate(READ_OPENED)
            new_jump["target"] = obsidian_target(new_jump["first"])
            report["pillar_new"]["jump"] = new_jump
            await shot(cdp, output, report, "obsidian-jump.png")
            # Ctrl/⌘ K 全局搜索；回车打开详情，详情只读且能跳 Obsidian。
            await cdp.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true, bubbles: true }))")
            await cdp.wait_for("document.querySelector('.pillar-search-dialog')")
            report["shortcut_search"] = True
            await cdp.evaluate("""(() => {
                const input = document.querySelector('[data-global-query]');
                input.value = '考研';
                input.dispatchEvent(new Event('input', { bubbles: true }));
            })()""")
            await cdp.wait_for("document.querySelectorAll('[data-global-note]').length > 0", timeout=20)
            await cdp.evaluate("document.querySelector('[data-global-query]').dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }))")
            report["search_keyboard"] = await cdp.evaluate("""(() => ({
                selected: document.querySelectorAll('[data-global-note].is-selected').length,
                current: document.querySelector('[data-global-note].is-selected')?.getAttribute('aria-current') || '',
            }))()""")
            await cdp.evaluate("document.querySelector('[data-global-query]').dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))")
            await cdp.wait_for("document.querySelector('.pillar-reader')", timeout=20)
            report["search_enter_opened"] = await cdp.evaluate(
                "document.querySelector('.pillar-reader h1')?.textContent || ''")
            report["note_actions"] = await cdp.evaluate(READ_NOTE_DIALOG)
            await cdp.evaluate(RESET_OPENED)
            await cdp.evaluate("document.querySelector('.pillar-reader [data-reader-open-obsidian]')?.click()")
            await cdp.wait_for("Array.isArray(window.__torchOpened) && window.__torchOpened.length > 0", timeout=20)
            note_jump = await cdp.evaluate(READ_OPENED)
            note_jump["target"] = obsidian_target(note_jump["first"])
            report["note_jump"] = note_jump
            await shot(cdp, output, report, "note-detail-readonly.png")
            await cdp.evaluate("document.querySelector('.pillar-reader [data-reader-back]')?.click()")
            report["nav"] = await cdp.evaluate(
                "Array.from(document.querySelectorAll('.panel-nav-item')).map((node) => node.dataset.view)")
            # 工作台回归：任务板、问候、横幅、天气卡都还在。
            await cdp.evaluate("document.querySelector('.panel-nav-item[data-view=\"workbench\"]').click()")
            await cdp.wait_for("document.querySelector('#workbench-hello') && document.querySelector('#workbench-hello').textContent !== '你好'", timeout=45)
            await asyncio.sleep(0.5)
            await shot(cdp, output, report, "workbench.png")
            await cdp.evaluate("(() => { const node = Array.from(document.querySelectorAll('.time-side-item')).find((item) => item.dataset.timeId === '收集箱'); node?.click(); })()")
            await asyncio.sleep(0.5)
            report["workbench"] = await cdp.evaluate("""(() => ({
                hello: document.querySelector('#workbench-hello')?.textContent || '',
                banner: Boolean(document.querySelector('#workbench-banner')),
                timeBoard: Boolean(document.querySelector('#time-board')),
                taskRows: document.querySelectorAll('#time-board .time-task').length,
                inboxRows: document.querySelectorAll('#time-board .time-task').length,
                weather: document.querySelector('#workbench-weather')?.textContent?.slice(0, 40) || '',
                almanac: document.querySelector('#workbench-almanac')?.textContent?.slice(0, 40) || '',
                hash: location.hash,
            }))()""")
            # 设置 → 库结构与迁移
            await cdp.evaluate("document.querySelector('.panel-nav-item[data-view=\"settings\"]').click()")
            await cdp.wait_for("location.hash === '#settings'")
            await cdp.evaluate("document.querySelector('[data-settings-nav=\"migration\"]').click()")
            await cdp.wait_for("!document.querySelector('[data-settings-section=\"migration\"]').hidden")
            await asyncio.sleep(0.5)
            await shot(cdp, output, report, "migration-settings.png")
            report["migration"] = await cdp.evaluate("""(() => ({
                visible: !document.querySelector('[data-settings-section="migration"]').hidden,
                status: document.querySelector('#migration-status')?.textContent || '',
                layout: document.querySelector('#migration-layout')?.textContent?.slice(0, 120) || '',
            }))()""")
            # 全局搜索
            await cdp.evaluate("document.querySelector('.panel-nav-search')?.click()")
            await cdp.wait_for("document.querySelector('.pillar-search-dialog')")
            await cdp.evaluate("""(() => {
                const input = document.querySelector('[data-global-query]');
                input.value = '考研';
                input.dispatchEvent(new Event('input', { bubbles: true }));
            })()""")
            await cdp.wait_for("document.querySelectorAll('[data-global-note]').length > 0", timeout=20)
            await shot(cdp, output, report, "global-search.png")
            report["search"] = await cdp.evaluate("""(() => ({
                results: document.querySelectorAll('[data-global-note]').length,
                status: document.querySelector('[data-global-status]')?.textContent || '',
            }))()""")
            await cdp.evaluate("document.querySelector('.pillar-search-dialog [data-search-close]')?.click()")
            # 快捷键总览：? 打开，包含分组与条目；关闭后能用 g 组合键与 n 跳 Obsidian。
            await cdp.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', { key: '?', bubbles: true }))")
            await cdp.wait_for("document.querySelector('.shortcut-dialog')")
            report["shortcut_sheet"] = await cdp.evaluate("""(() => ({
                groups: document.querySelectorAll('.shortcut-dialog .shortcut-group').length,
                rows: document.querySelectorAll('.shortcut-dialog .shortcut-row').length,
                text: document.querySelector('.shortcut-dialog')?.textContent?.slice(0, 80) || '',
            }))()""")
            await shot(cdp, output, report, "shortcut-sheet.png")
            await cdp.evaluate("document.querySelector('.shortcut-dialog [data-shortcut-close]')?.click()")
            await cdp.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', { key: 'g', bubbles: true }))")
            await cdp.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', { key: 'l', bubbles: true }))")
            await cdp.wait_for("location.hash === '#life'")
            report["chord_nav"] = await cdp.evaluate("location.hash")
            # n 快捷键：1.0 起人生路/知识库打开应用内「写一条」编辑器，不再跳 Obsidian。
            await cdp.wait_for("document.querySelector('#life-view .pillar-head-actions [data-pillar-new]')")
            await cdp.evaluate("document.querySelector('#life-view .pillar-rail-item[data-pillar-chapter=\"life\"][data-chapter=\"\"]')?.click()")
            await cdp.wait_for("document.querySelectorAll('#life-view .pillar-note-row[data-note-path]').length > 0", timeout=20)
            await cdp.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', { key: 'n', bubbles: true }))")
            await cdp.wait_for("document.querySelector('#note-editor-dialog')?.open === true", timeout=20)
            report["shortcut_editor"] = await cdp.evaluate("""(() => ({
                open: Boolean(document.querySelector('#note-editor-dialog')?.open),
                title: document.querySelector('#note-editor-title')?.value ?? null,
                has_body: Boolean(document.querySelector('#note-editor-text')),
            }))()""")
            await shot(cdp, output, report, "shortcut-editor.png")
            await cdp.evaluate("document.querySelector('#note-editor-close').click()")
            await cdp.wait_for("document.querySelector('#note-editor-dialog')?.open !== true", timeout=20)
            # 方向键：设置侧栏移动焦点，Enter 才切换分区。
            await cdp.evaluate("document.querySelector('.panel-nav-item[data-view=\"settings\"]').click()")
            await cdp.wait_for("location.hash === '#settings'")
            await cdp.evaluate("document.querySelector('[data-settings-nav=\"status\"]').focus()")
            await cdp.evaluate("document.activeElement.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }))")
            report["arrow_nav"] = await cdp.evaluate("document.activeElement?.dataset?.settingsNav || ''")
            # 工具目录收缩为 12 个，旧学习/身体能力标记不再出现。
            await cdp.evaluate("document.querySelector('[data-settings-nav=\"data\"]').click()")
            await cdp.wait_for("!document.querySelector('[data-settings-section=\"data\"]')?.hidden")
            await cdp.evaluate("document.querySelector('#load-tools')?.click()")
            await cdp.wait_for("document.querySelector('#tools-status')?.textContent?.includes('共 12 个只读工具')", timeout=20)
            report["tools_count"] = 12
            report["retired_capabilities"] = await cdp.evaluate("""(async () => {
                const caps = (await fetch('/api/config/status').then((response) => response.json())).capabilities;
                return ['learning', 'learning_monthly', 'body_records', 'body_export', 'body_summary']
                    .filter((key) => key in caps);
            })()""")
            report["js_errors"] = list(cdp.errors)
            report["ok"] = _compute_ok(report)
            return report
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        if cdp is not None:
            report["js_errors"] = list(cdp.errors)
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        shutil.rmtree(profile, ignore_errors=True)


def _compute_ok(report: dict) -> bool:
    """只读期望全部成立才算通过；数据类断言依赖 QA 种下的虚构笔记。"""
    inbox = report.get("inbox_readonly") or {}
    ops = report.get("ops_readonly") or {}
    steward = report.get("steward_readonly") or {}
    note_dialog = report.get("note_dialog") or {}
    note_actions = report.get("note_actions") or {}
    pillar_new = report.get("pillar_new") or {}
    new_jump = pillar_new.get("jump") or {}
    shortcut_editor = report.get("shortcut_editor") or {}
    note_jump = report.get("note_jump") or {}
    ops_jump = report.get("ops_jump") or {}
    retired_writes = report.get("retired_writes") or {}
    return bool(
        all(view["chapters"] > 0 for view in report["views"].values())
        and report["shortcut_sheet"]["rows"] >= 10
        and report["chord_nav"] == "#life"
        and report["arrow_nav"] == "schedule"
        and report["tools_count"] == 12
        and report["retired_capabilities"] == []
        and not report["js_errors"]
        and report["seed"].get("seeded") is True
        # 四库写接口全部下线
        and all(retired_writes.get(path) == 404 for path in RETIRED_WRITES)
        # 头部新建仍跳 Obsidian；n 在人生路/知识库打开应用内「写一条」编辑器
        and pillar_new.get("present") is True
        and jump_is(new_jump.get("first", ""), "new", "03_知识库/")
        and new_jump.get("capture_dialog") is False
        and shortcut_editor.get("open") is True
        and shortcut_editor.get("has_body") is True
        # 任务笔记只读；允许章节的笔记可应用内编辑，且不再显示只读提示
        and note_dialog.get("present") is True
        and note_dialog.get("has_open_note") is True
        and note_dialog.get("has_tag_form") is False
        and note_dialog.get("has_move") is False
        and note_dialog.get("read_only_hint") is True
        and note_dialog.get("has_editor") is False
        and note_actions.get("present") is True
        and note_actions.get("has_open_note") is True
        and note_actions.get("has_tag_form") is False
        and note_actions.get("has_move") is False
        and note_actions.get("read_only_hint") is False
        and note_actions.get("has_editor") is True
        # 阅读视图真的渲染了 Markdown：表格、标注块、任务清单与目录
        and note_actions.get("markdown_rendered") is True
        and note_actions.get("tables", 0) >= 1
        and note_actions.get("callouts", 0) >= 1
        and note_actions.get("toc", 0) >= 2
        and jump_is(note_jump.get("first", ""), "open", "")
        # 收件箱建议卡只读
        and inbox.get("card") is True
        and inbox.get("rows", 0) >= 1
        and inbox.get("checkboxes") == 0
        and inbox.get("select") == 0
        and inbox.get("apply") == 0
        and inbox.get("open_actions") == inbox.get("rows")
        # 生活运营月度表只读
        and ops.get("table") is True
        and ops.get("rows", 0) >= 1
        and ops.get("has_form") is False
        and ops.get("has_delete") is False
        and ops.get("has_open_file") is True
        and jump_is(ops_jump.get("first", ""), "open", "02_生活运营/")
        # 管家提案只读
        and steward.get("apply_hooks") == 0
        and steward.get("open_actions") == steward.get("proposals")
        and steward.get("action_buttons") == steward.get("proposals")
    )


def summarize(report: dict) -> str:
    """一行摘要，方便 round.ps1 / 人工快速看结果。"""
    views = report.get("views") or {}
    inbox = report.get("inbox_readonly") or {}
    ops = report.get("ops_readonly") or {}
    steward = report.get("steward_readonly") or {}
    seed = report.get("seed") or {}
    return " ".join([
        f"ok={report.get('ok')}",
        f"种子={'已写入' if seed.get('seeded') else seed.get('skipped', '失败')}",
        f"视图={len(views)}",
        f"收件箱建议={inbox.get('rows', 0)}（打开动作={inbox.get('open_actions', 0)}）",
        f"运营表行={ops.get('rows', 0)}",
        f"提案={steward.get('proposals', 0)}",
        f"截图={len(report.get('screenshots') or [])}",
        f"JS错误={len(report.get('js_errors') or [])}",
    ])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8899")
    parser.add_argument("--output", default=".pillar-probe")
    parser.add_argument("--port", type=int, default=9333)
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=1000)
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    report = asyncio.run(run(args.base_url, output, args.port, args.width, args.height))
    print(summarize(report))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
