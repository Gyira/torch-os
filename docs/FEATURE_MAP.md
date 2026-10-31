# 薪火OS 功能地图（1.0）

这份文档是「当前仓库实际能做什么」的清单，不是愿景。它与运行中的
`GET /api/config/status`、`GET /api/tools` 和 SQLite 结构保持一致；契约测试
`tests/test_docs_contract.py` 会在能力标记漂移时失败。

`GET /api/config/status` 返回当前版本与能力标记：`ai_roles`、`attachment_localize`、`attachment_plan`、`audit_summary`、`backup_plan`、`candidate_tidy`、`conversation_export`、`conversation_extract`、`deepseek_balance`、`diagnostics`、`dida_sync`、`dsh_web`、`export_bundle`、`growth_export`、`habits`、`health`、`history_cleanup_preview`、`history_restore`、`identity`、`import_history`、`journal_export`、`life_assistant`、`life_reports`、`link_audit`、`love`、`love_cycle`、`love_memory`、`love_schedule`、`mcp_bridge`、`memory_batch`、`memory_delete`、`memory_export`、`memory_hub`、`monthly_review`、`note_editor`、`personal_data`、`planning_export`、`proposals`、`restore_drill`、`restored_copies`、`scheduler`、`scheduler_weekly`、`schema_export`、`share_link`、`task_reorder`、`tasks`、`tools`、`train_tracker`、`vault_admin`、`vault_audit`、`vault_connection`、`weather`、`weflow_sync`、`widget_visibility`、`workbench`、`workspaces`

## AI 角色层（1.0 P0–P1）

- `GET /api/ai/status`：角色层是否可用、dsh 命令类型、Key 来源（不返回 Key）、角色数；
  QA/禁用 dsh 时返回 `available=false`，不启动子进程。
- `GET /api/ai/roles`：读取 `dsh/roles/*.md` 的角色元数据（id/name/modes/proposals），
  不返回 prompt 正文；非法角色只出现在 `role_errors`，不会被使用。
- 当前已加载 7 个角色：忆灵/苏格拉底、写作伙伴、学术导师、生活管家、恋爱大师、
  私人医生、理财专家。角色 = 同一 dsh 内核 + 角色卡 + 后端拼装的只读上下文，
  不是第二个模型或第二个 Key。身份映射：人生路→写作伙伴、知识库→学术导师、
  生活运营→生活管家、恋爱系统→恋爱大师。
- `POST /api/ai/chat`：页面内角色抽屉的一轮对话；`thread_id` 续聊，`selectors` 只接受
  identity/note/journal/tasks/health/finance/love 等枚举来源，后端读取、编号、截断。
  新增 `selection_text` / `selection_source` / `draft_text`：编辑器/阅读器选区与未保存草稿
  作为「数据不是指令」区块进入 prompt，不写入 `ai_turns.content`，审计只记来源/字数/短哈希。
- `POST /api/ai/run`：一次性能力（润色/判类/分享总结/报告的底座），返回 `text` 与
  `structured` JSON；解析失败只保留自然语言，不产生提案。
- `GET/POST /api/ai/threads`、`GET/DELETE /api/ai/threads/{id}`：角色会话；删除需要
  `confirm=true`。
- `GET /api/proposals`、`GET /api/proposals/{id}`、`POST /api/proposals/{id}/apply|reject`：
  通用提案状态机；P1–P3 已注册 `new_task`/`new_habit`/`new_note`/`archive_note`/
  `identity_patch` 落盘 handler；采纳任务会在 `02_生活运营/00_待办事项/` 生成任务笔记；
  未注册的 kind 返回 409。
- 角色子进程通过 stdin 运行 `dsh --profile headless --json -`，空工作区、环境白名单、
  超时杀进程树；详见 `docs/AI_ROLES.md`。
- 前端：工作台时间管理顶部有「生活管家」按钮，打开 `life_assistant/tasks` 角色抽屉；
  提案卡片采纳后生成任务/习惯。小组件显隐在设置 →「工作台小组件」，隐藏组件不初始化、
  不发请求，偏好存 `.torch-os/personal-data.json`。
- 编辑器内打开伙伴不再关闭编辑器：角色抽屉可宿主在编辑器 dialog 右侧，也可收起为底部
  悬浮输入条；选中文字后出现「问…」浮钮，选区以 chip 形式引用，消息气泡不展示原文。
- 未注册的提案 apply 返回 409，不会静默成功。

## 生活运营 AI（1.0 P4）

- `GET /api/ops/ai/overview`、`POST /api/ops/ai/report` 仍保留（健康/财务/恋爱/生活汇总）；
  当前前端不再显示旧的 AI 卡片，生活运营首页改由「生活管家」角色读取
  tasks/identity/health/finance/love 摘要。
- 报告落点不变：健康 `02_生活运营/02_健康系统/00_健康报告`、财务 `07_财务报告`、
  恋爱/生活建议 `08_生活建议`；
  报告 `memory: false`、`generated_by` 标记；不自动改月度表/任务/习惯。
- 恋爱系统 13 页保持（2026-10-31 新增「经期关怀」）；顶部新增「恋爱大师」按钮，只读分析摘要，不读原始聊天。详见 `docs/LIFE_SYSTEMS.md`。

## 忆灵 / 记忆中枢（1.0 P3）

- `GET /api/memory-hub/overview`：规范画像、待审复盘卡、待审身份补丁、观察快照、
  记忆候选与统计；`GET /portrait`、`GET /portrait/history`。
- `GET/POST /api/memory-hub/events`：角色/主 dsh/用户投递的“值得记住的事”，
  按 kind|summary|source|日期去重；主 dsh 用 `scripts/memory_event.py` 投递。
- `POST /api/memory-hub/synthesize`（confirm=true）：读取身份、上一版画像、近 7/14/30 天
  日记、未完成任务、已确认记忆、新事件与旧提问队列；调用忆灵 `synthesize`，写
  `.torch-os/忆灵/画像.json`（带 revision 与历史）、`01_自我档案/08_忆灵观察` 快照、
  复盘卡与 `identity_patch` 提案；事件标记 absorbed。
- `GET/POST /api/memory-hub/cards…`：待审卡列表、留下（写进忆灵观察）、忽略。
- 身份两条写入路径：忆灵审核的补丁直接采纳；苏格拉底的补丁必须带 verdict token。
  `apply_identity_patch` 校验 `base_revision`、写前备份、审计不记内容。
- 工作台旧的每日提问卡已移除；回答改为写记忆笔记（`01_自我档案/02_记忆与自传`），
  由忆灵综合后再提议身份修改。自我档案新增忆灵面板、状态/画像/复盘卡、苏格拉底抽屉入口。
- 详细说明见 `docs/MEMORY_HUB.md`。

## 写作与知识库（1.0 P2）

- `GET /api/writing/chapters`：应用内可写的章节清单（人生路 14 章 / 知识库 8 章）。
- `POST /api/writing/notes`、`PUT /api/writing/notes`、`POST /api/writing/notes/move`、
  `POST /api/writing/notes/delete`：新建/编辑/移动/软删除；移动支持固定章节与自定义本子
  （同一库内）；删除移到 `.torch-os/笔记回收站`，`expected_revision` 冲突返回 409；
  所有写入审计；`VAULT_READ_ONLY=1` 时 409。任务、习惯、身份、生活运营不在这里写。
- 今天的日记可直接在应用内写：`GET /api/journal/note?day=` 返回 `content` 与原始字节 `sha256`；
  `POST /api/journal/write` 带 `expected_sha` 只写今天，过去日记保持只读；
  `POST /api/journal/delete` 可从阅读器软删除单篇日记到 `.torch-os/笔记回收站`，删除后回到列表。
- **回收中心（2026-10-31）**：工作台二级菜单的独立页面，按人生路/自我档案/生活运营/知识库
  聚合 `GET /api/recycle`；`POST /api/recycle/restore` 恢复原位置，任务仍按清单/完成状态路由；
  `POST /api/recycle/purge` 彻底删除。待办事项模块的 `/api/time/trash` 继续服务原回收站页面，
  两边查看的是同一批任务文件；本子（文件夹）回收站不并入。
- **人生路 / 知识库首页（2026-10-30，热力图 2026-10-31）**：人生路首屏是
  「今天，写点什么？」+ **写作热力图**（最近 26 周，替代原来的今日日记卡片）+
  我的本子（日记/反思复盘/心得体会/目标/项目/随笔/工作复盘）；知识库首屏是
  「今天想弄明白什么？」+ 打开 DeepSeek + 粘贴分享链接，本子网格在下方。
- **自定义本子 = 文件夹**：`GET /api/notebooks?pillar=life|knowledge` 列出对应库根目录
  （`00_人生路/`、`03_知识库/`）下的自定义本子；`POST /api/notebooks {pillar,name}`
  创建同名文件夹并写 `notebook_created` 审计。人生路 key 为 `custom:<名字>`，
  知识库 key 为 `custom:knowledge:<名字>`；`/api/pillars/{pillar}/overview` 返回
  `custom_notebooks`，`/api/writing/chapters` 也包含这些本子，笔记仍走
  `/api/writing/notes`。名称不能和内置本子/章节/库名重名；
  笔记可在同一库的本子之间移动（笔记卡片右上角「移动」或阅读器「移动到本子」直接选目标，
  也可在编辑器「本子」选择器里保存时移动；后端会实际把 Markdown 文件移到目标文件夹、
  同步已扫描的 `documents` 索引路径并写 `note_moved` 审计），也可软删除到
  `.torch-os/笔记回收站`。
  `POST /api/notebooks/delete {pillar,key,confirm:true}` 支持内置与自建本子：
  **人生路仅保留随笔、知识库仅保留收件箱**，这两个默认本子不可删；其他内置本子
  按章节集合整体移入 `.torch-os/本子回收站`，并从清单/首页/编辑器隐藏
  （`.torch-os/deleted-notebooks.json`），启动不再重建；自建本子整夹删除。
  本子改名/跨库移动仍不提供。
- `POST /api/writing/classify`：AI 判类（写作伙伴/学术导师），AI 不可用时回退到
  `journal_inbox` 规则（含新增的 essay 随笔）；只返回卡片，不落盘。
- `POST /api/writing/polish`：AI 润色，返回 `polished/changes`；前端预览后由用户点
  “应用到正文”，AI 不可用时拒绝、不改正文。
- 前端：人生路首页只保留一个通用写作入口「写点什么」——空白标题、默认分类随笔，
  保存前可改成任何本子；各本子页的「写一条」默认指向该本子。知识库页头「＋ 写一条」
  和阅读器「编辑」同样打开**全屏写作空间**（大标题/大正文、H1–H3 与列表/引用/表格等工具栏、
  写作↔预览、专注模式、字数/阅读时间、revision 冲突提示、在 Obsidian 打开）；
  今天的日记仍从「写作热力图」卡片右上角的「打开今天的日记 / 开始写今天的日记」进入，
  也可直接点热力图里今天的格子。
- **写作热力图（2026-10-31，参考 jotsy 的 ExploreHeatmapCard/Grid）**：
  `GET /api/pillars/life/overview` 增加 `journal_heatmap`：`start`（26 周前周一）、
  `weeks`、`today`、`first_weekday`、`written_days`、`days`（只含有正文的日期，含
  `entries/words/path/legacy`）。前端 `journal-heatmap.js` 按周排布 26×7 格，月份/
  星期标签、今天与悬停描边、字数四档图例（<200/<600/<1500/其余）与
  「近 26 周里有 N 天写了日记」说明；点有日记的格子打开当天笔记，今天的空格子直接
  进入编辑器。`today.journal_has_content` 与 `today.week[].written` 用同一口径
  （只有日期骨架的空文件不算写过），左侧「近 7 天」、7 天圆点与热力图保持一致。
- **写作模板（2026-10-31，参考 jotsy）**：空白写作页标题下有「从模板开始」建议条，
  按当下时段/日记星期推荐，先列推荐、再列最近用过的，最多 5 个；点胶囊直接套用，
  「全部」打开底部模板面板，可搜索、按「全部/推荐/日常/复盘/情绪/生活」筛选、先预览
  （日期与「每日一问」已按当天替换）再使用。内置 8 套：每日记录、晨间日记、晚间复盘、
  感恩日记、每日一问、周回顾、情绪梳理、读书笔记。模板只套用在空白页上，正文编译为
  Markdown（小标题加粗、空行留白、清单/引用/分隔线），日记会把模板标题写成当天正文的
  H1；套用后 6 秒内可在编辑器底部撤销，开始写之后不会覆盖用户内容。
- 知识库首页把「今天想弄明白什么？」和「打开 DeepSeek 提问」「粘贴分享链接」放在第一屏；
  页头「问答」仍保留为快捷入口。
- `POST /api/knowledge/share/preview|archive|digest`：只接受 `chat.deepseek.com/share/<id>`；
  preview 取回问答并预览；archive 在用户确认后写成知识库 Markdown；
  digest 让学术导师整理成 `new_note` 提案，采纳后才落盘。
- 不做 chat.deepseek.com iframe/登录态/Cookie；只做外链 + 公开分享链接解析。
- P2 已注册 `new_note`/`archive_note` 提案 handler。

## 一级导航

| 视图 | hash | 说明 |
|---|---|---|
| 工作台 | `#workbench` | 问候、横幅、时钟/天气/黄历、时间管理（任务、日历、习惯打卡、四象限、回收站） |
| 人生路 | `#life` | 首页「今天，写点什么？」+ 我的本子（日记、反思复盘、心得体会、目标、项目、随笔、工作复盘）；本子内是笔记卡片列表与全屏写作；最近 7 天日记圆点 |
| 自我档案 | `#self` | 身份卡、记忆长河、想法、关系、意义与目标、沉淀与创作、我的八字参考 |
| 生活运营 | `#ops` | 首页「待办事项」本子（任务文档列表 + 工作台看板 CTA）+ 恋爱/健康/财务/家庭系统卡片；二级分区「恋爱系统」（聊天分析、微信同步、画像、日志、每日记忆、报告、值班日历、八字合婚、经期关怀） |
| 知识库 | `#knowledge` | 首页「今天想弄明白什么？」（DeepSeek 提问/分享链接回填）+ 知识本子：收件箱、主题/读书/课程笔记、概念卡、方法库、输出与作品 |
| 设置 | `#settings` | 备份与恢复、库结构与迁移、诊断、导出、数据手册、工具桥、侧边栏顺序、Obsidian 库连接（个人数据库）、帮助 |

旧 hash（`#today`、`#identity`、`#profile`、`#memory`、`#bazi`、`#goals`、`#habits`、
`#brain`、`#documents`、`#notes`、`#archive`、`#help`）仍由 `frontend/app.js` 的
`GO_TARGETS` 做一次性重定向，避免旧书签失效。

## 工作台

- **主动提问（2026-10-27）**：顶部一天一个问题，答了**直接**写进自我档案（身份字段，
  或对应章节的一篇笔记）；「跳过」冷却 7 天，「别再问这个」永久生效；当天答过一个就不再自动出现，
  但卡片留一个「再问一个」（`GET /api/questions/next?force=1`，绕过一天一个的上限）。
  问题有两个来源，同一张卡片消费：
  - **dsh 投的提问队列**（`<个人数据库>/.torch-os/提问队列/`）：大模型读过四库后，把
    「这里该有我、现在空着」变成一个问题，`scripts/question_draft.py` 落成一张卡，**必须带
    `--why`（引用具体笔记）**；torch 只负责什么时候问、答案写哪，答完/跳过自动挪进 `已用/`；
  - **固定题库兜底**：队列空时问「署名 / 三观 / 哪段经历最塑造你」这类地基问题，
    所以离线也有得问、也测得住。
  torch 不调模型——「问什么最有价值」由读过内容的一方决定，「什么时候问」属于界面节奏。
  状态在 `.torch-os/questions.json`，队列超过 14 天自动失效。
  接口：`GET /api/questions/next`、`POST /api/questions/answer`、`POST /api/questions/skip`。
- **待确认中枢（2026-10-27）**：工作台顶部一行常驻数字，点开是带四个 tab 的弹层，
  把四处各自为政的积压收成一个入口：
  - **草稿** `inbox_items`（早报/晚报/周报）：2026-10-05 删掉「提醒与草稿」列表后一直没人看得见，
    现在可以读摘要、标记已读、丢弃（只标记，不删数据）；
  - **事实**：`memories.status='candidate'` + `.torch-os/身份待确认/`，采纳才写进身份卡或
    `01_自我档案/02_记忆与自传`，拒绝不动四库；
  - **收件箱**：`03_知识库/00_收件箱` 带 `steward_policy` 给的章节建议，选好章节一键真的搬过去
    （重名自动 `-2`，不覆盖；任务/日记/习惯不由这里搬）；原入口保持不变；
  - **恋爱记忆**：恋爱系统 `memory_drafts` 里还没写入的每日记忆草稿，写入四库或丢弃；
    动作仍由恋爱系统自己的接口执行，中枢只做汇总。
  接口：`GET /api/pending/summary`、`GET /api/pending/items?kind=`、
  `POST /api/pending/drafts`、`POST /api/pending/facts`、`POST /api/pending/triage`；
  统一结构 `{kind,id,title,preview,lines,source,created,actions}` 由 `backend/app/pending.py` 适配，
  前端不为每种类型写一套渲染。
- 问候语、横幅（座右铭/统计、背景、字体）、时钟、紧凑天气卡、黄历。
- 横幅编辑器（2026-10-27）：座右铭用空行分隔多句，同一条内部的换行原样显示（CSS `pre-line`），
  第一句仍是主字号；图片改成文件选择器（可多选 PNG/JPG/WebP/GIF，单张 ≤10MB），
  选中的图片由 `POST /api/identity/banner/image` 复制到当前个人数据库的 `.torch-os/横幅背景/`，
  返回库内相对路径写回 `身份（identity）.md` 的 `heroes`；仍支持追加 https 链接、列表移除与
  每半小时轮换。
- **横幅统计模式（2026-10-31，用户定义规则；2026-11-01 改 GitHub 风格）**：7 行热力图
  （后端最近 56 天，按横幅实际宽度取最近 15–56 天；格子是**正方形**、2px 圆角 + 3px 间距，
  整块按内容居中，标签右对齐，今日格描边，参考 GitHub 贡献日历），五档颜色
  0 / 25 / 50 / 75 / 100。行依次是人生路、知识库、自我档案（当天新建 Markdown 文档数，
  每 4 篇满格；人生路排除习惯追踪目录）、任务创建（待办根下新建任务数）、任务完成
  （`已完成/` 里完成于当天的任务数）、习惯完成（当天计划内习惯完成比例，主动休息不进分母）、
  总热度（其余各行得分的等权平均，无任务/无计划习惯时不参与）。热力格颜色独立于横幅强调色，
  默认绿（`#2f9e6a`），编辑器里可选预设或任意颜色（`身份（identity）.md` 的 `banner_heat_color`），
  数据看板同步使用。计数来源是
  `backend/app/heatmap.py`；每天新建文档的创建日记录在
  `<个人数据库>/.obsidian/torch-os-heatmap/file-created.json`，原子写入不会把“编辑”误算成“新增”。
  横幅右上角有一键图片/统计切换（`POST /api/identity/banner/mode`，只改 `banner_mode`）与
  「回看」按钮：`GET /api/heatmap/board?view=day|week|month|year&day=YYYY-MM-DD` 返回日/周/月/年
  看板；快照按 `daily/YYYY/YYYY-MM-DD.json`、`weekly/YYYY/YYYY-Www.json`、
  `monthly/YYYY/YYYY-MM.json`、`yearly/YYYY.json` 写入 `.obsidian/torch-os-heatmap/`。
  前端纯渲染在 `frontend/heatmap-board-model.js`，弹窗控制与取数在 `frontend/heatmap-board.js`。
- 天气卡与倒计时/DeepSeek 卡同高，只显示时钟、年月日/星期、城市与今日天气（含高低温）；
  每次打开系统自动刷新。点卡片打开详情浮窗：5 日预报、体感/湿度/降水/风向/风速/气压/日出/日落，
  以及常用城市的搜索添加、切换与删除（城市列表存 `.torch-os/personal-data.json`；
  普通取天气走 Open-Meteo 白名单）。
- 任务：一任务一笔记（`02_生活运营/00_待办事项/**`），看板/清单/标签、日期时间优先级、
  多选批量、右键菜单、手动排序、拖拽换清单、四象限换格、回收站、滴答双向同步。
  **每个任务就是一篇文档**：任务行「文档」、右键菜单和生活运营「待办事项」阅读器都用应用内
  编辑器打开，可改正文与标题（标题就是任务板显示的名字，改名会同步文件名/文档路径）；
  双击任务条（看板行与日历条目）就地改标题；旧任务与虚拟习惯仍回 Obsidian。
- 日历：年/月/周/日/日程/多日/多周视图；任务按清单颜色着色，时间任务上时刻轴。
- 习惯打卡：工作台「✅ 习惯打卡」页有周条、习惯卡、月打卡/总打卡/月完成率/当前连续、
  月历热力图和月度打卡日志；支持每天/每周/每月/每隔 N 天、图标/颜色/分组/提醒/坚持天数/
  自动弹出打卡日志。习惯定义在 `00_人生路/04_目标/04_习惯追踪/习惯/<名称>.md`，
  打卡在 `打卡/YYYY-MM.md`，`习惯定义.md` 是自动生成的只读索引；Markdown 是权威源，
  SQLite 只是统计缓存，Obsidian 里的修改会被读回。
  每日当期的习惯会作为虚拟任务进入「今日任务」和「四象限」；任务行勾选/拖优先级/双击改名
  与习惯页双向同步，虚拟习惯不会落成任务笔记，也不会进入滴答同步。
- 每日必做仍来自 `02_生活运营/00_待办事项/_系统/待办（todos）.md`，勾选写入当天日记；
  习惯页底部保留每日必做清单。
- 旧式 `任务（tasks）.md` 复选框任务只读保留。
- 资料里的 Markdown 复选框可预览后复制为独立任务笔记；每篇笔记带稳定 `导入键`，
  重名/移动后重复导入仍返回原任务；可选关联人生路目标笔记（写入 frontmatter `目标`）。
- 任务与目标联动：任务行显示目标 chip，点击只看该目标；分组菜单新增「目标」；
  目标笔记详情列出关联任务并可打开任务文档。
- 右侧倒计时卡（仿 Apex Dashboard）：目标日期时间、天/小时/分钟、提前提醒、标签与背景；
  齿轮打开设置，默认指向下一个元旦。配置写入 `.torch-os/personal-data.json`（不写 Markdown、不上传），
  本地图片背景也只在浏览器里读取。
- 纪念日卡（仿 Apex Dashboard，默认左栏）：从历史日期起已过去多久（年 月 日 / 总天数 / 天 + 小时），
  支持阳历/农历、每年同日提醒、本机图片或 https 图片 URL 背景；点卡片打开紧凑设置弹窗。
  配置写入 `.torch-os/personal-data.json`，不写 Markdown、不上传。
- 列车追踪小组件（2026-10-27，默认右栏，独立于恋爱系统）：用户自己添加/删除关注车次；
  卡片显示待发车/运行中/停站中/已到达、当前站→下一站、区间进度和倒计时，点开看全部经停站。
  `GET /api/train-tracker/status`、`GET /train?code=`、`POST /timetable`（导入 JSON）、
  `POST /sync`（用户点击时从 12306 官方接口取一次经停站，失败保留本地数据）；时刻表在
  `<个人数据库>/.torch-os/train-tracker/timetable.json`，与 `.torch-os/love/` 完全分开。
- DeepSeek 余额卡（默认右栏）：本地后端代理官方 `GET /api/deepseek/balance`（上游
  `https://api.deepseek.com/user/balance`），显示总余额、充值余额与赠金，支持本机预警线、
  「去充值」跳转和 60 秒自动刷新；Key 优先用本机保存的（AES-GCM 加密在数据目录，不进
  Markdown/备份/导出），否则用 `DEEPSEEK_API_KEY` 环境变量或 DSH 凭据；只发往官方余额接口，
  不写日志、不返回浏览器。QA 模式不联网。
- 运势黄历卡（默认右栏）：与其它小组件同高的小卡片，只保留干支、大号农历日期、个人运势等级与
  简要宜忌；未签到只显示签到入口。真实黄历（农历/干支/五行/冲煞/彭祖/喜福财神/宜忌/吉神凶神，
  来自本地 lunar 库）与个人运势说明都在点击后的宽版浮窗里，可自由切换年/月/日；
  签到记录存本地 SQLite（`fortune_checkins`）。
- 左右两侧小组件栏（天气、倒计时、纪念日、值班、运势、列车追踪、DeepSeek 余额）默认
  天气+倒计时+纪念日+值班在左、运势+列车追踪+DeepSeek 在右；卡片可拖拽互换位置与顺序，
  也可以整卡从一栏拖到另一栏，落点上下半区决定插到前/后。值班卡显示今天与未来 6 天的
  上班/休息和当天值乘车次，点开看未来 31 天；只读，备注仍去恋爱系统的值班日历里改。
  布局写入 `.torch-os/personal-data.json`（localStorage 只是缓存）。新增组件时，给
  `.workbench-side-left` 或 `.workbench-side-right` 的直接子元素补 `data-widget`
  （可选 `data-widget-label`）即可自动获得拖拽、跨栏和 ↑/↓ 排序。
- 网页书签栏（2026-10-28）：问候语右侧一条 Edge 风格书签条，点书签用系统默认浏览器打开
  （Obsidian 里经插件 `torch:open-url` → `shell.openExternal`，只放行 http/https；浏览器直开时
  走 `window.open`），右端齿轮管理：加、改名、改网址、删除、↑↓ 排序，最多 24 个、按网址去重。
  数据键 `torch-os:bookmark-bar`，跟着当前库的 `.torch-os/personal-data.json` 走；首次打开预置
  `https://chat.deepseek.com/`。用途是把「网页版问完 → 复制分享链接/正文回四库」这一步缩短。
- 个性化数据（2026-10-27）：`backend/app/personal_data.py` 提供 `GET/PUT /api/personal-data`，
  文件固定为 `<个人数据库>/.torch-os/personal-data.json`；只接受 `torch-os:` / `torch:` / `torch.`
  键，值 ≤2MB、合计 ≤8MB，单键传 `null` 删除。前端 `frontend/personal-data.js` 启动时把后端值
  灌回 localStorage（按 `vault_id` 校验 `__torch_personal_owner`，换库先清缓存），之后
  `setItem/removeItem` 防抖镜像；天气 30 分钟缓存不写回。
  这是顶层数据规则：系统与小组件个性化数据都放 `.torch-os`，四库只放 Markdown 正文。

## 四库

- 读写边界（1.0 P2 起）：**人生路/知识库的允许章节可在应用内写**（工具栏 + 预览 + revision）；
  任务正文/标题也可在应用内编辑，但走 `/api/time/tasks/document`（文件级 revision，标题改名会
  轮换任务 key），排期/完成/清单仍在任务板；习惯/日记/身份/生活运营仍由各自专用入口管理；
  **自我档案/生活运营页面仍只读**。
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
  （`aria-expanded`，状态存 `torch:rail-groups:life` 并同步到 `.torch-os/personal-data.json`）；子章节缩进，当前
  章节自动展开所属分组，方向键跳过折叠里的章节，未分组章节保持顶层。
- 「打开本周复盘 / 打开本月复盘」只跳转 Obsidian，不生成骨架内容。
- 年度/季度/月度目标笔记是目标选择器与目标导出/只读工具的唯一来源。
- 最近 7 天日记圆点；旧目录日记会提示先在「库结构与迁移」里迁移。
- 习惯定义与打卡在 `00_人生路/04_目标/04_习惯追踪/`（`习惯/` 单篇笔记 + `打卡/`
  月度表 + 自动索引；2026-10-30 起从 06_目标与计划 移到这里）。

### 自我档案

- **档案总览（2026-10-27）**：进入自我档案不再是「最近笔记列表」，而是一份档案——
  封面（署名 / 座右铭逐行 / 世界观·人生观·价值观·自述 / 完整度条 + 「下一步补什么」）、
  目录（七个分区带编号、一句话说明与条数，点进章节）、自传时间线（`02_记忆与自传` 按日期倒序，
  带一句摘要）。字段没填就写「还没写」，不假装认识你；数据来自
  `GET /api/pillars/self/overview` 的 `identity`（含横幅用的 `mottos`）与 `timeline`。
- 身份/三观（`01_自我档案/01_内核与三观/身份（identity）.md`，与工作台横幅共用）。
- 身份待确认（2026-10-27）：「与神对话」从对话里提炼的「关于我」先投到
  `.torch-os/身份待确认/`，右栏校验块逐条「采纳 / 丢弃」。采纳才写入身份字段
  (`POST /api/identity/drafts/{name}/accept`) 或 `01_自我档案/02_记忆与自传` 的一篇笔记；
  身份字段已有内容时不覆盖，丢弃只把卡片挪进 `已丢弃/`，四库一个字不动。
- 记忆与自传、想法与灵感、情感与关系、意义与目标、沉淀与创作。
- 管家会提示可从反思/心得提炼原则；原则草稿的新建与双链回原文在 Obsidian 里做。

### 生活运营

- 首页是「待办事项」本子 + 恋爱/健康/财务/家庭四个系统卡片；健康与恋爱已可用，
  财务/家庭仍是占位。
- 财务账目使用「月度 Markdown 表 + SQLite 投影」，行有稳定 `row_id`。
- 证件/事务一次事件一篇笔记。
- 月度表只读展示趋势 sparkline 与分类占比；「在 Obsidian 打开月度表」跳到原文，
  新增/修改/删除行都在 Obsidian 里做。

### 健康系统（生活运营的二级分区，2026-10-31）

- 与「恋爱系统」平级：生活运营顶部二级菜单「生活运营 / 恋爱系统 / 健康系统」，
  不是新的顶层模块；`#health` 深链接直达。
- 数据全部在 `02_生活运营/02_健康系统/`：`01_每日健康/YYYY-MM-DD.md`（frontmatter
  存睡眠/身体/体征/营养/活动/心理/训练，正文是后端重写的可读摘要）、
  `02_身体档案`（身高/生日/病史/过敏/用药/目标/自定义指标）、
  `03_体检与检验`（一张带参考区间的 Markdown 表）、`00_健康报告`（私人医生产物）。
  旧 `01_健康与体重` 月度表只读兼容，不再写入。
- 原生 12 页：今日总览（六维健康指数圆环、七天文概、待补提醒）、身体与体征
  （BMI/BMR、围度、血压/心率/血氧/血糖/HRV、异常提示）、睡眠（评分与分期）、
  饮食（餐次合计、三大宏量、饮水快捷按钮）、活动（步数/久坐）、心理（情绪/压力/
  精力/焦虑/正念/日记与热力图）、训练（本地动作库、组记录、容量）、体检与医疗
  （检验表与用药）、趋势看板（最多 4 指标叠加，量纲不同自动归一化）、关联洞察、
  健康报告、设置（档案/目标/自定义指标/权重只读）。
- 评分：睡眠 0–100（时长/深睡/REM/入睡耗时/质量）；健康指数六维加权，缺数据维度
  不参与并标「数据不足」；洞察 5 类规则，总样本 <7 天或每组 <5 不生成。
- 私人医生：`health_doctor`（`report`/`advice`），按钮打开角色抽屉，
  `POST /api/health/report` 生成周报/月报并落 `00_健康报告`；AI 不可用时 409。
- 动作库文本复用 openGym（5526 条唯一名称，中文优先），不复制其图片/动画。
- 图表是原生 SVG（`frontend/health-model.js`），页面不使用内联 style（CSP）。
- 未做：训练计划、体检报告 PDF/OCR、CSV 批量导入、进度照片时间轴、Web 通知提醒。
  数据模型、API 与评分公式详见 [`HEALTH_SYSTEM.md`](HEALTH_SYSTEM.md)。

### 恋爱系统（生活运营的二级分区，2026-10-27）

- 位于「生活运营」模块的二级菜单（顶部「生活运营 / 恋爱系统」标签），不是新的顶层模块；不再启动原「恋爱分析助手」的 Electron/FastAPI 服务。
- 数据统一放在当前个人数据库的 `.torch-os/love/`：`love_analysis.db`、`cycle.db`（经期关怀，
  首次保存时创建）、AES 加密 Key、排班表 `xlsx/`、`schedule-notes.json` 值班备注和停用词。
  首次启动会把旧版
  `%LOCALAPPDATA%\love-analysis-assistant`、仓库 `data/love` 和原项目目录里的数据复制过来，
  原文件保留；已有项目、消息、画像、分析记录和日志继续可用。车次时刻表不在这里，
  已独立到 `.torch-os/train-tracker/`。
- 原生页面：总览、**经期关怀**（经期记录、预测、月历、每日身体状态与护理提示）、
  导入聊天（TXT/编码识别）、**微信同步**、统计（趋势/回复间隔/媒体/高频词）、
  AI 深度分析、画像（锁定/编辑/删除）、恋爱日志（手写与按天 AI 生成）、**每日记忆**、
  HTML 报告（独立文档预览与打印）、**值班日历**（月历显示上班/休息，并从排班表单元格
  自动带出当天值乘车次；点日期弹窗可追加交接备注）、**八字合婚**（只读渲染
  `.torch-os/love/bazi.json`：双人命盘、三条依据、口径说明、关键年份与相处建议）、
  设置（DeepSeek Key）。排班页不再出现交路表、车次时刻表、实时位置或沿线天气。
- **经期关怀（2026-10-31）**：恋爱系统二级页面；经期记录、下次经期/排卵/易孕期预测、
  月历、每日身体状态（疼痛/情绪/睡眠/饮水/精力/症状/备注）、阶段护理与伴侣关怀提示、
  趋势统计。数据在 `.torch-os/love/cycle.db`，读取不建文件、首次保存才创建；纯本地计算，
  不接 AI；预测不是诊断，也不能作为避孕依据；只读模式拒绝写入。
- **微信同步（WeFlow 本地 API，2026-10-27）**：WeFlow 负责读取本机已登录微信，
  torch 只访问 `http://127.0.0.1:<端口>`（默认 5031）并只拉取用户选中的一个单聊。
  Token 用 AES-256-GCM 加密存在 `.torch-os/love/`，不回显、不写日志；群聊、公众号和
  其他聊天不会被读取。首次使用可在「微信同步」里用单聊直接新建聊天项目，已有项目则
  先预览、再合并；按平台消息 ID、内容哈希和 ±2 分钟模糊匹配去重，写入前自动备份
  `.torch-os/love/backups/`。API 暂时不可用时，可在同一页导入 WeFlow 导出的 ChatLab
  JSON/JSONL 单聊文件作为兜底（同样只合并，不写四库）。原文只进恋爱数据库，不写四库。
- **每日记忆**：按天用原生 DeepSeek 生成结构化草稿（标题、摘要、小事、计划、心情、关键词），
  用户在页面里修改并确认后，才写入 `02_生活运营/05_情感与记忆` 的普通 Markdown。
  DSH 直接读这些 Markdown 继续对话；批量/每日摘要仍走原生 DeepSeek，因为 DSH 没有
  稳定的批处理接口。未确认的草稿只存在恋爱数据库。
- AI 调用只在用户显式操作时发生；发送前脱敏姓名、电话、证件、邮箱、链接和银行卡号。
  Key 使用 AES-256-GCM 加密保存在同一目录，不上传；`.torch-os` 不参与普通 Vault 扫描和
  工作台备份快照，拷贝整个个人数据库文件夹时会一起带走。
- QA 隔离工作区不注册恋爱路由，也不会打开真实恋爱数据库；微信同步、每日记忆与经期关怀只在个人模式出现。

### 知识库

- 「＋ 在 Obsidian 新建」默认在 `03_知识库/00_收件箱` 建笔记，标题、正文、来源、标签与
  「待分流」都在 Obsidian 里写。
- 主题笔记、读书笔记、课程笔记、概念卡片、方法库、输出与作品。
- 收件箱分流在「待确认中枢」手动选择目标章节后移动；学术导师可在编辑器里讨论/整理。
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
  迁移记录写入 `02_生活运营/00_待办事项/_系统/迁移记录/`。
- 与神对话：完整 DeepSeek Harness Web UI（同源 `/harness/` 代理），嵌在工作台待办事项下方，
  宽度对齐任务主区并空出左右两侧小组件列；顶部「待办事项 / 与神对话」二级菜单用于页面定位。
  它是对话的唯一 AI 入口。恋爱系统的分析/日记生成是独立功能，使用它自己的可选 DeepSeek Key。
- 诊断与恢复：数据库完整性、表计数、Vault 文件数、备份复验、恢复副本只读验证、一键恢复演练。
- 导出：记忆、目标与任务、习惯与打卡、对话、每日记录、身体记录的可读 ZIP；不是恢复包。
- 备份：本地明文 ZIP；恢复写入独立目录，不覆盖当前库，不自动切换。
- 滴答同步：拉取清单/任务、本地新增修改删除回写、清单回写、回收站恢复；
  正文/重复规则/提醒/附件暂不回写。
- 审计：只记事件类型与数量，不记正文、问题、标题或路径。

### 设置 · 个人数据库（2026-10-26）

- 设置 →「Obsidian 库连接」→「个人数据库」：显示当前库路径、模板只读预览
  （章节与分组来自 `backend/app/vault_layout.py`，摘要显示 `47 个章节 / layout v2`；设置页只
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
