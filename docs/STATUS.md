# 当前状态与交接（2026-10-27）

这份文档给下一次会话快速接手用；功能细节以 `README.md`、`docs/FEATURE_MAP.md` 和
`docs/薪火OS-重设计方案-v2.0.md` 为准。

## 快照

- 分支：`main`
- 代码基线：`3c7094a`（`feat(train-tracker): 独立列车追踪新组件及排班表功能`，以 `git log -1` 为准）
- 工作树：微信同步、每日记忆、ChatLab 文件兜底与新章节未提交（2026-10-27，以 `git status` 为准）；
  列车追踪/值班日历已提交（`3c7094a`）。工作树里另有一组不是本次改动的 dsh/mentor 文件
  （`docs/MENTOR.md`、`dsh/skills/`、`scripts/session_brief.py` 等），本次未修改、未回退。
- 布局版本：**`pillars-v2`**（2026-10-26 起；新增 `02_生活运营/05_情感与记忆` 与
  `01_自我档案/07_我的八字参考` 后共 37 章，仍是增量添加，旧路径不变）
- 后端：**807 passed / 1 skipped / 0 failed**（2026-10-27 全量，356.25 秒；5 个既有 QA 编码警告；
  新增 `tests/test_weflow.py` 17 项、`tests/test_love_memory.py` 7 项）
- 前端：**286 passed**（2026-10-27 本次改动完成后实跑；`frontend/tests/love.test.mjs` 新增
  微信同步/每日记忆静态契约与响应式样式用例）。之后并行的 identity-inbox 改动让全量前端出现
  1 个模块映射失败（`pillars.js` 新增 `./identity-inbox.js` 未登记），love/widget 相关 16 项
  仍通过；这不是本次微信同步改动。
- `scripts/check_all.ps1`：**2026-10-25 exit 0**；本轮未重跑全量脚本，后端/前端按上面分别实跑
- 能力标记：**46**（新增 `weflow_sync`、`love_memory`）；用户表：**25 张**（其中多张 deprecated）；
  MCP 只读工具：**12 个**
- 运行环境：`.env` 已指向真实 Vault（`C:\Users\<用户名>\Desktop\Torch\个人数据库`）；
  后端测试使用隔离临时目录，可在这个 `.env` 下直接跑。真实恋爱库当前 **0 个项目 / 0 条消息**，
  微信同步页已支持直接选择单聊新建项目；未在真实库执行同步，WeFlow 联调用的是临时数据库。
- WeFlow 实测：本机 WeFlow API 在 5031 可用，ChatLab Pull 拉到 191 条消息；临时库预览/写入
  计数正确，备份文件生成。Token 不在仓库里，需在页面里粘贴。
- 排障提示：如果全量测试突然出现大量 setup `PermissionError`（`%TEMP%\torch-pytest`
  被占用），通常是有两个 pytest 全量进程同时在跑、共用并互删同一个临时根目录；
  确认只剩一个进程后重跑即可，不是代码回归。

## 已完成

- **2026-10-28 习惯打卡模块（对齐滴答清单 + 待办联动）**：
  工作台习惯页补齐周条、习惯卡、月打卡/总打卡/月完成率/当前连续、月历热力图、月度打卡日志
  和新增/编辑/归档/删除；支持每天/每周/每月/每隔 N 天、图标/颜色/分组/提醒/坚持天数/
  自动弹出打卡日志。习惯定义写入 `00_人生路/06_目标与计划/04_习惯追踪/习惯/<名称>.md`，
  打卡写入 `打卡/YYYY-MM.md`，`习惯定义.md` 是自动生成的只读索引；Markdown 是权威源，
  SQLite（`habit_definitions`/`habit_entries`/`habit_cache_meta`）只是统计缓存。
  当天到期的习惯作为虚拟任务进入今日任务和四象限，勾选/拖优先级/双击改名与习惯页双向同步；
  虚拟习惯不落任务笔记，也不进入滴答推送。新增后端 `test_habit_schedule/stats/virtual_tasks`
  与前端 `habits-model/markup` 测试。
- **2026-10-28 网页版分享链接 → 四库（dsh 工作流）**：
  - 新增 `scripts/share_link.py`：解析 `chat.deepseek.com/share/<id>`，走公开只读接口
    `GET /api/v0/share/content?share_id=` 取回问答；只收提问 `REQUEST` 与最终回答 `RESPONSE`，
    `THINK`/`TOOL_*`/`TIP` 丢弃；支持 `--rounds 1-3` 分轮、`--json` 摘要、`--out` 草稿（不覆盖），
    单篇上限 40 万字（超了只给摘要）。
  - `dsh/AGENTS.md` 增加「网页版回答：我贴分享链接，你取回再归档」：先报标题/章节/摘要，
    确认后 `archive_note.py --source "<链接>"` 落盘，`来源` 写链接；一条链接一篇。
  - 测试：`tests/test_share_link.py` 6 项（解析/筛选/错误映射/标题/摘要）；与
    `archive_note.py --dry-run` 串通验证过，未写任何笔记。
- **2026-10-28 网页书签栏（工作台问候语右侧）**：
  - `frontend/bookmark-model.js`（纯数据层：网址规范化、去重、限量、增删改排序）+ 
    `frontend/bookmarks.js`（DOM：书签条与齿轮面板）；`index.html` 把问候语包进
    `.workbench-topline`，书签条在同一行靠右，齿轮在右端。
  - 数据键 `torch-os:bookmark-bar`，跟着当前库的 `.torch-os/personal-data.json` 走；
    首次打开预置 `https://chat.deepseek.com/`。打开方式：浏览器直开 `window.open`；
    Obsidian 内发 `torch:open-url`，插件 0.99.3 校验来源与 http/https 后 `shell.openExternal`。
  - 测试：`frontend/tests/bookmarks.test.mjs` 7 项；前端全量 **346 passed**；
    `scripts/check_integrity.py`、`scripts/check_module_surface.mjs` 通过。
- **2026-10-28 我的八字参考 + 恋爱系统八字合婚**：
  - 新增章节 `01_自我档案/07_我的八字参考`（`type: bazi`，`vault_layout.CHAPTERS` 共 37 章），
    侧边栏独立一项；两篇笔记：`我的八字参考.md`（命盘/五行/十神/事业/财运/婚姻/六亲/健康/
    学业/大运/流年/开运/人生高度/口径边界）与 `八字速查卡.md`。
  - 恋爱系统新增只读页「八字合婚」（导航第 11 项）：`GET /api/love/bazi` 读取
    `.torch-os/love/bazi.json`，展示双人命盘、三条依据、口径说明、关键年份与相处建议；
    数据不进四库、不进自我档案。
  - 测试：`tests/test_love_bazi.py` 4 项；前端 `frontend/tests/bazi-page.test.mjs` 2 项。
  - 已知：本机 `.env` 指向真实 Vault 时，`tests/test_workspaces.py` 有 3 项既有失败
    （工作区默认名被真实库名覆盖），与本次改动无关。
- **2026-10-27 微信同步 + 每日记忆（生活运营）**：
  - `backend/app/weflow.py`：WeFlow 本地 HTTP 客户端（只访问 `127.0.0.1`，Bearer Token，
    不跟随重定向、不走系统代理、响应大小上限）、ChatLab Pull/JSON/JSONL 解析、offset 翻页；
    首次空页 0.4 秒重试一次，规避 WeFlow 刚启动时的空结果。
  - `backend/app/love.py`：`messages` 增加平台消息 ID/发送者 ID/消息类型/是否本人/回复 ID/
    媒体路径/来源列；新增 `project_sources`、`weflow_sync_logs`、`memory_drafts` 表。
    `/api/love/weflow/status|settings|token|sessions|project|connect|sync|import-preview|import-file`：
    只同步一个单聊、Token 加密且不回显、预览/写入分离、并发保护、写入前备份、按平台 ID/
    内容哈希/±2 分钟模糊匹配去重；没有项目时可直接新建。ChatLab 文件兜底支持 JSON/JSONL 单聊。
  - 每日记忆：`/api/love/memory/days|generate|drafts|write`；原生 DeepSeek 生成结构化草稿，
    用户修改确认后写 `02_生活运营/05_情感与记忆/YYYY-MM-DD 恋爱记忆.md`；只建索引、不自动
    拆候选；目标文件不是 torch 生成时拒绝覆盖；只读模式禁止写入。
  - `vault_layout.py` 增量新增 `love_memory` 章节（36 章，`pillars-v2` 不变）。
  - 前端：恋爱系统新增「微信同步」「每日记忆」两页；支持保存/清除 Token、读取单聊、项目角色
    映射、预览/写入轮询、ChatLab 文件导入、按天草稿编辑/确认写入；没有 Token 不自动联网。
  - 测试：`tests/test_weflow.py` 17 项、`tests/test_love_memory.py` 7 项；后端全量 **807 passed /
    1 skipped / 0 failed**；前端 **286 passed**；真实 WeFlow 联调在临时库完成 191 条消息预览/写入。
- **2026-10-27 列车追踪独立 + 恋爱值班日历**：
  - 新增工作台「列车追踪」小组件（默认右栏，独立于恋爱系统）：用户自己添加/删除关注车次；
    卡片显示待发车/运行中/停站中/已到达、当前站→下一站、区间进度和倒计时；点开看全部经停站，
    可导入 `timetable.json`，也可手动从 12306 同步。关注列表是 `torch-os:train-tracker`。
  - 新增 `backend/app/train_tracker.py`：数据在 `<个人数据库>/.torch-os/train-tracker/timetable.json`；
    `GET /api/train-tracker/status`、`GET /train?code=`、`POST /timetable`、`POST /sync`。
    12306 只走官方 `kyfw.12306.cn`（余票查内部车次号 → `queryByTrainNo` 取经停站），
    只在用户点击时请求一次，失败保留本地数据；旧 `train_timetable.json` 从 `.torch-os/love/`
    与原项目位置复制迁移，只复制不删除。
  - 恋爱系统「查看排班表」改为「值班日历」：`GET /api/love/schedule/month` 只返回每天
    上班/休息；`PUT /note` 把备注写入 `.torch-os/love/schedule-notes.json`；点日期弹窗编辑，
    可写车次。删除恋爱端的车次时刻表、实时位置、沿线天气、`/timetable`、`/train` 端点与
    交路表界面用语；Excel 排班表导入保留（只判断上班/休息）。`LoveStore` 不再暴露时刻表路径。
  - 月份 `torch-os:love-schedule-month` 与关注列表都通过 personal-data 镜像到 `.torch-os`。
  - 测试：新增 `tests/test_train_tracker.py`、`frontend/tests/train-tracker.test.mjs`；更新
    `tests/test_love.py` 的排班用例、`test_love_data_dir.py`、`test_love_storage.py`、
    `test_phase5_cleanup.py`（能力 44）；`frontend/tests/love.test.mjs` 增加排班隐私静态用例。
    后端全量 **772 passed / 1 skipped / 0 failed**；前端 **284 passed**；headless Edge 实测
    列车卡片/经停站、值班日历（上班 21 / 休息 10）、备注写回；12306 官方接口手动同步
    用 G1 实网验证通过（7 个经停站）。
- **2026-10-27 横幅选图 + 顶层 `.torch-os` 数据规则**：
  - 横幅编辑器不再手填路径：`#banner-hero-file` 文件选择器（可多选 PNG/JPG/WebP/GIF，单张 ≤10MB）
    → `POST /api/identity/banner/image`。后端按文件头校验类型，写死复制到
    `<个人数据库>/.torch-os/横幅背景/`，返回库内相对路径；编辑器支持缩略图列表、移除、
    追加 https 链接、最多 12 张，保存写回 `身份（identity）.md` 的 `heroes`，原有每半小时轮换不变。
    座右铭支持空行分隔多句、同一句内换行保留（`parseMottos`/`mottoText` + CSS `pre-line`）。
  - 新增 `backend/app/personal_data.py` + `GET/PUT /api/personal-data`：权威文件
    `<个人数据库>/.torch-os/personal-data.json`；只接受 `torch-os:` / `torch:` / `torch.`
    键，单值 ≤2MB、合计 ≤8MB，单键 `null` 删除，读写受 Vault 沙箱与只读开关约束；
    能力标记新增 `personal_data`（43）。
  - 新增 `frontend/personal-data.js`：启动时先按 `vault_id` 校验缓存归属
    （`__torch_personal_owner`），首次升级本机旧值优先迁入，换库先清缓存；随后灌入后端值、
    patch localStorage 写操作并防抖镜像，页面隐藏/关闭时用 keepalive 补发；localStorage
    配额满时退到内存覆盖层，权威副本仍在 `.torch-os`。天气 30 分钟缓存（`torch-os:weather`）不写回。
  - 真实库 `身份（identity）.md` 的横幅路径从绝对路径 `.torch-os\背景\image.png` 修正为
    `.torch-os/横幅背景/image.png`；原文件备份在
    `.torch-os/backups/身份-横幅路径修正-20261027T121704Z.md`。`/api/identity/hero` 仍兼容
    Vault 内的历史绝对路径。
  - 顶层规则写进 `C:\Users\19814\Desktop\CLAUDE.md`、`README.md`、`docs/DATA_HANDBOOK.md`、
    `docs/FEATURE_MAP.md`、`docs/VAULT_TEMPLATE.md`、`docs/WAKE_UP_START.md` 与换机交接文档。
  - 测试：后端 `tests/test_identity_banner.py`（选图/非法/超限/只读/历史绝对路径）与
    `tests/test_personal_data.py`（合并/删除/校验/只读/损坏文件/图片大小/`vault_id`）；
    前端 `frontend/tests/personal-data.test.mjs` 8 项、`banner.test.mjs` 10 项；
    后端全量 **761 passed / 1 skipped**，前端 **277 passed**；headless Edge 实测选图上传、
    保存、列表缩略图、`vault_id` 归属、镜像写回与座右铭换行显示。
- **2026-10-26 dsh 工作流内置到项目**：规则正文放在 `dsh/AGENTS.md`（不写进数据库、不复制到用户目录）；
  torch 启动 dsh 时注入 `DSH_AGENTS_HOME=<仓库>/dsh` 与 `TORCH_REPO_ROOT=<仓库>`，任何库/任何工作区
  的新会话都自动加载。配套 `scripts/session_brief.py`（只读开场简报）与 `tests/test_dsh_workflow.py`
  （规则章节路径与 `vault_layout` 同步的守卫）；见 `docs/DSH_WORKFLOW.md`。
- **2026-10-26 人生路布局 v2（分组侧边栏 + 迁移）**：`LAYOUT_VERSION` 升到 `pillars-v2`，
  人生路新增四个一级分组（心得体会 / 反思复盘 / 目标 / 项目）；`chapter_manifest()` 每库返回
  `groups`（只有人生路非空，其它三库 `[]`），顺序与章节 key 引用写在 `CHAPTER_GROUPS`。
  前端 `buildRailItems()` 把分组行放在该组首个章节的位置，未分组章节保持顶层：点分组标题
  只折叠/展开（`aria-expanded`，状态存 `localStorage` 的 `torch:rail-groups:<view>`），
  子章节缩进，当前章节自动展开所属分组，方向键跳过折叠里的章节，窄屏横排时分组铺平。
  目录迁到 v2：反思 `00_人生路/02_反思复盘/01_反思`、周复盘 `.../02_周复盘`、月复盘
  `.../03_月复盘`、年度/季度/月度目标 `00_人生路/04_目标/01..03`、项目计划
  `00_人生路/05_项目/01_项目计划`、项目复盘 `00_人生路/05_项目/02_项目复盘`；日记、任务与
  待办、习惯追踪（`06_目标与计划/04_习惯追踪`）、会议收获、踩坑记录不变。
  设置 →「库结构与迁移」预览按 `V1_CHAPTER_MOVES` 逐文件规划 v1→v2 移动（理由
  `layout-v2:<章节 key>`，冲突单独列出，执行要带 `plan_hash`，先备份、改写链接、可回滚）；
  新库直接按 v2 路径创建，`.torch-os/vault-layout.json` 也含 `groups`。真实库已把
  `00_人生路/06_目标与计划/05_项目计划` 下 2 个文件移到 `00_人生路/05_项目/01_项目计划`，
  `00_人生路/08_任务与待办/任务/考研规划.md` 的 wiki 链接同步改写（移动前已备份）。
  测试：后端 **712 passed / 1 skipped**（2026-10-26 全量）；前端 **207 passed**（含
  `frontend/tests/rail-groups.test.mjs` 7 项）。
- **2026-10-26 个人数据库模板**：设置 →「Obsidian 库连接」新增「个人数据库」卡，显示当前库路径、
  模板只读预览（唯一来源 `backend/app/vault_layout.py`）与「创建新的个人数据库」（父文件夹 + 名字）。
  创建只写新文件夹：四库章节（4 库 / 35 章）、`00_人生路/08_任务与待办/任务` 与 `_系统`、
  `.torch-os/vault-layout.json`（章节 1:1 映射）、`.obsidian` 预设与 torch 插件、种子身份笔记；
  不碰当前库、不覆盖非空文件夹、不允许嵌在当前库内。接口 `GET /api/vault/template`（QA 409）与
  `POST /api/vault/create`（201；422 名字/相对路径，409 非空/嵌套/重名/QA）；能力标记新增
  `vault_admin`（仅个人运行时为真，`docs/FEATURE_MAP.md` 已同步，文档契约测试恢复通过）；
  注册表 `data/workspaces.json`，创建写一条不含路径/正文的 `vault_created` 审计。切换：插件里
  在 Obsidian 打开另一个库（每库独立实例，端口从 9168 起）；浏览器模式仍「切换到此工作区」写
  `.env` 后重启。模板说明见 `docs/VAULT_TEMPLATE.md`；`tests/test_vault_admin.py` **10 passed**，
  前端 **199 passed**（含 `frontend/tests/vault-admin.test.mjs`）。
- **2026-10-26 四库只读 + 跳转 Obsidian**：四库（人生路/自我档案/生活运营/知识库）在插件里
  改为只读浏览，手动 Markdown 写入统一回到 Obsidian 原生编辑器；新建、改标签、移章节、
  复盘、体重/账目行、习惯文件与管家提案都只做跳转（「＋ 在 Obsidian 新建」、
  「在 Obsidian 打开并编辑」、「在 Obsidian 打开月度表」、「打开本周复盘 / 打开本月复盘」、
  「在 Obsidian 打开习惯文件」、「在 Obsidian 打开原文」）。插件用 `torch:new-note`
  宿主桥建文件并打开，浏览器模式退回 `obsidian://new`；新建仍按四库章节落到对应目录。
  退役写接口 `POST /api/capture`、`/api/notes/update`、`/api/notes/move`、
  `/api/reviews/weekly`、`/api/reviews/monthly`、`/api/ops/{dataset}/months/{month}/rows`
  （新增）、`POST .../rows/{row_id}`（更新）、`DELETE .../rows/{row_id}`、`.../restore`、
  `/api/steward/proposals/apply`、`/apply-batch`、`/api/journal/note/generate`（均 404）。
  工作台任务、日历、习惯打卡、四象限、滴答同步、备份与迁移不变。前端测试 **184 passed**
  （含宿主桥/只读守卫）。
- **2026-10-26 收尾**：仓库与库整体搬进 `Desktop\Torch`（`torch-os` + `个人数据库`），
  插件 `repoRoot` 与 `.env` 已同步；重建索引（30 条移动前残留标记为不可用，21 条可用
  记录与磁盘一一对应）；真实库备份 + 一键恢复演练 `ok: true`、`sqlite_integrity: ok`；
  清理 deprecated 表 13 行（`suggestions` 10 / `conversations` 1 / `messages` 2），
  CSV 与 `restore.sql` 在 `.torch-os/diagnostics/deprecated-20261026T002919/`；
  12 个只读工具已接进 dsh（`mcp__torch__*`，配置方式见 `docs/MCP_BRIDGE.md`）。
- **2026-10-25 唯一 AI：DeepSeek Harness**：工作台「与神对话」改为完整 dsh Web UI，
  经 torch 同源 `/harness/` 反向代理加载（HTTP + WebSocket，服务端 cookie，兼容
  Obsidian 跨站 iframe）。torch 自动检测 `dsh`，缺失时用 `npx` 启动；模型、密钥与
  权限都在 dsh 内。已删除模型网关、模型密钥设置、本地回答、ACP 聊天、管家问答、
  Ollama/向量检索与相关测试；检索改为纯词法。已用真实 npx dsh 验证完整界面、
  WebSocket、跨站 iframe（Obsidian 场景）与退出清理。
- **2026-10-23 与神对话（历史，已被 2026-10-25 取代）**：工作台移除「今天你动过哪些文件」「今天的日记」两个面板，
  改为 `agent-view`「与神对话」；`/api/chat/stream` 合并 dsh ACP 控制面与
  `agent/assistant-stream` 实时流（思考/正文/工具增量），新增 `/api/agent/cancel`；
  dsh 使用独立 `torch-live` profile 与 `agent/dsh-torch-live` 插件；
  旧前端 `journal.js` / `journal-note.js` 已删除，后端日记 API 保留给今日工具与导出。
- **2026-10-23 全产品 UI 打磨**：主列统一为 1240px 并让顶部导航、工作台、四库、设置
  对齐；窄屏边距 18/14px；统一卡片 14px 圆角与按钮节奏；四库移动端操作区、设置页
  操作换行、开始提示关闭按钮、时间模块横向滚动条、空状态、日历格与「与神对话」
  窄屏头部/正文列宽均已修正。
- **2026-10-23 对话上下文持久化（历史，ACP 已移除）**：会话/消息存 SQLite；dsh ACP session 通过
  `harness_sessions` 跨进程 resume；前端优先恢复本地会话指针，否则自动接最近会话；
  SSE 客户端中途断开时由后台 worker 完成并保存回答，不丢最后一轮。
- **2026-10-22 清单删除修复**：清单目录里的百度网盘同步临时文件
  （`*.baiduyun.uploading.cfg`）不再阻止删除清单，会随任务一起移入回收站；
  其它未知非任务文件仍会拦截，避免误删附件。
- **2026-10-23 四象限同步修复**：切回任务/日历/四象限页时会先静默重读任务板，
  不再因为页面缓存而继续显示已在 Obsidian、其他窗口或滴答同步中删除的任务。
- **2026-10-22 数据整理**：`00-System` / `02-Life` / `提示词` 全部并入四库；
  任务与待办在 `00_人生路/08_任务与待办`（任务笔记在 `任务/`，配置/迁移记录在 `_系统/`），
  旧重复文件归档到 `_旧版`；滴答映射 relpath、任务回收站、内部链接与索引已同步迁移。
- Phase 1–4：四库布局迁移、六项导航、四库页面、捕获/搜索、体重账目月度表、
  习惯 Markdown 镜像、周/月复盘骨架与确定性管家提案。
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
    已进入建议卡的条目不再在下方重复。2026-10-26 起四库只读：建议只读展示，移动/改名回 Obsidian。
  - 四库笔记详情支持复制 Obsidian 双链/路径、增删标签、移动章节与查看反向链接。
    2026-10-26 起只读：仅保留复制双链/路径与反向链接，编辑点「在 Obsidian 打开并编辑」回 Obsidian。
  - 「与神对话」右上角新增 DeepSeek Harness 官网入口，新标签页打开。
  - 友好日期（今天 16:40 / 昨天 / 10月22日）、搜索回车打开第一条；
  - 顶部「?」帮助入口、首次使用引导卡、四库空状态示例与失败重试；
  - 小屏顶部标签单行滚动并自动定位当前页、设置侧栏自动定位；
  - 1180px 以下四库单列、章节轨横向、右栏信息下移保留；
  - 小屏任务主区优先、时间管理按钮横向 chips；
  - 页面切换淡入、toast 改用主题表面与语义边框、生活运营删除确认
    （2026-10-26 起生活运营行只在 Obsidian 里改，插件没有删除入口）。
  - 键盘导航与快捷键：`?` 打开快捷键总览，帮助页有同一入口；`g` 后按
    `w/l/s/o/k/,`/`h` 跳转，`n` 在四库打开「记一条」、在工作台聚焦任务输入
    （2026-10-26 起四库为「在 Obsidian 新建」）；
    顶部标签、设置侧栏、四库章节轨、最近 7 天与任务页标签支持方向键，Home/End 到两端；
    时间页标签改为 roving tabindex，只有当前标签在 Tab 顺序里。
  - 危险操作策略：`confirmAction` 新增危险色选项，清空/彻底删除回收站、回滚迁移、
    清除滴答凭据、开启本地删除回写使用它；生活运营删除行后 15 秒内可撤销，
    按原 row_id 与位置写回，后端新增 `POST /api/ops/{dataset}/months/{month}/rows/{row_id}/restore`。
    toast 支持一个动作按钮；确认框默认焦点仍在“取消”。
    （2026-10-26 起生活运营删除/撤销与 restore 路由已退役；危险色仍用于回收站、迁移回滚与凭据清理）
  - 任务 ↔ 目标联动：任务行显示目标 chip，点击只看该目标；分组菜单新增「目标」，
    未关联任务归入「未关联目标」；目标笔记详情列出关联任务，可直接打开任务文档。
    任务板加载时并行读取 `GET /api/goals`，目标标题变化会触发重画。
- QA/发布：
  - `browser_pillars_probe.py` 在 QA 工作区自备日记与搜索样本，真实库不写入；
    探针新增快捷键总览、g 组合跳转、n 捕获、方向键与生活运营删除/撤销检查；
  - `round.ps1` 的浏览器步骤已切换到四库探针；旧 `browser_smoke.py` 仅保留给失败报告测试。

## 仍保留 / 已知限制

- 「与神对话」现在是完整 dsh：默认可按 dsh 自己的权限读写当前库，torch 不再套只读确认或自动备份。
  dsh 首次启动可能需要 Node.js >=24.9 / npx 下载；模型、密钥、账号登录都在 dsh 内完成。
- torch 不再提供模型、模型密钥、本地回答、管家问答或向量检索；问答统一在 dsh。
- dsh 的 API Key/模型配置在 dsh 内完成；若使用账号 OAuth，根路径 `/oauth/callback`
  可能不在 `/harness/` 代理下，优先用 dsh 的 API Key 配置。
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
- 四库在插件里只读（2026-10-26 起）：新建、编辑、移动、复盘和体重/账目行都在 Obsidian 里完成，
  插件只做浏览、搜索与跳转。
- 新库模板只建目录与模板文件（含 `.torch-os/vault-layout.json`），不拷贝任何旧数据；已有库不会
  自动补写这份清单或 `.obsidian` 预设——换布局仍要先备份再迁移，或新建库后把笔记搬过去。
- 布局 v2 的搬迁只覆盖 `V1_CHAPTER_MOVES` 里的 8 个 v1 目录；已有库不会自动改名或移动，
  迁移后旧目录只做只读兜底、空目录保留。真实库这轮只搬了项目计划下的 2 个文件（见上）。
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

本轮用户指定顺序：**键盘导航（已完成）→ 危险操作确认与撤销（已完成）→ 退役旧后端（已完成）→ 新产品功能（任务 ↔ 目标联动、知识库收件箱批量整理已完成；2026-10-26 起四库只读，批量整理入口已退役）**。

1. 四库已改为只读＋跳转 Obsidian（2026-10-26）：浏览、搜索、复制双链/路径、反向链接保留；
   增删标签与移动章节退役，编辑统一回 Obsidian。
2. 用真实库做一次完整备份 → 恢复演练 → 视情况清理 deprecated 表（必须由用户确认）。
3. 继续选下一组产品功能（按用户需求）。
4. dsh 后续：12 个只读工具已接入（2026-10-26，见 `docs/MCP_BRIDGE.md`）；还剩
   dsh 的 OAuth 回调验证与模型/账号配置。
5. 用真实 dsh 版本跑一次浏览器/ Obsidian 验收，确认 `/harness/` 的 HTTP + WebSocket
   与 npx 首次启动提示；后续 dsh 升级时回归代理契约。
