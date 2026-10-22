# 当前状态与交接（2026-10-22）

这份文档给下一次会话快速接手用；功能细节以 `README.md`、`docs/FEATURE_MAP.md` 和
`docs/薪火OS-重设计方案-v2.0.md` 为准。

## 快照

- 分支：`main`
- 代码基线：`6aba049`（收件箱批量整理；以 `git log -1` 为准）
- 工作树：笔记详情操作、四库数据整理（任务路径迁移）尚未提交
- 后端：**807 passed / 1 skipped / 0 failed**（2026-10-22 最终单独重跑，167.27 秒）
- 前端：**186 passed**
- `scripts/check_all.ps1`：**exit 0**
- 能力标记：**44**；用户表：**25 张**（其中 7 张 deprecated）；MCP 只读工具：**12 个**
- 运行环境：`.env` 已指向真实 Vault（`C:\Users\<用户名>\Desktop\个人数据库`）；
  后端测试使用隔离临时目录，可在这个 `.env` 下直接跑。
- 排障提示：如果全量测试突然出现大量 setup `PermissionError`（`%TEMP%\torch-pytest`
  被占用），通常是有两个 pytest 全量进程同时在跑、共用并互删同一个临时根目录；
  确认只剩一个进程后重跑即可，不是代码回归。

## 已完成

- **2026-10-22 数据整理**：`00-System` / `02-Life` / `提示词` 全部并入四库；
  任务与待办在 `00_人生路/08_任务与待办`（任务笔记在 `任务/`，配置/迁移记录在 `_系统/`），
  旧重复文件归档到 `_旧版`；滴答映射 relpath、任务回收站、内部链接与索引已同步迁移。
- Phase 1–4：四库布局迁移、六项导航、四库页面、捕获/搜索、体重账目月度表、
  习惯 Markdown 镜像、周/月复盘骨架、薪火管家与带引用问答。
- Phase 5（收尾）：
  - 退役 `bazi` / `profile` / `suggestions` / `share_import` / 旧结构化日记 `journal` 路由；
  - **2026-10-22 退役 `goals` / `learning` / `body` 旧后端**：
    - 目标改读人生路年度/季度/月度目标笔记；`GET /api/goals` 仅返回这些笔记，写接口已移除；
    - 任务导入不再写旧 SQLite `tasks`，改为创建 Markdown 任务笔记
      （`00_人生路/08_任务与待办/任务/**`），
      任务 frontmatter 写入 `目标` 与稳定 `导入键`，重名/移动后仍能去重；
    - 身体汇总、调度草稿与导出改读「健康与体重」月度 Markdown 表；
    - 学习分钟、学习工具、学习导出与 `/api/learning/*` 下线；工作台/日记草稿不再出现学习行；
    - `goals.py` / `learning.py` / `body.py` 已删除；旧表保留为 deprecated，无写入方、不自动 DROP。
  - 删除无活引用的旧前端模块、旧日记归档月历、旧 CSS 选择器与无用依赖 `lunar-python`；
  - `bazi_profiles` / `suggestions` / `journal_entries` / `journal_actions` /
    `goals` / `tasks` / `study_subjects` / `study_sessions` / `body_entries` /
    `task_import_ledger` 保留历史数据，DDL 不自动 DROP；
  - 工具桥收缩为 12 个只读工具，`docs/MCP_BRIDGE.md` 已同步。
- UX 打磨：
  - 知识库收件箱整理建议按目标章节分组、标注置信度，支持勾选/全选/只选高置信后批量移动，
    执行前确认；单条失败不回滚已成功项，建议卡内可直接「打开」原文，
    已进入建议卡的条目不再在下方重复。
  - 四库笔记详情支持复制 Obsidian 双链/路径、增删标签、移动章节与查看反向链接。
  - 友好日期（今天 16:40 / 昨天 / 10月22日）、搜索回车打开第一条；
  - 顶部「?」帮助入口、首次使用引导卡、四库空状态示例与失败重试；
  - 小屏顶部标签单行滚动并自动定位当前页、设置侧栏自动定位；
  - 1180px 以下四库单列、章节轨横向、右栏信息下移保留；
  - 小屏任务主区优先、时间管理按钮横向 chips；
  - 页面切换淡入、toast 改用主题表面与语义边框、生活运营删除确认。
  - 键盘导航与快捷键：`?` 打开快捷键总览，帮助页有同一入口；`g` 后按
    `w/l/s/o/k/,`/`h` 跳转，`n` 在四库打开「记一条」、在工作台聚焦任务输入；
    顶部标签、设置侧栏、四库章节轨、最近 7 天与任务页标签支持方向键，Home/End 到两端；
    时间页标签改为 roving tabindex，只有当前标签在 Tab 顺序里。
  - 危险操作策略：`confirmAction` 新增危险色选项，清空/彻底删除回收站、回滚迁移、
    清除滴答凭据、开启本地删除回写使用它；生活运营删除行后 15 秒内可撤销，
    按原 row_id 与位置写回，后端新增 `POST /api/ops/{dataset}/months/{month}/rows/{row_id}/restore`。
    toast 支持一个动作按钮；确认框默认焦点仍在“取消”。
  - 任务 ↔ 目标联动：任务行显示目标 chip，点击只看该目标；分组菜单新增「目标」，
    未关联任务归入「未关联目标」；目标笔记详情列出关联任务，可直接打开任务文档。
    任务板加载时并行读取 `GET /api/goals`，目标标题变化会触发重画。
- QA/发布：
  - `browser_pillars_probe.py` 在 QA 工作区自备日记与搜索样本，真实库不写入；
    探针新增快捷键总览、g 组合跳转、n 捕获、方向键与生活运营删除/撤销检查；
  - `round.ps1` 的浏览器步骤已切换到四库探针；旧 `browser_smoke.py` 仅保留给失败报告测试。

## 仍保留 / 已知限制

- 旧 `goals` / `tasks` / `study_subjects` / `study_sessions` / `body_entries` /
  `task_import_ledger` 表只保留历史数据，没有写入方；删除/迁移需用户手动确认并先做已验证备份。
- 学习分钟与学习导出已下线；旧的 `study_*` 数据仍可在数据库/备份里阅读，但产品不再展示。
- 2026-10-22 整理期间发现滴答远端项目（工作任务/购物清单/锻炼计划）与本地旧项目映射不一致；
  本地任务笔记完整，但先检查「设置 → 滴答同步」再决定是否重新同步/推送，避免覆盖远端。
- `scripts/reorganize_legacy.py` 是一次性数据整理脚本，内含个人文件名映射；默认 dry-run，
  只有 `--apply --confirm APPLY` 才执行。真实库已完成整理；仓库是公开的，该文件已加入
  `.gitignore`，只在本机保留，不提交。
- `browser_smoke.py` 是 legacy，仅用于失败报告测试；不要作为验收证据。
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
`help_nav`、`help_hidden_outside_settings`、快捷键/撤销检查与四库 chapter/row 指标。

## 下一步候选

本轮用户指定顺序：**键盘导航（已完成）→ 危险操作确认与撤销（已完成）→ 退役旧后端（已完成）→ 新产品功能（任务 ↔ 目标联动、知识库收件箱批量整理已完成）**。

1. 四库笔记详情操作已完成：复制 Obsidian 双链/路径、增删标签、移动章节、反向链接。
2. 用真实库做一次完整备份 → 恢复演练 → 视情况清理 deprecated 表（必须由用户确认）。
3. 继续选下一组产品功能（按用户需求）。
