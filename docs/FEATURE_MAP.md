# 薪火OS 功能地图（0.99）

这份文档是「当前仓库实际能做什么」的清单，不是愿景。它与运行中的
`GET /api/config/status`、`GET /api/tools` 和 SQLite 结构保持一致；契约测试
`tests/test_docs_contract.py` 会在能力标记漂移时失败。

`GET /api/config/status` 返回当前版本与能力标记：`attachment_localize`、`attachment_plan`、`audit_summary`、`backup_plan`、`candidate_tidy`、`conversation_export`、`conversation_extract`、`diagnostics`、`dida_sync`、`dsh_web`、`export_bundle`、`growth_export`、`habits`、`history_cleanup_preview`、`history_restore`、`identity`、`import_history`、`journal_export`、`link_audit`、`love`、`love_lijie`、`mcp_bridge`、`memory_batch`、`memory_delete`、`memory_export`、`monthly_review`、`planning_export`、`restore_drill`、`restored_copies`、`scheduler`、`scheduler_weekly`、`schema_export`、`task_reorder`、`tasks`、`tools`、`vault_admin`、`vault_audit`、`vault_connection`、`weather`、`workbench`、`workspaces`

## 一级导航

| 视图 | hash | 说明 |
|---|---|---|
| 工作台 | `#workbench` | 问候、横幅、时钟/天气/黄历、时间管理（任务、日历、习惯打卡、四象限、回收站） |
| 人生路 | `#life` | 日记、任务与待办、反思复盘、心得体会、目标、项目、习惯追踪、工作复盘；章节轨按分组折叠、最近 7 天日记圆点 |
| 自我档案 | `#self` | 身份卡、记忆长河、想法、关系、意义与目标、沉淀与创作 |
| 生活运营 | `#ops` | 健康与体重、财富与记账、体检与医疗、证件与事务；二级分区「恋爱系统」（聊天分析、画像、日志、报告、丽洁追踪） |
| 知识库 | `#knowledge` | 收件箱、主题/读书/课程笔记、概念卡、方法库、输出与作品 |
| 设置 | `#settings` | 备份与恢复、库结构与迁移、诊断、导出、数据手册、工具桥、侧边栏顺序、Obsidian 库连接（个人数据库）、帮助 |

旧 hash（`#today`、`#identity`、`#profile`、`#memory`、`#bazi`、`#goals`、`#habits`、
`#brain`、`#documents`、`#notes`、`#archive`、`#help`）仍由 `frontend/app.js` 的
`GO_TARGETS` 做一次性重定向，避免旧书签失效。

## 工作台

- 问候语、横幅（座右铭/统计、背景、字体）、时钟、5 日天气、黄历。
- 任务：一任务一笔记（`00_人生路/08_任务与待办/任务/**`），看板/清单/标签、日期时间优先级、
  多选批量、右键菜单、手动排序、拖拽换清单、四象限换格、回收站、滴答双向同步。
- 日历：年/月/周/日/日程/多日/多周视图；任务按清单颜色着色，时间任务上时刻轴。
- 习惯打卡：每日必做来自 `00_人生路/08_任务与待办/_系统/待办（todos）.md`，勾选写入当天日记；
  四库「人生路 → 习惯」提供同一份 Markdown 习惯数据的面板。
- 旧式 `任务（tasks）.md` 复选框任务只读保留。
- 资料里的 Markdown 复选框可预览后复制为独立任务笔记；每篇笔记带稳定 `导入键`，
  重名/移动后重复导入仍返回原任务；可选关联人生路目标笔记（写入 frontmatter `目标`）。
- 任务与目标联动：任务行显示目标 chip，点击只看该目标；分组菜单新增「目标」；
  目标笔记详情列出关联任务并可打开任务文档。
- 右侧倒计时卡（仿 Apex Dashboard）：目标日期时间、天/小时/分钟、提前提醒、标签与背景；
  齿轮打开设置，默认指向下一个元旦。配置保存在浏览器 LocalStorage（不写 Markdown、不上传），
  本地图片背景也只在浏览器里读取。
- 右侧小组件（天气、运势、倒计时）可拖拽互换顺序，落点上下半区决定插到前/后；顺序只保存在
  当前浏览器。新增右栏组件时，只要给 `.workbench-side` 的直接子元素补 `data-widget`（可选
  `data-widget-label`），就会自动获得拖拽和 ↑/↓ 排序。

## 四库

- 四库只读：插件内不写 Markdown，新建/改标签/移章节都在 Obsidian 里做。
  笔记以页内阅读视图打开：Markdown 按 Obsidian 风格渲染（标题/表格/任务清单/标注块/代码块/双链），
  右侧目录，下方关联任务与反向链接；支持复制双链/路径和「在 Obsidian 打开并编辑」。

### 人生路

- 与工作台共用 `00_人生路/01_日记/YYYY-MM-DD.md`；「写今日日记」在 Obsidian 里打开权威文件
  （没有就由 Obsidian 新建）。
- 布局 v2 的章节目录：反思/周复盘/月复盘在 `00_人生路/02_反思复盘/`（`01_反思`、`02_周复盘`、
  `03_月复盘`），年度/季度/月度目标在 `00_人生路/04_目标/01..03`，项目计划与项目复盘在
  `00_人生路/05_项目/01_项目计划`、`02_项目复盘`；日记、任务、习惯、会议/踩坑路径不变。
- 侧边栏按布局 v2 分组：心得体会、反思复盘、目标、项目四个一级分组（`chapter_manifest()` 的
  `groups`，只有人生路非空）。分组行出现在该组首个章节的位置，点标题折叠/展开
  （`aria-expanded`，状态存 `localStorage` 的 `torch:rail-groups:life`）；子章节缩进，当前
  章节自动展开所属分组，方向键跳过折叠里的章节，未分组章节保持顶层。
- 「打开本周复盘 / 打开本月复盘」只跳转 Obsidian，不生成骨架内容。
- 年度/季度/月度目标笔记是目标选择器与目标导出/只读工具的唯一来源。
- 最近 7 天日记圆点；旧目录日记会提示先在「库结构与迁移」里迁移。
- 习惯定义与打卡镜像在 `00_人生路/06_目标与计划/04_习惯追踪/`（v2 不变）。

### 自我档案

- 身份/三观（`01_自我档案/01_内核与三观/身份（identity）.md`，与工作台横幅共用）。
- 记忆与自传、想法与灵感、情感与关系、意义与目标、沉淀与创作。
- 管家会提示可从反思/心得提炼原则；原则草稿的新建与双链回原文在 Obsidian 里做。

### 生活运营

- 体重/健康、财务账目使用「月度 Markdown 表 + SQLite 投影」，行有稳定 `row_id`。
- 体检/医疗、证件/事务一次事件一篇笔记。
- 月度表只读展示趋势 sparkline 与分类占比；「在 Obsidian 打开月度表」跳到原文，
  新增/修改/删除行都在 Obsidian 里做。

### 恋爱系统（生活运营的二级分区，2026-10-26）

- 位于「生活运营」模块的二级菜单（顶部「生活运营 / 恋爱系统」标签），不是新的顶层模块；不再启动原「恋爱分析助手」的 Electron/FastAPI 服务。
- 直接读写本机原 SQLite（`LOVE_DB_PATH`，默认 `%LOCALAPPDATA%\love-analysis-assistant\love_analysis.db`），
  已有项目、消息、画像、分析记录和日志继续可用；导入的新项目写同一份数据库。
- 原生页面：总览、导入聊天（TXT/编码识别）、统计（趋势/回复间隔/媒体/高频词）、AI 深度分析、
  画像（锁定/编辑/删除）、恋爱日志（手写与按天 AI 生成）、HTML 报告（独立文档预览与打印）、
  丽洁追踪（交路表 Excel、车次时刻表、实时位置、沿线天气）、设置（DeepSeek Key）。
- AI 调用只在用户显式操作时发生；发送前脱敏姓名、电话、证件、邮箱、链接和银行卡号。
  Key 使用 AES-256-GCM 加密保存在原数据库同目录，不进入 Obsidian 库、普通备份或导出。
- QA 隔离工作区不注册恋爱路由，也不会打开真实恋爱数据库。

### 知识库

- 「＋ 在 Obsidian 新建」默认在 `03_知识库/00_收件箱` 建笔记，标题、正文、来源、标签与
  「待分流」都在 Obsidian 里写。
- 主题笔记、读书笔记、课程笔记、概念卡片、方法库、输出与作品。
- 收件箱分流与薪火管家的确定性整理建议：建议按目标章节分组并标注置信度，只读展示，
  可逐条「在 Obsidian 打开」原文；移动与改名在 Obsidian 里做。
- 全局搜索 `Ctrl/⌘ K`，↑↓ 选择、回车打开；结果带章节、日期与摘要。

## 体验与帮助

- 顶部「?」直接打开「设置 → 帮助」；帮助含快速上手、数据边界与键盘快捷键。
- 键盘：`Ctrl/⌘ K` 或 `/` 打开全局搜索；`?` 打开快捷键总览；`g` + `w/l/s/o/k/,`
  依次跳转六个主页面，`g h` 打开帮助；`n` 在四库打开「在 Obsidian 新建」、在工作台聚焦新任务输入。
  方向键在顶部标签、设置侧栏、四库章节轨与任务页标签间移动，Home/End 到两端；
  任务页标签用方向键直接切换，且只有当前标签在 Tab 顺序里。
- 工作台无任务/习惯时显示可关闭的「先做一件小事」引导，可直接打开今日日记、
  聚焦任务输入或打开帮助。
- 列表/详情/搜索使用友好日期（今天 16:40 / 昨天 / 10月22日），不显示 ISO 时间串。
- 全局搜索 `Ctrl/⌘ K` 或 `/`；有结果时回车打开第一条，↑↓ 切换；`Esc` 关闭弹窗。
  帮助页提供「键盘快捷键」按钮，与 `?` 打开同一份本地静态总览。
- 小屏顶部标签单行横向滚动并自动定位当前页；设置侧栏同样自动定位。
- 1180px 以下四库单列、章节轨横向，右栏的 Obsidian 快捷入口/最近 7 天/收件箱/身份卡下移保留。
- 空状态带「例如……」示例；加载失败可重试。
- 危险操作分两级：真正不可逆的操作（彻底删除/清空回收站、回滚迁移、清除滴答凭据、
  开启本地删除回写）在确认框里使用危险色按钮；可恢复的删除（任务）保留确认并进回收站。
  确认默认焦点在“取消”。

## 跨库与系统能力

- 库结构与迁移（当前布局 `pillars-v2`）：预览 → 备份 → 执行 → 报告 → 回滚；预览包含旧布局
  目录与人生路 v1 章节目录 → v2 的逐文件搬迁（冲突单独列出，执行要带预览的 `plan_hash`）；
  迁移记录写入 `00_人生路/08_任务与待办/_系统/迁移记录/`。
- 与神对话：完整 DeepSeek Harness Web UI（同源 `/harness/` 代理），嵌在工作台待办事项下方，
  宽度对齐任务主区并空出右侧小组件列；顶部「待办事项 / 与神对话」二级菜单用于页面定位。
  它是对话的唯一 AI 入口。恋爱系统的分析/日记生成是独立功能，使用它自己的可选 DeepSeek Key。
- 薪火管家：确定性的收件箱分流/原则提炼提案与四库健康检查；自由问答在 dsh 里。
  提案与体检都只读（2026-10-26 起没有执行入口），卡片只提供「在 Obsidian 打开原文」。
- 诊断与恢复：数据库完整性、表计数、Vault 文件数、备份复验、恢复副本只读验证、一键恢复演练。
- 导出：记忆、目标与任务、习惯与打卡、对话、每日记录、身体记录的可读 ZIP；不是恢复包。
- 备份：本地明文 ZIP；恢复写入独立目录，不覆盖当前库，不自动切换。
- 滴答同步：拉取清单/任务、本地新增修改删除回写、清单回写、回收站恢复；
  正文/重复规则/提醒/附件暂不回写。
- 审计：只记事件类型与数量，不记正文、问题、标题或路径。

### 设置 · 个人数据库（2026-10-26）

- 设置 →「Obsidian 库连接」→「个人数据库」：显示当前库路径、模板只读预览
  （章节与分组来自 `backend/app/vault_layout.py`，摘要显示 `35 个章节 / layout v2`；设置页只
  展示、不能编辑，也不渲染分组行），以及「创建新的个人数据库」（父文件夹 + 名字）。
- 创建只写一个新文件夹，按 `pillars-v2` 直接落 v2 章节路径：四库章节、`_系统`/`任务`、
  `.torch-os/vault-layout.json`（含 `groups`）、`.obsidian` 预设与 torch 插件、种子身份笔记；
  不碰当前库、不覆盖非空文件夹、不允许建在当前库内部；非个人运行时（QA）两个接口都 409
  （能力标记 `vault_admin` 为 `false`）。
- 新库注册后出现在数据库/工作区列表，行上带 `obsidian://open?vault=<名字>`；切换 = 在
  Obsidian 里打开另一个库（插件每库一个实例，端口从 9168 起）；浏览器模式仍由
  「切换到此工作区」写 `.env`、重启服务后生效。模板与接口见 `docs/VAULT_TEMPLATE.md`。

## 只读工具桥（12 个）

`today`、`memories`、`goals`、`habits`、`journal`、`profile`、`vault`、
`body`、`documents`、`imports`、`vault_audit`、`identity`。

全部是 GET、只在本机回环地址与本地会话下可用、每次调用只写一条计数审计；
写操作（确认记忆、建任务、写日记）不通过这些接口开放。详见 `docs/MCP_BRIDGE.md`。

## 退役与遗留

- 旧「数字分身 / 第二大脑」页面、目标页、记忆审核页、八字页、身体记录页、
  分享导入页、资料导入页、Vault 体检页已退出导航；对应前端文件已在 Phase 5 清理中删除。
- 旧结构化日记 `/api/journal/{day}`、`/api/journal/monthly-review` 等路由已退役；
  `/api/journal/note` 仍是工作台日记接口，日记权威源是 Markdown。
- 2026-10-26 四库改为只读：`POST /api/capture`、`/api/notes/update`、`/api/notes/move`、
  `/api/reviews/weekly`、`/api/reviews/monthly`、生活运营行的增删改与恢复、
  `/api/steward/proposals/apply` 与 `apply-batch`、`/api/journal/note/generate` 已退役（404）。
  插件只保留跳转 Obsidian（`torch:new-note` 宿主桥，浏览器模式退回 `obsidian://new`）；
  完整清单见 `docs/DEPRECATIONS.md`。
- `bazi_profiles`、`suggestions`、`journal_entries`、`journal_actions` 保留为 deprecated
  （0.99 起无写入方），不自动 DROP；日记导出仍可读取历史 `journal_entries`。
- `goals`/`learning`/`body` 旧后端已退役（2026-10-22）：
  目标改读人生路目标笔记，任务导入写入 Markdown 任务笔记，身体汇总/导出改读健康月度表；
  学习分钟、学习工具与学习导出已下线。旧 SQLite 表保留为 deprecated，不自动 DROP。

## 与设计书的偏差

- 设计书 Phase 5 要求退役 `goals.py`、`learning.py`、`body.py`、`journal.py`；
  2026-10-22 已完成最后三项的迁移式退役：目标/任务/身体改读 Markdown，学习能力下线；
  任务导入不再写旧 SQLite `tasks`，而是创建一任务一笔记并写入稳定导入键。
- 设计书删除清单中的 `inbox.js`、`journal.js`、`growth-model.js`、`habit-grid.js`
  仍有活引用（设置页日程、工作台日记、四库习惯/日历），保留；`journal-calendar.js`
  已随旧日记归档月历前端一起删除。
- MCP 工具目录为 12 个：`profile` 是计数快照工具，不是已退役的 profile 页面；
  `learning` 已随旧学习后端退役。
- 设计书第 4 节的插件内捕获、笔记移动、周/月复盘骨架生成与管家确认执行，已按 2026-10-26
  产品决定退役：四库只读，改动统一回到 Obsidian 原生编辑器。
- 已移除路由、deprecated 表与仍保留旧后端的完整清单见 `docs/DEPRECATIONS.md`。
- 当前状态、验证结果与下一步见 `docs/STATUS.md`。
