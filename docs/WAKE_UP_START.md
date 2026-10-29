# 唤醒与启动（1.0）

这份说明对应运行中的 1.0 版。它只覆盖本仓库当前能验证的行为；遇到与界面不一致时，
以 `GET /api/config/status`、`GET /api/schema.json` 和实际页面为准。

## 1. 首次启动

```powershell
# 仓库根目录
.\安装.cmd                  # 建 venv、装依赖、复制 .env、生成示例数据
.\scripts\start.ps1         # 启动本地服务
```

默认地址是 `http://127.0.0.1:8765`。浏览器直接打开即可；推荐在 Obsidian 里安装插件，
用 `scripts\install-obsidian-plugin.ps1` 把界面装进当前库。

## 2. 60 秒自检

1. 打开 `http://127.0.0.1:8765/api/health`，应看到 `status: ok` 和版本号。
2. 打开 `http://127.0.0.1:8765/api/config/status`，确认 `version` 是 `1.0.0`，
   `capabilities` 里包含 `workbench`、`tasks`、`habits`、`schema_export`、`tools`。
3. 打开设置 →「数据与恢复自检」，确认数据库完整性、Vault 文件数与最近备份。
4. 点一次「一键恢复演练」：只生成备份与独立恢复副本，不覆盖当前库，不切换数据。
5. 四库各点一次：人生路、自我档案、生活运营、知识库；确认章节轨、最近记录和搜索可用；
   点「＋ 在 Obsidian 新建」，确认是在 Obsidian 里建并打开笔记（浏览器模式走 `obsidian://new`）。
6. 人生路点「打开本周复盘 / 打开本月复盘」、生活运营点「在 Obsidian 打开月度表」，
   确认都是打开 Obsidian 里的 Markdown；插件自己不写这些文件。
7. 工作台点一次任务、日历、习惯打卡、四象限、回收站，确认既有功能没有回归。习惯页要能看到
   周条、习惯卡、四格统计、月历与月度日志；新建一个每天习惯后，它应出现在今日任务和四象限，
   在任务行勾选/改优先级/改名会同步回习惯页，在习惯页打卡也会同步到任务行。
8. 点顶部「?」，确认直接打开设置里的「帮助」；再切回四库，帮助内容不应残留在页面下方。
9. 设置 →「Obsidian 库连接」→「个人数据库」：确认卡片显示当前库路径，展开「模板结构」看
   只读预览（章节来自 `backend/app/vault_layout.py`，页面不能编辑模板）；填父文件夹 + 名称后
   点「创建个人数据库」，父文件夹选一个**空**的临时目录、名字填 `自检库-日期`，确认只新建了
   一个文件夹（四库 + `_系统`/`任务` + `.torch-os/vault-layout.json` + `.obsidian` 预设 +
   种子身份笔记），当前库的笔记与目录没有任何改动（只会多一条 `vault_created` 审计）。
   这个自检库可在 Obsidian 里打开验证后再自行删除；不要拿现有库或非空文件夹试创建。
10. 人生路侧边栏：点「心得体会 / 反思复盘 / 目标 / 项目」分组标题，确认子章节收起/展开、
    `aria-expanded` 跟着变，刷新后折叠状态还在；点分组里的章节，分组保持展开；已折叠的
    子章节不会被方向键选中；窄屏（章节轨横排）时分组标题仍在、展开的章节紧跟其后。
11. 设置 →「库结构与迁移」→ 点预览，确认计划里能看到人生路 v1 章节目录 → v2 的新路径
    （理由带 `layout-v2:`，如 `00_人生路/02_反思` → `00_人生路/02_反思复盘/01_反思`、
    `00_人生路/06_目标与计划/05_项目计划` → `00_人生路/05_项目/01_项目计划`）；日记、任务、
    习惯、会议/踩坑仍是原地保留。**只看预览，不要执行**：真实库已经搬过，v1 目录若已空就
    不会再有这些行。
12. 如果本机已装并登录 WeFlow：恋爱系统 →「微信同步」→ 填 WeFlow 的 API Token（默认端口
    5031）→「保存并测试连接」→「读取联系人」→ 只选你对象的那一个单聊 → 先「预览同步」，
    确认新增/重复数量后再「写入数据库」。**没有 WeFlow 或不想同步时不要点写入**；群聊、
    公众号和其他聊天不会被读取。写入前会自动备份到 `.torch-os/love/backups/`。
    API 暂时不可用时，可在同一页导入 WeFlow 导出的 ChatLab JSON/JSONL 单聊文件兜底。
13. 恋爱系统 →「每日记忆」→ 选一个已有聊天记录的日期 → 生成草稿 → 核对/修改 →
    「写入生活运营」；确认文件出现在 `02_生活运营/05_情感与记忆/`。没有配置 DeepSeek Key
    时只应看到可读错误，不会生成或写入。

## 3. 数据放在哪里

- 个人内容：当前配置的 Obsidian Vault（Markdown 是权威源）。
  - `00_人生路/`、`01_自我档案/`、`02_生活运营/`、`03_知识库/`。
  - 人生路 v2 分组：`00_人生路/02_反思复盘/`（反思/周复盘/月复盘）、`04_目标/`（年度/季度/
    月度）、`05_项目/`（项目计划/项目复盘）；习惯追踪仍在 `00_人生路/06_目标与计划/04_习惯追踪/`。
  - 工作台任务与配置：`00_人生路/08_任务与待办/任务/`、
    `00_人生路/08_任务与待办/_系统/任务（tasks）.md`。
- 索引、备份、恢复副本、导入、审计、迁移日志、滴答凭据：`<Vault>/.torch-os/`。
- 小组件个性化（城市、倒计时、纪念日、排序、时间视图、列车关注列表等）：
  `<Vault>/.torch-os/personal-data.json`，浏览器 localStorage 只是缓存；
  横幅图片：`<Vault>/.torch-os/横幅背景/`；列车时刻表：`<Vault>/.torch-os/train-tracker/`；
  恋爱数据库、加密 Key、WeFlow Token、排班表、值班备注与微信同步备份：
  `<Vault>/.torch-os/love/`；确认后的每日情感记忆是四库里的
  `02_生活运营/05_情感与记忆/`。
  顶层规则：四库只放 Markdown 正文，其余系统数据都进 `.torch-os`。
- DeepSeek Harness：唯一的 AI 入口；模型/密钥存在 dsh 的 `DSH_HOME`，torch 不保存。
- 旧 Apex `dashboard.md`、`dashboard-2.md` 原地保留，不索引、不迁移。

## 4. 0.99 的变化

- **2026-10-27 微信同步 + 每日记忆（生活运营）**：
  - 恋爱系统新增「微信同步」：通过本机 WeFlow 的 HTTP API 读取微信，只选一个单聊；
    可合并进现有项目，没有项目时直接新建。Token 加密保存在 `.torch-os/love/`，只访问
    `127.0.0.1`；按平台消息 ID、内容哈希和 ±2 分钟模糊匹配合并，写入前备份。
  - 新增「每日记忆」：原生 DeepSeek 按天生成草稿（标题/摘要/小事/计划/心情/关键词），
    用户确认后才写入 `02_生活运营/05_情感与记忆` 的 Markdown；未确认草稿只在恋爱库。
    DSH 读四库里的 Markdown，批量摘要仍走原生 DeepSeek。
  - 新章节是增量添加（36 章，`pillars-v2` 不变），旧路径不改；能力标记增加
    `weflow_sync`、`love_memory`。
- **2026-10-26 人生路布局 v2（分组侧边栏）**：`LAYOUT_VERSION` 升到 `pillars-v2`，人生路章节
  收进 `02_反思复盘/`、`04_目标/`、`05_项目/`（日记、任务、习惯、会议/踩坑不变）；侧边栏按
  四个一级分组（心得体会/反思复盘/目标/项目）折叠，分组行出现在首个章节的位置，当前章节
  自动展开，折叠状态存 `localStorage`。旧库的 v1 章节目录由设置 →「库结构与迁移」的预览
  逐文件搬到 v2（备份、链接改写、可回滚），新库直接按 v2 创建。
- **2026-10-26 四库改为只读**：手动写入只在 Obsidian。插件里的四库只浏览/搜索，
  新建、改标签、移章节、复盘、体重账目行都跳回 Obsidian 原生编辑器；插件用
  `torch:new-note` 宿主桥建笔记，浏览器模式退回 `obsidian://new`。同时退役
  `POST /api/capture`、`/api/notes/update`、`/api/notes/move`、`/api/reviews/weekly`、
  `/api/reviews/monthly`、生活运营行增删改/恢复、`/api/steward/proposals/apply*`、
  `/api/journal/note/generate`（均 404）；工作台任务/日历/习惯打卡/四象限/滴答/备份/迁移不变。
- 四库工作台、薪火管家、库结构迁移、日记最近 7 天圆点、全局搜索键盘导航已落地
  （2026-10-26 起四库编辑改为跳转 Obsidian，见上）。
- Phase 5 保守清理：
  - 删除旧「数字分身 / 第二大脑」页面遗留的前端文件与对应测试；
  - 退役 `/api/bazi/*`、`/api/profile*`、`/api/chat/preview`、`/api/memories/{id}/usage`、
    `/api/suggestions*`、`/api/imports/share/*`，以及旧结构化日记
    `/api/journal/{day}`、`/api/journal/monthly-review` 等；`/api/journal/note` 仍用于工作台日记读取；
  - `bazi_profiles`、`suggestions`、`journal_entries`、`journal_actions` 保留为 deprecated，
    不自动删除；日记导出仍可读取历史 `journal_entries`；
  - **2026-10-22 退役 `goals` / `learning` / `body` 旧后端**：目标改读人生路目标笔记，
    任务导入改写入 Markdown 任务笔记，身体汇总/导出改读健康月度表，学习能力下线；
    旧表保留为 deprecated，不自动 DROP，工具桥收缩为 12 个只读工具。
- 旧的 `#today`、`#identity`、`#profile`、`#bazi` 等 hash 仍会重定向到新页面。

## 5. 验证命令

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --experimental-vm-modules --test frontend/tests/*.test.mjs
.\.venv\Scripts\python.exe scripts\check_integrity.py
node scripts\check_module_surface.mjs
.\.venv\Scripts\python.exe -m compileall -q backend scripts tests
.\.venv\Scripts\python.exe -m pip check
```

发布前建议再跑一次真实浏览器探针：

```powershell
.\.venv\Scripts\python.exe scripts\browser_pillars_probe.py --base-url http://127.0.0.1:8765
```

## 6. 已知边界

完整的已移除路由、deprecated 表与仍保留旧后端清单见 `docs/DEPRECATIONS.md`；
当前验证结果与下一步见 `docs/STATUS.md`。

- 四库在插件里只读；新建、编辑、移动、复盘与体重/账目行都在 Obsidian 里完成，
  插件只提供跳转（`torch:new-note` / `obsidian://new`）。
- 分享导入已退役；Wolai 导入仍有后端接口，但当前没有前端入口。
- 旧「目标 / 记忆审核 / 八字 / 身体记录 / Vault 体检」页面不再提供 UI；
  旧数据表保留历史数据，新数据写入 Markdown（目标/任务/身体），学习能力已下线。
- 备份恢复不会自动把副本切换为当前库；切换需要另行设计并明确确认。
- 「实时」滴答同步是后台约每 15 秒轮询推送，不是长连接。
- torch 不再调用模型；「与神对话」内嵌完整 dsh，发送范围与权限由 dsh 决定。
