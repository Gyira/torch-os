# 当前状态与交接（2026-10-22）

这份文档给下一次会话快速接手用；功能细节以 `README.md`、`docs/FEATURE_MAP.md` 和
`docs/薪火OS-重设计方案-v2.0.md` 为准。

## 快照

- 分支：`main`
- 最后代码提交：`0a1e724`（本文档单独提交在其后）
- 工作树：干净（提交本文档后）
- 后端：**980 passed / 1 skipped**
- 前端：**175 passed**
- `scripts/check_all.ps1`：**exit 0**
- 能力标记：**49**；SQLite：**28 张表**（其中 4 张 deprecated）；MCP 只读工具：**13 个**

## 已完成

- Phase 1–4：四库布局迁移、六项导航、四库页面、捕获/搜索、体重账目月度表、
  习惯 Markdown 镜像、周/月复盘骨架、薪火管家与带引用问答。
- Phase 5（保守清理）：
  - 退役 `bazi` / `profile` / `suggestions` / `share_import` / 旧结构化日记 `journal` 路由；
  - 删除无活引用的旧前端模块、旧日记归档月历、旧 CSS 选择器与无用依赖 `lunar-python`；
  - `bazi_profiles` / `suggestions` / `journal_entries` / `journal_actions` 保留为 deprecated，
    DDL 集中在 `db.py` 的 `LEGACY_SCHEMA`，不自动 DROP；
  - 工具桥保留 13 个只读工具，`docs/MCP_BRIDGE.md` 已补齐。
- UX 打磨：
  - 友好日期（今天 16:40 / 昨天 / 10月22日）、搜索回车打开第一条；
  - 顶部「?」帮助入口、首次使用引导卡、四库空状态示例与失败重试；
  - 小屏顶部标签单行滚动并自动定位当前页、设置侧栏自动定位；
  - 1180px 以下四库单列、章节轨横向、右栏信息下移保留；
  - 小屏任务主区优先、时间管理按钮横向 chips；
  - 页面切换淡入、toast 改用主题表面与语义边框、生活运营删除确认。
- QA/发布：
  - `browser_pillars_probe.py` 在 QA 工作区自备日记与搜索样本，真实库不写入；
  - `round.ps1` 的浏览器步骤已切换到四库探针；旧 `browser_smoke.py` 仅保留给失败报告测试。

## 仍保留 / 已知限制

- `goals.py`、`learning.py`、`body.py` 仍有活跃用途（任务导入、工作台学习分钟、
  身体记录导出、只读工具），尚未彻底退役；需要先决定这些能力迁移还是保留。
- `browser_smoke.py` 是 legacy，仅用于失败报告测试；不要作为验收证据。
- deprecated 表保留历史数据，删除/迁移需用户手动确认并先做已验证备份。
- 旧的已移除 HTTP 路由对仓库外客户端会返回 404；清单见 `docs/DEPRECATIONS.md`。
- 首版仍不做复杂图谱、聊天页或完整 Markdown 编辑器；深度编辑交给 Obsidian。

## 验证命令

```powershell
# 全量：Python + 文本/结构 + 模块面 + 前端 + 编译 + 依赖
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\check_all.ps1

# 分开跑
.\.venv\Scripts\python.exe -m pytest -q
node --experimental-vm-modules --test frontend/tests/*.test.mjs

# 隔离 QA（独立库，全虚构数据；真实库不会被写入）
.\.venv\Scripts\python.exe -m backend.app.qa_runtime --port 8766
.\.venv\Scripts\python.exe scripts\browser_pillars_probe.py --base-url http://127.0.0.1:8766 --output .test-data\pillar-probe --port 9333
```

浏览器探针会截图到输出目录，并打印 `report.json`；检查 `ok: true`、
`help_nav`、`help_hidden_outside_settings`、四库 chapter/row 指标。

## 下一步候选

1. 更完整的键盘导航与快捷键提示（搜索/捕获之外）。
2. 其他危险操作的统一确认与撤销策略。
3. 下一组产品功能（按用户需求选）。
4. 彻底退役 `goals` / `learning` / `body` 旧后端，或把它们迁移到四库 Markdown；
   这会改变任务导入、工作台学习分钟与身体记录导出，需产品确认。
