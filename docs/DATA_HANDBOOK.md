# 薪火OS 数据手册（0.99）

这份手册描述当前运行中的 SQLite 结构，以及备份、导出、恢复与「什么会离开本机」的边界。
它由 `GET /api/schema.json` / `GET /api/schema.md` 的实际结构校验，测试会阻止手册与运行库漂移。

## 表清单

| 表 | 分组 |
|---|---|
| `audit_events` | 系统 |
| `bazi_profiles` | 个人参考 |
| `body_entries` | 每日记录 |
| `conversations` | 对话 |
| `dida_project_links` | 系统 |
| `dida_sync_settings` | 系统 |
| `dida_task_links` | 系统 |
| `documents` | 资料与记忆 |
| `goals` | 目标与任务 |
| `habit_definitions` | 习惯与学习 |
| `habit_entries` | 习惯与学习 |
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
| `study_sessions` | 习惯与学习 |
| `study_subjects` | 习惯与学习 |
| `suggestions` | 建议与提醒 |
| `task_import_ledger` | 目标与任务 |
| `tasks` | 目标与任务 |

`documents_fts*` 是全文检索辅助表，不列入用户数据表清单。

## 各表用途（不包含正文）

- 资料与记忆：`documents` 是 Vault 文件索引；`memories` 是人工确认/候选/归档的记忆；
  `memory_events`、`memory_deletions`、`memory_usages`、`memory_embeddings` 记录变更、
  删除墓碑、引用与向量。
- 对话：`conversations`、`messages`、`harness_sessions` 保存对话与会话引用；
  正文是否保存取决于调用端与设置。
- 目标与任务：`goals`、`tasks`、`task_import_ledger` 服务工作台任务、目标映射与导入账本。
- 每日记录：`journal_entries`、`journal_actions`、`body_entries`、`inbox_items`、
  `scheduler_settings` 是旧每日记录投影、动作、身体记录、收件箱与调度设置。
- 习惯与学习：`habit_definitions`、`habit_entries`、`study_subjects`、`study_sessions`。
- 个人参考：`bazi_profiles` 是旧八字页面保存的出生资料，deprecated。
- 建议与提醒：`suggestions` 是旧每日建议表，deprecated。
- 系统：`audit_events`、`schema_migrations`、`dida_sync_settings`、`dida_task_links`、
  `dida_project_links`。

## 权威源与投影

- 日记、任务、习惯、体重/账目、目标/计划、身份的权威源都是 Markdown；
  SQLite 只做索引、投影、审核状态和统计缓存。
- 结构化月度表（体重、账目）在 Markdown 里每行有稳定 `row_id`；删除投影后可以从
  Markdown 重建。
- 备份是整库 ZIP 快照；恢复副本独立存在，不覆盖当前库。

## 导出

设置页的一键导出 ZIP 包含可读文件：记忆、目标与任务、习惯与学习、对话、每日记录、
身体记录。它是阅读/迁移用的导出包，不是恢复包；恢复请使用备份与恢复演练。

## 恢复

- 备份生成时校验路径穿越、哈希清单与 SQLite 完整性。
- 恢复副本写入 `data/restored/<32 位十六进制>/`，只读验证数据库完整性、外键与文件清单。
- 恢复演练不会覆盖当前库，也不会自动切换；「把副本切换为当前库」仍需明确确认。

## 什么会离开本机

- 默认不联网；天气查询只访问固定白名单主机。
- 只有显式配置模型并主动发起请求时，才会把当前问题与经确认的相关片段发送给模型；
  模型 Key 只写入本地 `.env`。
- 审计只记事件类型与数量，不记正文、问题、标题或路径。
