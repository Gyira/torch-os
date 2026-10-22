# 薪火OS 功能地图（0.99）

这份文档是「当前仓库实际能做什么」的清单，不是愿景。它与运行中的
`GET /api/config/status`、`GET /api/tools` 和 SQLite 结构保持一致；契约测试
`tests/test_docs_contract.py` 会在能力标记漂移时失败。

`GET /api/config/status` 返回当前版本与能力标记：`agent`、`attachment_localize`、`attachment_plan`、`audit_summary`、`backup_plan`、`body_export`、`body_records`、`body_summary`、`candidate_tidy`、`chat_memory_panel`、`conversation_export`、`conversation_extract`、`diagnostics`、`dida_sync`、`export_bundle`、`growth_export`、`habits`、`harness`、`harness_reset`、`history_cleanup_preview`、`history_restore`、`identity`、`import_history`、`journal_export`、`learning`、`learning_monthly`、`link_audit`、`mcp_bridge`、`memory_batch`、`memory_delete`、`memory_export`、`model_key_write`、`monthly_review`、`planning_export`、`restore_drill`、`restored_copies`、`scheduler`、`scheduler_weekly`、`schema_export`、`semantic_search`、`task_reorder`、`tasks`、`tools`、`vault_audit`、`vault_connection`、`weather`、`workbench`、`workbench_polish`、`workspaces`

## 一级导航

| 视图 | hash | 说明 |
|---|---|---|
| 工作台 | `#workbench` | 问候、横幅、时钟/天气/黄历、时间管理（任务、日历、习惯打卡、四象限、回收站） |
| 人生路 | `#life` | 日记、反思、心得体会、周/月复盘、目标与计划、工作复盘、最近 7 天日记圆点 |
| 自我档案 | `#self` | 身份卡、记忆长河、想法、关系、意义与目标、沉淀与创作 |
| 生活运营 | `#ops` | 健康与体重、财富与记账、体检与医疗、证件与事务 |
| 知识库 | `#knowledge` | 收件箱、主题/读书/课程笔记、概念卡、方法库、输出与作品 |
| 设置 | `#settings` | 模型密钥、备份与恢复、库结构与迁移、诊断、导出、数据手册、工具桥、侧边栏顺序、帮助 |

旧 hash（`#today`、`#identity`、`#profile`、`#memory`、`#bazi`、`#goals`、`#habits`、
`#brain`、`#documents`、`#notes`、`#archive`、`#help`）仍由 `frontend/app.js` 的
`GO_TARGETS` 做一次性重定向，避免旧书签失效。

## 工作台

- 问候语、横幅（座右铭/统计、背景、字体）、时钟、5 日天气、黄历。
- 任务：一任务一笔记（`00-System/任务/**`），看板/清单/标签、日期时间优先级、
  多选批量、右键菜单、手动排序、拖拽换清单、四象限换格、回收站、滴答双向同步。
- 日历：年/月/周/日/日程/多日/多周视图；任务按清单颜色着色，时间任务上时刻轴。
- 习惯打卡：每日必做来自 `00-System/待办（todos）.md`，勾选写入当天日记；
  四库「人生路 → 习惯」提供同一份 Markdown 习惯数据的面板。
- 旧式 `任务（tasks）.md` 复选框任务只读保留。

## 四库

### 人生路

- 与工作台共用 `00_人生路/01_日记/YYYY-MM-DD.md`；「写今日日记」直接打开权威文件。
- 反思、心得体会、周复盘、月复盘骨架；目标与计划、工作复盘。
- 最近 7 天日记圆点；旧目录日记会提示先迁移。
- 习惯定义与打卡镜像在 `00_人生路/06_目标与计划/04_习惯追踪/`。

### 自我档案

- 身份/三观（`01_自我档案/01_内核与三观/身份（identity）.md`，与工作台横幅共用）。
- 记忆与自传、想法与灵感、情感与关系、意义与目标、沉淀与创作。
- 支持从反思/心得提炼原则草稿，并双链回原文。

### 生活运营

- 体重/健康、财务账目使用「月度 Markdown 表 + SQLite 投影」，行有稳定 `row_id`。
- 体检/医疗、证件/事务一次事件一篇笔记。
- 月度表支持行内新增/编辑、趋势 sparkline、分类占比。
- 删除行会先确认；删除后 15 秒内可在右下角撤销，按原 `row_id` 与位置写回原月度表。

### 知识库

- 快速捕获默认只写 `03_知识库/00_收件箱`，带标题、正文、来源、标签与「待分流」。
- 主题笔记、读书笔记、课程笔记、概念卡片、方法库、输出与作品。
- 收件箱手动分流；薪火管家提供确定性整理建议，确认后才移动。
- 全局搜索 `Ctrl/⌘ K`，↑↓ 选择、回车打开；结果带章节、日期与摘要。

## 体验与帮助

- 顶部「?」直接打开「设置 → 帮助」；帮助含快速上手、数据边界与键盘快捷键。
- 键盘：`Ctrl/⌘ K` 或 `/` 打开全局搜索；`?` 打开快捷键总览；`g` + `w/l/s/o/k/,`
  依次跳转六个主页面，`g h` 打开帮助；`n` 在四库打开「记一条」、在工作台聚焦新任务输入。
  方向键在顶部标签、设置侧栏、四库章节轨与任务页标签间移动，Home/End 到两端；
  任务页标签用方向键直接切换，且只有当前标签在 Tab 顺序里。
- 工作台无任务/习惯时显示可关闭的「先做一件小事」引导，可直接打开今日日记、
  聚焦任务输入或打开帮助。
- 列表/详情/搜索使用友好日期（今天 16:40 / 昨天 / 10月22日），不显示 ISO 时间串。
- 全局搜索 `Ctrl/⌘ K` 或 `/`；有结果时回车打开第一条，↑↓ 切换；`Esc` 关闭弹窗。
  帮助页提供「键盘快捷键」按钮，与 `?` 打开同一份本地静态总览。
- 小屏顶部标签单行横向滚动并自动定位当前页；设置侧栏同样自动定位。
- 1180px 以下四库单列、章节轨横向，右栏的快速记录/最近 7 天/收件箱/身份卡下移保留。
- 空状态带「例如……」示例；加载失败可重试。
- 危险操作分两级：真正不可逆的操作（彻底删除/清空回收站、回滚迁移、清除滴答凭据、
  开启本地删除回写）在确认框里使用危险色按钮；可恢复的删除（生活运营行、任务）
  保留确认并尽量提供撤销或回收站。确认默认焦点在“取消”。

## 跨库与系统能力

- 库结构与迁移：预览 → 备份 → 执行 → 报告 → 回滚；迁移记录写入 `00-System/迁移记录/`。
- 薪火管家：收件箱分流/原则提炼提案、四库健康检查、本地优先的带引用问答；
  执行必须 `confirm=true`，审计只记提案 id、动作与路径，不记正文。
- 诊断与恢复：数据库完整性、表计数、Vault 文件数、备份复验、恢复副本只读验证、一键恢复演练。
- 导出：记忆、目标与任务、习惯与学习、对话、每日记录、身体记录的可读 ZIP；不是恢复包。
- 备份：本地明文 ZIP；恢复写入独立目录，不覆盖当前库，不自动切换。
- 滴答同步：拉取清单/任务、本地新增修改删除回写、清单回写、回收站恢复；
  正文/重复规则/提醒/附件暂不回写。
- 审计：只记事件类型与数量，不记正文、问题、标题或路径。

## 只读工具桥（13 个）

`today`、`memories`、`goals`、`habits`、`learning`、`journal`、`profile`、`vault`、
`body`、`documents`、`imports`、`vault_audit`、`identity`。

全部是 GET、只在本机回环地址与本地会话下可用、每次调用只写一条计数审计；
写操作（确认记忆、建任务、写日记）不通过这些接口开放。详见 `docs/MCP_BRIDGE.md`。

## 退役与遗留

- 旧「数字分身 / 第二大脑」页面、目标页、记忆审核页、八字页、身体记录页、
  分享导入页、资料导入页、Vault 体检页已退出导航；对应前端文件已在 Phase 5 清理中删除。
- 旧结构化日记 `/api/journal/{day}`、`/api/journal/monthly-review` 等路由已退役；
  `/api/journal/note` 仍是工作台日记接口，日记权威源是 Markdown。
- `bazi_profiles`、`suggestions`、`journal_entries`、`journal_actions` 保留为 deprecated
  （0.99 起无写入方），不自动 DROP；日记导出仍可读取历史 `journal_entries`。
- `goals`/`learning`/`body` 后端仍服务工作台、任务导入与导出。

## 与设计书的偏差

- 设计书 Phase 5 原本要求退役 `goals.py`、`learning.py`、`body.py`、`journal.py`；
  本轮保守处理：`journal.py` 已退役，`goals`/`learning`/`body` 仍被任务导入、
  工作台学习分钟与导出使用，因此保留；另已退役 bazi/profile/suggestions/share_import。
- 设计书删除清单中的 `inbox.js`、`journal.js`、`growth-model.js`、`habit-grid.js`
  仍有活引用（设置页日程、工作台日记、四库习惯/日历），保留；`journal-calendar.js`
  已随旧日记归档月历前端一起删除。
- MCP 工具目录仍为 13 个：`profile` 是计数快照工具，不是已退役的 profile 页面。
- 已移除路由、deprecated 表与仍保留旧后端的完整清单见 `docs/DEPRECATIONS.md`。
- 当前状态、验证结果与下一步见 `docs/STATUS.md`。
