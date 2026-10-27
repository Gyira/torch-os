# 薪火OS 数据手册（0.99）

这份手册描述当前运行中的 SQLite 结构，以及备份、导出、恢复与「什么会离开本机」的边界。
它由 `GET /api/schema.json` / `GET /api/schema.md` 的实际结构校验，测试会阻止手册与运行库漂移。

## 表清单

| 表 | 分组 |
|---|---|
| `audit_events` | 系统 |
| `bazi_profiles` | 个人参考 |
| `conversations` | 对话 |
| `dida_project_links` | 系统 |
| `dida_sync_settings` | 系统 |
| `dida_task_links` | 系统 |
| `documents` | 资料与记忆 |
| `fortune_checkins` | 每日记录 |
| `goals` | 目标与任务 |
| `habit_definitions` | 习惯 |
| `habit_entries` | 习惯 |
| `harness_sessions` | 对话 |
| `inbox_items` | 每日记录 |
| `journal_actions` | 每日记录 |
| `journal_entries` | 每日记录 |
| `memories` | 资料与记忆 |
| `memory_deletions` | 资料与记忆 |
| `memory_embeddings` | 资料与记忆 |
| `memory_events` | 资料与记忆 |
| `memory_usages` | 资料与记忆 |
| `messages` | 对话 |
| `scheduler_settings` | 每日记录 |
| `schema_migrations` | 系统 |
| `suggestions` | 建议与提醒 |
| `task_import_ledger` | 目标与任务 |
| `tasks` | 目标与任务 |

`documents_fts*` 是全文检索辅助表，不列入用户数据表清单。

## 各表用途（不包含正文）

- 资料与记忆：`documents` 是 Vault 文件索引；`memories` 是人工确认/候选/归档的记忆；
  `memory_events`、`memory_deletions`、`memory_usages` 记录变更、删除墓碑与引用。
  `memory_embeddings` 是旧向量缓存（deprecated，无读写，不自动删除）。
- 对话：`conversations`、`messages` 保留历史对话导出数据；`harness_sessions` 是旧 ACP
  会话引用（deprecated，无写入方）。新的 AI 会话由 DeepSeek Harness 自己管理。
- 目标与任务：`goals`、`tasks`、`task_import_ledger` 是旧 SQLite 结构（deprecated，无写入方）；
  目标和任务以 Markdown 为权威，任务导入用任务笔记的稳定导入键去重。
- 每日记录：`journal_entries`、`journal_actions` 是旧 SQLite 日记与动作（deprecated，
  仅保留历史数据与导出读取）；`inbox_items`、`scheduler_settings` 是收件箱与调度设置；
  `fortune_checkins` 是每日运势签到（只记日期与签到时间，黄历内容本身不落库）。
  身体记录改读健康月度 Markdown 表，旧 `body_entries` 表不再创建。
- 习惯：`habit_definitions`、`habit_entries`；旧学习表已退役且不再创建。
- 个人参考：`bazi_profiles` 是旧八字页面保存的出生资料，deprecated。
- 建议与提醒：`suggestions` 是旧每日建议表，deprecated。
- 旧日记：`journal_entries`、`journal_actions` 自 0.99 起 deprecated；旧写入路由已退役，
  导出接口仍可读取历史记录，表不会自动删除。
- 系统：`audit_events`、`schema_migrations`、`dida_sync_settings`、`dida_task_links`、
  `dida_project_links`。

## 权威源与投影

- 日记、任务、习惯、体重/账目、目标/计划、身份的权威源都是 Markdown；
  SQLite 只做索引、投影、审核状态和统计缓存。
- 四库目录与章节的映射也只有一份来源：`backend/app/vault_layout.py`（当前 `pillars-v2`，
  人生路分组见 `CHAPTER_GROUPS`）；新建库写出的 `.torch-os/vault-layout.json` 只是这份映射的
  快照（章节 + 分组），模板预览、创建与四库页面都读同一个文件。v1 章节目录不在运行时写入，
  只由设置 →「库结构与迁移」按 `V1_CHAPTER_MOVES` 搬到 v2 路径。
- 四库页面只读（2026-10-26 起）：插件只浏览、搜索与跳转；日记、体重/账目月度表和四库笔记的
  增删改都在 Obsidian 里做。工作台任务、习惯打卡、备份与迁移的写入路径不变。
- 结构化月度表（体重、账目）在 Markdown 里每行有稳定 `row_id`；删除投影后可以从
  Markdown 重建。
- 备份是整库 ZIP 快照；恢复副本独立存在，不覆盖当前库。

## 导出

设置页的一键导出 ZIP 包含可读文件：记忆、目标与任务、习惯与打卡、对话、每日记录、
身体记录。它是阅读/迁移用的导出包，不是恢复包；恢复请使用备份与恢复演练。

## 恢复

- 备份生成时校验路径穿越、哈希清单与 SQLite 完整性。
- 恢复副本写入 `data/restored/<32 位十六进制>/`，只读验证数据库完整性、外键与文件清单。
- 恢复演练不会覆盖当前库，也不会自动切换；「把副本切换为当前库」仍需明确确认。

## 什么会离开本机

- torch 的天气卡只访问固定白名单主机；普通资料、任务与四库功能不调用模型。
- 工作台倒计时卡的配置（目标日期、标签、背景图）只保存在当前浏览器的 LocalStorage，
  不写 Vault、不进备份；其中本地图片只由浏览器读取，不上传。
- 所有 AI 对话由本地 DeepSeek Harness 发起；是否联网、用哪个模型、发送哪些上下文，
  都由 dsh 自己的设置与权限决定。
- 恋爱系统的 AI 分析/日记生成是例外，且必须由你点击触发：它使用你在恋爱设置里单独配置的
  DeepSeek Key，发送前脱敏姓名、电话、证件、邮箱、链接与银行卡号；Key 用 AES-256-GCM
  加密保存在原恋爱数据库同目录，不进入 Obsidian 库、普通备份或导出。
- 审计只记事件类型与数量，不记正文、问题、标题或路径。
