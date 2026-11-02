# 薪火OS 数据手册（1.0）

这份手册描述当前运行中的 SQLite 结构，以及备份、导出、恢复与「什么会离开本机」的边界。
它由 `GET /api/schema.json` / `GET /api/schema.md` 的实际结构校验，测试会阻止手册与运行库漂移。

## 文件放哪里（顶层规则，2026-10-27）

一个个人数据库（Vault）里只有两类东西：

1. **Markdown 正文 / 用户内容**放在四库：`00_人生路`、`01_自我档案`、`02_生活运营`、
   `03_知识库`。这些是用户可在 Obsidian 里直接读改的权威源。
2. **系统数据、应用配置、小组件个性化数据、缓存、非 Markdown 资源**放在库根
   `<Vault>/.torch-os/`。主要文件：

| 路径 | 内容 |
|---|---|
| `.torch-os/torch_os.sqlite3` | 索引、记忆、审计、签到、调度设置；v1.0 角色会话/轮次/提案与忆灵运行记录 |
| `.torch-os/personal-data.json` | 小组件偏好、常用城市、导航/小组件排序、时间视图等个性化键值 |
| `.torch-os/忆灵/` | **旧版忆灵**画像、画像历史、复盘卡、事件状态；2026-11-01 起只作历史兼容，新界面不调用 |
| `01_自我档案/01_档案/` | 自我档案 v2：`00_画像.md`、`身份（identity）.md` 与 11 个固定本子（每个条目一篇 Markdown） |
| `01_自我档案/02_人生导师/` | 分层记忆（核心/时间线/问答）、待确认补丁、每日三问、旧忆灵历史；全部 Markdown |
| `01_自我档案/03_八字系统/` | `命盘.md`、`解读/*.md`、`历史/`；排盘与解读都在本机 |
| `.torch-os/ai-runtime/` | 角色子进程的空工作区与临时文件；不是 Vault，不承载用户资料 |
| `.torch-os/横幅背景/` | 工作台横幅图片；编辑器选图后由后端复制进来，路径写回身份笔记 |
| `.torch-os/train-tracker/` | 独立列车追踪组件的 `timetable.json`（可导入或手动从 12306 同步） |
| `02_生活运营/01_恋爱系统/01_恋爱记忆/` | 恋爱记忆 Markdown 本子：`恋爱日志/`（默认）、`AI 草稿/`、自定义本子 |
| `02_生活运营/01_恋爱系统/02_微信聊天/` | `chat.db`、加密 WeFlow Token（`.credentials.json` 与 `.credential.key`）、`media/`、`exports/` |
| `02_生活运营/01_恋爱系统/03_排班日历/` | `xlsx/`、`schedule-notes.json`、`overrides.json` |
| `02_生活运营/01_恋爱系统/04_经期关怀/` | `cycle.db`（首次保存时创建） |
| `02_生活运营/01_恋爱系统/05_恋爱相册/` | `albums.db`、`照片/`、`视频/`、`thumbnails/` |
| `02_生活运营/01_恋爱系统/06_八字合婚/` | `bazi.db` |
| `02_生活运营/01_恋爱系统/07_恋爱大师/` | `analysis.db` 与生成结果 |
| `02_生活运营/01_恋爱系统/_归档/` | 旧 `.torch-os/love` 备份、换绑备份、其他聊天项目索引 |
| `.torch-os/love/` | **旧版**恋爱数据目录；v2 首次启动迁移到上面七个目录，原文件保留，不再作为运行时权威 |
| `.torch-os/vault-layout.json` | 建库时的四库/章节布局快照 |
| `.torch-os/backups/`、`logs/`、`diagnostics/`、`migrations/`、`restored/` | 备份、日志、诊断与迁移记录 |
| `02_生活运营/00_待办事项/回收站/` | 任务与清单文件夹的软删除恢复区 |
| `.torch-os/dida-credentials.json` | 滴答清单登录凭据（敏感） |

`personal-data.json` 的格式是 `{"version":1,"updated_at":"…","values":{…}}`，值统一为字符串，
`null` 表示删除；接口 `GET/PUT /api/personal-data` 只接受 `torch-os:`、`torch:`、`torch.`
命名空间的键，单值 ≤2MB（能容纳倒计时/纪念日的本机图片 data URL）、合计 ≤8MB。浏览器
localStorage 只是当前窗口缓存：`frontend/personal-data.js` 在启动时把库里的值灌进去，
之后 `setItem/removeItem` 防抖镜像回后端。缓存里有一个非 torch 命名空间的
`__torch_personal_owner` 标记当前 Vault；打开另一个个人数据库时会先清掉上一个库的缓存再灌入
新库的值。升级后的第一次启动还没有归属标记：本机旧值优先，当时 localStorage 里的个性化键会迁入当次打开的库，
库里缺的键再补进来；之后以库为准。多库用户建议先打开主库，之后每个库各自维护缓存。
天气的 30 分钟缓存（`torch-os:weather`）不写回。

`.torch-os` 不参与普通 Vault 扫描、全文索引和 Markdown 导出；ZIP 备份快照包含其中的
SQLite（`ai_threads`/`ai_turns`/`ai_proposals`/`memory_hub_*` 等表随库一起备份），
但不包含其余 `.torch-os` 文件（忆灵画像 JSON、身份备份、小组件偏好、恋爱库/Key）。
复制整个个人数据库文件夹时会一起带走，所以换机仍能保留这些数据。迁移旧数据时只复制、
不删除原文件。

例外：`.env` 只放启动引导与密钥（`DATA_DIR`/`VAULT_DIR`/端口/运行开关，以及历史遗留的
`LOVE_TARGET_NAME` 这类启动配置）；`.obsidian/` 是 Obsidian 自己要求的插件/主题/工作区配置
（含 `.obsidian/plugins/torch/data.json`）；DSH 的模型与凭据在它自己的 `DSH_HOME`。torch 自己的
业务数据与小组件个性化数据仍以 `.torch-os` 为准，**新增**数据不要再放进这些位置。

## 表清单

| 表 | 分组 |
|---|---|
| `ai_proposals` | 建议与提醒 |
| `ai_threads` | 对话 |
| `ai_turns` | 对话 |
| `audit_events` | 系统 |
| `bazi_profiles` | 个人参考 |
| `conversations` | 对话 |
| `dida_project_links` | 系统 |
| `dida_sync_settings` | 系统 |
| `dida_task_links` | 系统 |
| `documents` | 资料与记忆 |
| `fortune_checkins` | 每日记录 |
| `goals` | 目标与任务 |
| `habit_cache_meta` | 习惯 |
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
| `memory_hub_events` | 资料与记忆 |
| `memory_hub_runs` | 资料与记忆 |
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
  `memory_events`、`memory_deletions`、`memory_usages` 记录变更、删除墓碑与引用；
  `memory_hub_events` 收集角色投递的“值得记住的事”，`memory_hub_runs` 记录每次忆灵综合。
  `memory_embeddings` 是旧向量缓存（deprecated，无读写，不自动删除）。
- 对话：`conversations`、`messages` 保留历史对话导出数据；`harness_sessions` 是旧 ACP
  会话引用（deprecated，无写入方）。`ai_threads`/`ai_turns` 是 v1.0 角色抽屉的会话与轮次；
  「与神对话」主对话仍由 DeepSeek Harness 自己管理。
- 建议与提醒：`suggestions` 是旧提醒表；`ai_proposals` 是 AI 角色提出的待确认动作
  （身份补丁/新笔记/新任务/新习惯），只有用户采纳后才会落盘。
- 目标与任务：`goals`、`tasks`、`task_import_ledger` 是旧 SQLite 结构（deprecated，无写入方）；
  目标和任务以 Markdown 为权威，任务导入用任务笔记的稳定导入键去重。
- 每日记录：`journal_entries`、`journal_actions` 是旧 SQLite 日记与动作（deprecated，
  仅保留历史数据与导出读取）；`inbox_items`、`scheduler_settings` 是收件箱与调度设置；
  `fortune_checkins` 是每日运势签到（只记日期与签到时间，黄历内容本身不落库）。
  身体记录改读健康系统的每日 Markdown（旧库回退到健康月度表），旧 `body_entries` 表不再创建；
  健康数据本身不进 SQLite，见 `docs/HEALTH_SYSTEM.md`。
- 习惯：`habit_definitions`、`habit_entries` 是 Markdown 的派生缓存（整数 id、同步版本号、
  统计用），不是权威源；`habit_cache_meta` 记录缓存实例、generation 与首次迁移时间。
  权威内容在 `00_人生路/04_目标/04_习惯追踪/`：`习惯/<名称>.md` 是定义，
  `打卡/YYYY-MM.md` 是月度打卡表，`习惯定义.md` 是自动生成的只读索引。
  在 Obsidian 里修改笔记或打卡表，应用会在下一次读取时同步回缓存。旧学习表已退役且不再创建。
- 个人参考：`bazi_profiles` 是旧八字页面保存的出生资料，deprecated。
- 建议与提醒：`suggestions` 是旧每日建议表，deprecated。
- 旧日记：`journal_entries`、`journal_actions` 自 0.99 起 deprecated；旧写入路由已退役，
  导出接口仍可读取历史记录，表不会自动删除。
- 系统：`audit_events`、`schema_migrations`、`dida_sync_settings`、`dida_task_links`、
  `dida_project_links`。

## 权威源与投影

- 日记、任务、习惯、体重/账目、目标/计划、身份、每日情感记忆的权威源都是 Markdown；
  SQLite 只做索引、投影、审核状态和统计缓存。习惯在 SQLite 里的表可以随时从
  `习惯/` + `打卡/` 重建；`habit_cache_meta` 只为版本号与迁移标记服务。
- 恋爱系统的权威源按页面分开：恋爱记忆是
  `02_生活运营/01_恋爱系统/01_恋爱记忆/` 下的 Markdown；微信原文是
  `02_微信聊天/chat.db`；排班是 `xlsx/` + `overrides.json`；经期是 `cycle.db`；
  相册是 `albums.db` + 原文件；八字是 `bazi.db`；大师结果是 `analysis.db`。
  旧的 `.torch-os/love/love_analysis.db` 只在迁移时读取。
- 四库目录与章节的映射也只有一份来源：`backend/app/vault_layout.py`（当前 `pillars-v2`，
  人生路分组见 `CHAPTER_GROUPS`）；新建库写出的 `.torch-os/vault-layout.json` 只是这份映射的
  快照（章节 + 分组），模板预览、创建与四库页面都读同一个文件。v1 章节目录不在运行时写入，
  只由设置 →「库结构与迁移」按 `V1_CHAPTER_MOVES` 搬到 v2 路径。
- 四库页面只读（2026-10-26 起）：插件只浏览、搜索与跳转；日记、体重/账目月度表和四库笔记的
  增删改都在 Obsidian 里做。工作台任务、习惯打卡、备份与迁移的写入路径不变。
  恋爱记忆是独立写作域：`/api/writing/notes` 支持 `love_journal`、`love_drafts` 与
  `custom:love:<名字>`；AI 补日记先写「AI 草稿」，不会直接改四库其他章节。
- 结构化月度表（体重、账目）在 Markdown 里每行有稳定 `row_id`；删除投影后可以从
  Markdown 重建。
- 本地备份是 Markdown + 主库快照 ZIP，恢复副本独立存在，不覆盖当前库。完整库
  （含微信聊天、恋爱系统数据库/媒体、相册和 `.torch-os`）请用「设置 → 备份与恢复 →
  云端完整备份」；它是单向快照，不是实时双向同步。

## 导出

设置页的一键导出 ZIP 包含可读文件：记忆、目标与任务、习惯与打卡、旧对话、
AI 角色会话与提案、每日记录、身体记录。它是阅读/迁移用的导出包，不是恢复包；
恢复请使用备份与恢复演练。角色会话里的 dsh 会话 ID 与 verdict token 不进入导出。

## 恢复

- 备份生成时校验路径穿越、哈希清单与 SQLite 完整性。
- 恢复副本写入 `data/restored/<32 位十六进制>/`，只读验证数据库完整性、外键与文件清单。
- 恢复演练不会覆盖当前库，也不会自动切换；「把副本切换为当前库」仍需明确确认。

## 什么会离开本机

- torch 的天气卡只访问固定白名单主机（Open-Meteo 预报/城市搜索与 IP 定位）；城市搜索只在
  你主动输入「添加常用城市」时发生。普通资料、任务与四库功能不调用模型。
- 工作台倒计时卡的配置（目标日期、标签、背景图）写入当前个人数据库的
  `.torch-os/personal-data.json`（localStorage 只是缓存），不写 Markdown；其中本地图片以
  data URL 存在同一个文件里，不上传。
- 纪念日卡的配置同样写入 `.torch-os/personal-data.json`；背景若是 https 图片 URL，浏览器会
  直接向该地址请求图片，请只填你信任的地址；选择本机图片时只由浏览器读取，不上传。
- 所有 AI 对话由本地 DeepSeek Harness 发起；是否联网、用哪个模型、发送哪些上下文，
  都由 dsh 自己的设置与权限决定。
- 云端完整备份默认加密后再写进你选择的网盘同步文件夹；一旦网盘客户端上传，备份文件
  就离开了这台电脑。密码不会明文落盘，但**忘记密码无法恢复**；关闭加密则任何能访问
  网盘文件的人都能看到完整私人数据。恢复步骤见 `docs/CLOUD_BACKUP.md`。
- 恋爱系统的全部私人数据在 `02_生活运营/01_恋爱系统/` 的七个页面目录；旧
  `.torch-os/love/` 首次启动迁移并保留原文件。经期关怀的 `cycle.db` 只在本机计算周期、
  排卵与易孕期，不接外部服务；预测不是诊断，也不能作为避孕依据。恋爱大师只发送
  阶段/预计日期等聚合信息，不发送每日症状明细。
- 微信聊天只访问本机 `http://127.0.0.1:<WeFlow端口>`（默认 5031），不访问 WeFlow 云端或
  其他主机；WeFlow 自己负责读取本机微信数据库。torch 只拉取你选中的一个单聊，Token 加密
  保存在 `02_微信聊天/.credentials.json`（Windows 用 DPAPI 主副本，同时用同目录
  `.credential.key` 保存可移植副本，个人数据库换机/走网盘后仍能读取），不回显、不写日志；
  群聊、公众号和其他聊天不会被读取。媒体复制到 `02_微信聊天/media/`；
  换绑会先备份 `chat.db` 到 `_归档/` 再清空。
- 恋爱大师的所有 AI 都走 dsh；画像/深度分析/报告/角色层使用脱敏后的聊天摘要
  （姓名/电话/证件/邮箱/链接/银行卡号隐藏）。**AI 批量补日记例外**：为保留真实称呼与
  当天细节，后端按天把聊天原文（不脱敏）直接交给 `journal_batch`；范围可选最近
  10/30/60 天或全部未补日期。照片、完整经期明细和原始数据库仍不发送。AI 结果存在
  `07_恋爱大师/analysis.db`，补日记草稿写在 `01_恋爱记忆/AI 草稿/`。
- 列车追踪组件只在用户点击「从 12306 同步」时访问 `kyfw.12306.cn` 官方接口，取一次车次经停站；
  不做后台轮询，也不上传本地时刻表。失败时保留 `.torch-os/train-tracker/timetable.json`。
  该组件与恋爱系统互不读取：恋爱系统只保留上班/休息日历、排班表里的当天值乘车次
  和用户自己写的备注。
  同步流程是：`leftTicket/init` 取会话 Cookie → 用起止站和日期查 `leftTicket/query*`
  （入口会自动轮换）取车次内部编号 → `czxx/queryByTrainNo` 取经停站。只允许
  `kyfw.12306.cn` 一个主机，15 秒超时，无后台重试；官方接口改版或限流时会返回可读错误，
  本地时刻表不受影响。没有接入任何第三方车次 API，也没有保存 12306 账号。
- 恋爱系统的 AI 分析/日记生成是例外，且必须由你点击触发：它使用你在恋爱设置里单独配置的
  DeepSeek Key，发送前脱敏姓名、电话、证件、邮箱、链接与银行卡号；Key 用 AES-256-GCM
  加密保存在同一目录，不上传。
- 审计只记事件类型与数量，不记正文、问题、标题或路径。
