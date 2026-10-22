# 退役与兼容（0.99）

这份清单记录 0.99 清理后**已经移除**、**保留但不再写入**、以及**仍然保留**的旧接口和表。
原则：不删除用户文件，不自动 DROP SQLite 表；旧数据保留，新写入走 Markdown 或四库页面。

## 已移除的 HTTP 路由

- `/api/bazi/*`：旧八字页面。
- `/api/profile`、`/api/profile/activity`、`/api/chat/preview`、`/api/memories/{id}/usage`：
  旧「认识我」页面与预览接口。
- `/api/suggestions/*`：旧建议卡。
- `/api/imports/share/*`：DeepSeek 分享导入。
- `/api/journal/{day}`、`/api/journal/{day}/review`、`/api/journal/{day}/next-task`、
  `/api/journal/{day}/export`、`/api/journal/monthly-review`：旧 SQLite 结构化日记。

仍在使用的相邻接口：

- `/api/journal/note`：工作台日记（Markdown 权威文件）。
- `/api/reviews/weekly`、`/api/reviews/monthly`：四库复盘骨架。
- `/api/export/journal.json|md`：历史日记导出（只读）。
- `/api/time/*`：工作台任务（Markdown 一任务一笔记）。
- `/api/goals`：任务导入的目标选择与计数工具仍在使用。

## 保留但 deprecated 的 SQLite 表

这些表自 0.99 起没有写入方，保留历史数据，**不会自动删除**；如确认不再需要，请手动备份后
由用户决定是否迁移/清理。

- `bazi_profiles`：旧八字资料。
- `suggestions`：旧建议卡。
- `journal_entries`、`journal_actions`：旧 SQLite 日记与动作；导出接口仍可读取历史记录。

## 仍保留的旧后端

以下模块仍有活跃用途，本轮没有删除：

- `goals.py`：任务导入的目标关联、导出与只读工具。
- `learning.py`：工作台「学习分钟」、调度草稿、导出与只读工具。
- `body.py`：工作台/调度中的身体记录、导出与只读工具。

对应的旧页面已经下线；这些能力现在只通过工作台、导出包或只读工具使用。

## 兼容说明

- 旧 hash（`#today`、`#identity`、`#profile`、`#memory`、`#bazi`、`#goals`、`#habits`、
  `#brain`、`#documents`、`#notes`、`#archive`、`#help`）仍会重定向到新页面。
- 旧书签/外部客户端调用已移除路由会得到 404；请改用上表中的相邻接口或 Markdown 文件。
- 备份、恢复副本与导出包仍包含历史数据；迁移或清理表前先做一次已验证备份。
