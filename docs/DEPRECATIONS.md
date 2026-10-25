# 退役与兼容（0.99）

这份清单记录 0.99 清理后**已经移除**、**保留但不再写入**、以及**仍然保留**的旧接口和表。
原则：不删除用户文件，不自动 DROP SQLite 表；旧数据保留，新写入走 Markdown（工作台或 Obsidian）。

## 已移除的 HTTP 路由

- `/api/bazi/*`：旧八字页面。
- `/api/profile`、`/api/profile/activity`、`/api/chat/preview`、`/api/memories/{id}/usage`：
  旧「认识我」页面与预览接口。
- `/api/suggestions/*`：旧建议卡。
- `/api/imports/share/*`：DeepSeek 分享导入。
- `/api/journal/{day}`、`/api/journal/{day}/review`、`/api/journal/{day}/next-task`、
  `/api/journal/{day}/export`、`/api/journal/monthly-review`：旧 SQLite 结构化日记。
- `/api/learning/*`：旧学习科目、学习记录与周统计。
- `/api/body/*`：旧 SQLite 身体记录；身体数据现在只读「健康与体重」月度 Markdown 表。
- `/api/goals` 的写接口与 `/api/tasks/*` 旧接口：目标写转人生路目标笔记，任务只用
  `00_人生路/08_任务与待办/任务/**` 的 Markdown 笔记（`GET /api/goals` 保留，返回目标笔记）。
- `/api/daily-plan/draft`：旧目标任务的今日计划草稿；时间管理页已替代。
- `/api/chat`、`/api/chat/stream`、`/api/agent/cancel`：旧 torch 自建对话与 ACP 会话。
- `/api/config/model-key`：旧模型密钥写入接口；torch 不再保存模型凭据。
- `/api/steward/ask` 与 `/api/steward/status`：旧管家问答；问答统一交给 dsh。
- `/api/workbench/summarize`：旧模型润色接口；`/api/workbench/draft` 仍保留。
- `/api/conversations`、`/api/conversations/{id}`：旧对话读取；历史数据仍可通过导出接口读取。
- **2026-10-26 四库只读**（手动写入改为在 Obsidian 原生编辑器里做）：
  - `POST /api/capture`：旧快速捕获对话框。
  - `POST /api/notes/update`、`POST /api/notes/move`：笔记标签/状态更新与章节移动。
  - `POST /api/reviews/weekly`、`POST /api/reviews/monthly`：周/月复盘骨架生成。
  - `POST /api/ops/{dataset}/months/{month}/rows`、`POST /api/ops/{dataset}/months/{month}/rows/{row_id}`、
    `DELETE /api/ops/{dataset}/months/{month}/rows/{row_id}`、
    `POST /api/ops/{dataset}/months/{month}/rows/{row_id}/restore`：生活运营月度行的新增、更新、删除与撤销。
  - `POST /api/steward/proposals/apply`、`POST /api/steward/proposals/apply-batch`：管家提案执行。
  - `POST /api/journal/note/generate`：日记骨架生成与追加。

  以上接口现在都返回 404。界面对应按钮改为跳转 Obsidian：「＋ 在 Obsidian 新建」、
  「在 Obsidian 打开并编辑」、「在 Obsidian 打开月度表」、「打开本周复盘 / 打开本月复盘」、
  「在 Obsidian 打开习惯文件」、「在 Obsidian 打开原文」。插件内用 `torch:new-note` /
  `torch:open-note` 宿主桥，浏览器模式退回 `obsidian://new` / `obsidian://open`；
  工作台的任务、日历、习惯打卡、四象限、滴答同步、备份与迁移不受影响。

仍在使用的相邻接口：

- `/api/journal/note`：工作台日记读取（Markdown 权威文件；生成/追加写入已退役）。
- `/api/export/journal.json|md`：历史日记导出（只读）。
- `/api/time/*`：工作台任务（Markdown 一任务一笔记）。
- `/api/goals`：任务导入的目标选择与计数工具仍在使用。

## 保留但 deprecated 的 SQLite 表

这些表自 0.99 起没有写入方，保留历史数据，**不会自动删除**；如确认不再需要，请手动备份后
由用户决定是否迁移/清理。

- `bazi_profiles`：旧八字资料。
- `suggestions`：旧建议卡。
- `journal_entries`、`journal_actions`：旧 SQLite 日记与动作；导出接口仍可读取历史记录。
- `goals`、`tasks`：旧 SQLite 目标与任务；2026-10-22 起无写入方，任务导入改写 Markdown。
- `study_subjects`、`study_sessions`：旧学习科目与记录；学习能力已下线。
- `body_entries`：旧身体记录；汇总与导出改读健康月度 Markdown 表。
- `task_import_ledger`：旧任务导入账本；新导入用任务笔记 frontmatter 的稳定导入键去重。
- `conversations`、`messages`：旧 torch 对话历史；只读保留，供导出/迁移。
- `harness_sessions`：旧 ACP 会话引用；无写入方。
- `memory_embeddings`：旧向量缓存；检索已改为纯词法，无读写。

## 仍保留的旧后端

`goals.py`、`learning.py`、`body.py` 已在 2026-10-22 退役并删除。
目标、任务与身体数据现在分别来自人生路目标笔记、Markdown 任务笔记与健康月度表；
学习分钟、学习工具与学习导出已下线。

2026-10-25 起，`model_gateway.py`、`agent.py`、`harness_brain.py`、`embedding.py`
与 `agent/dsh-torch-live` 已删除；唯一 AI 入口是内嵌的 DeepSeek Harness。
环境变量 `MODEL_*`、`EMBEDDING_PROVIDER`、`OLLAMA_*` 不再读取；dsh 使用
`TORCH_DSH_COMMAND`、`TORCH_DSH_WORKSPACE` 与自己的 `DSH_HOME`。

## 兼容说明

- 旧 hash（`#today`、`#identity`、`#profile`、`#memory`、`#bazi`、`#goals`、`#habits`、
  `#brain`、`#documents`、`#notes`、`#archive`、`#help`）仍会重定向到新页面。
- 旧书签/外部客户端调用已移除路由会得到 404；请改用上表中的相邻接口或 Markdown 文件。
- 2026-10-26 起四库编辑统一在 Obsidian：插件只读浏览与跳转，写接口返回 404；
  外部客户端请直接改 Vault 里的 Markdown，工作台操作仍走 `/api/time/*` 等既有接口。
- 备份、恢复副本与导出包仍包含历史数据；迁移或清理表前先做一次已验证备份。
