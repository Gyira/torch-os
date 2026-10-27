# 个人数据库模板（vault template）

这份文档说明设置 →「Obsidian 库连接」→「个人数据库」创建出来的库长什么样、怎么改、
怎么切换。模板的唯一来源是 `backend/app/vault_layout.py`（`LAYOUT_VERSION = "pillars-v2"`，
4 个库 / 35 个章节）；设置页只展示、不复制路径表，也不能在界面里编辑模板。

卡片本身只渲染后端返回值：当前库路径、可折叠的「模板结构」（摘要显示
`35 个章节 / layout v2`，即 `pillars-v2`）、父文件夹 + 名称表单，以及主按钮
「创建个人数据库」（卡片描述里写作「创建新的个人数据库」）；没有编辑模板的入口。

## 一次创建会写什么

创建只写**一个新的文件夹**，内容一次成型：

- 四个库目录（`PILLAR_DIRS`）和 35 个章节目录（`CHAPTERS`），章节目录与四库 1:1 对应；
- 任务目录：`00_人生路/08_任务与待办/任务/`（一任务一笔记，清单 = 子文件夹）与
  `00_人生路/08_任务与待办/_系统/`（清单配置、待办与迁移记录）；
- `.torch-os/vault-layout.json`：这次创建用到的布局清单（schema 1，见下）；
- `.obsidian/` 预设：`app.json`、`appearance.json`、`core-plugins.json`、
  `community-plugins.json`；
- `.obsidian/plugins/torch/`：`main.js`、`manifest.json`、`styles.css` 从仓库
  `obsidian-plugin/` 原样复制，另写一份 `data.json`（带本机 `repoRoot`）；
- 种子文件：`01_自我档案/01_内核与三观/身份（identity）.md`（frontmatter 带 `id`、
  `type: identity`、`created`、`updated` 与三观空字段，正文是「我是谁 / 三观 / 我要去哪里」）。

除这一篇身份笔记外**不生成任何 Markdown**：新库没有示例笔记、日记、旧目录，也没有
`dashboard*.md`。创建过程先写同级临时目录 `.torch-new-<随机>`，成功后再改名成目标
文件夹；中途失败会清掉临时目录，不会留下半个库。

## 四个库与 35 个章节

路径都相对库根目录，使用 POSIX 分隔符（后端统一，Windows 上由文件系统换算）。

### 00_人生路（16 章）

| 章节 | 路径 | 默认 type | 分组 |
|---|---|---|---|
| 日记 | `00_人生路/01_日记` | `journal` | — |
| 任务与待办 | `00_人生路/08_任务与待办` | `task` | — |
| 反思 | `00_人生路/02_反思复盘/01_反思` | `reflection` | 反思复盘 |
| 生活心得 | `00_人生路/03_心得体会/01_生活心得` | `insight` | 心得体会 |
| 工作心得 | `00_人生路/03_心得体会/02_工作心得` | `insight` | 心得体会 |
| 学习心得 | `00_人生路/03_心得体会/03_学习心得` | `insight` | 心得体会 |
| 周复盘 | `00_人生路/02_反思复盘/02_周复盘` | `weekly_review` | 反思复盘 |
| 月复盘 | `00_人生路/02_反思复盘/03_月复盘` | `monthly_review` | 反思复盘 |
| 年度目标 | `00_人生路/04_目标/01_年度目标` | `goal` | 目标 |
| 季度目标 | `00_人生路/04_目标/02_季度目标` | `goal` | 目标 |
| 月度目标 | `00_人生路/04_目标/03_月度目标` | `goal` | 目标 |
| 习惯追踪 | `00_人生路/06_目标与计划/04_习惯追踪` | `habit_log` | — |
| 项目计划 | `00_人生路/05_项目/01_项目计划` | `plan` | 项目 |
| 项目复盘 | `00_人生路/05_项目/02_项目复盘` | `reflection` | 项目 |
| 会议收获 | `00_人生路/07_工作复盘/02_会议收获` | `insight` | — |
| 踩坑记录 | `00_人生路/07_工作复盘/03_踩坑记录` | `insight` | — |

### 01_自我档案（7 章）

| 章节 | 路径 | 默认 type |
|---|---|---|
| 内核与三观 | `01_自我档案/01_内核与三观` | `value` |
| 记忆与自传 | `01_自我档案/02_记忆与自传` | `memory` |
| 想法与灵感 | `01_自我档案/03_想法与灵感` | `idea` |
| 情感与关系 | `01_自我档案/04_情感与关系` | `relationship` |
| 意义与目标 | `01_自我档案/05_意义与目标` | `meaning` |
| 工作方法沉淀 | `01_自我档案/06_沉淀与创作/01_工作方法` | `principle` |
| 职业原则 | `01_自我档案/06_沉淀与创作/02_职业原则` | `principle` |

### 02_生活运营（4 章）

| 章节 | 路径 | 默认 type |
|---|---|---|
| 健康与体重 | `02_生活运营/01_健康与体重` | `health_month` |
| 财富与记账 | `02_生活运营/02_财富与记账` | `finance_month` |
| 体检与医疗 | `02_生活运营/03_体检与医疗` | `medical` |
| 证件与事务 | `02_生活运营/04_证件与事务` | `document` |

### 03_知识库（8 章）

| 章节 | 路径 | 默认 type |
|---|---|---|
| 收件箱 | `03_知识库/00_收件箱` | `inbox` |
| 主题笔记 | `03_知识库/01_主题笔记` | `topic` |
| 读书笔记 | `03_知识库/02_读书笔记` | `reading` |
| 课程笔记 | `03_知识库/03_课程笔记` | `course` |
| 概念卡片 | `03_知识库/04_概念卡片` | `concept` |
| 工作方法 | `03_知识库/05_方法库/01_工作方法` | `method` |
| 通用方法 | `03_知识库/05_方法库/02_通用方法` | `method` |
| 输出与作品 | `03_知识库/06_输出与作品` | `output` |

`key`（如 `journal`、`goal_year`、`inbox`）只用于接口与映射，不写进目录名；同一个
`default_type` 可以被多个章节共用（心得/复盘/原则等）。`GET /api/pillars` 返回的章节清单
就是这张表的 JSON 版本，前端不做第二份。

### 侧边栏分组（人生路）

分组只影响侧边栏展示，章节仍是 `CHAPTERS` 里的唯一条目。`GET /api/pillars` 返回
`chapter_manifest()` 的结构：每个库上多一个 `groups`，只有人生路非空，其它三个库是 `[]`。

下表按侧边栏实际出现顺序（分组行跟着该组第一个章节的位置走）：

| 分组 key | 分组 | 章节（按清单顺序） |
|---|---|---|
| `review` | 反思复盘 | 反思、周复盘、月复盘 |
| `insight` | 心得体会 | 生活心得、工作心得、学习心得 |
| `goal` | 目标 | 年度目标、季度目标、月度目标 |
| `project` | 项目 | 项目计划、项目复盘 |

- 分组行渲染在该组**第一个章节**原来的位置；未分组的日记、任务与待办、习惯追踪、
  会议收获、踩坑记录仍是顶层章节。分组只按章节 key 引用，不复制路径。
- 点分组标题只做折叠/展开，不改变当前章节；`aria-expanded` 跟随状态，折叠状态存在
  `localStorage` 的 `torch:rail-groups:<view>`（如 `torch:rail-groups:life`），默认展开。
- 子章节缩进显示；打开某章节时自动展开它所属的分组，当前章节不会藏在折叠里；
  方向键只在可见章节间移动，折叠分组里的章节会被跳过。
- 窄屏（章节轨横排）时分组铺平成一行，分组标题仍是折叠开关。
- 设置页的「模板结构」预览按 `chapters` 平铺列出章节（不渲染分组行）；分组只出现在
  四库页面的章节轨里。

## 从 v1 升级到 v2（布局迁移）

2026-10-26 起 `LAYOUT_VERSION` 是 `pillars-v2`：反思复盘、目标、项目收进分组目录，其余
章节路径不变。旧库不会自动改名，只在设置 →「库结构与迁移」的预览里按
`vault_layout.V1_CHAPTER_MOVES` 逐文件规划搬迁。

| 章节 | v1 目录（只由迁移搬走） | v2 目录 |
|---|---|---|
| 反思 | `00_人生路/02_反思` | `00_人生路/02_反思复盘/01_反思` |
| 周复盘 | `00_人生路/04_周复盘` | `00_人生路/02_反思复盘/02_周复盘` |
| 月复盘 | `00_人生路/05_月复盘` | `00_人生路/02_反思复盘/03_月复盘` |
| 年度/季度/月度目标 | `00_人生路/06_目标与计划/01..03` | `00_人生路/04_目标/01..03` |
| 项目计划 | `00_人生路/06_目标与计划/05_项目计划` | `00_人生路/05_项目/01_项目计划` |
| 项目复盘 | `00_人生路/07_工作复盘/01_项目复盘` | `00_人生路/05_项目/02_项目复盘` |

- 不搬迁：日记、任务与待办、习惯追踪（`00_人生路/06_目标与计划/04_习惯追踪`）、
  会议收获、踩坑记录，以及其它三个库。
- 预览逐文件给出「源 → 目标」，理由带 `layout-v2:<章节 key>`；目标已存在且内容不同会进
  冲突列表，不会覆盖。执行仍要带预览的 `plan_hash`，先备份再移动，随后改写内部 wiki /
  相对链接、重建索引，并按 run 回滚。
- 新库直接用 v2 路径创建（模板预览与 `.torch-os/vault-layout.json` 也是 v2）；已有库
  不会自动补建新章节或搬文件，迁移后旧目录只做只读兜底、空目录保留。
- 真实库 2026-10-26 已搬：`00_人生路/06_目标与计划/05_项目计划` 下 2 个文件移到
  `00_人生路/05_项目/01_项目计划`，`00_人生路/08_任务与待办/任务/考研规划.md` 里的
  wiki 链接同步改写；移动前已备份。

## 额外目录

```text
00_人生路/08_任务与待办/任务/     # 一任务一笔记；清单 = 子文件夹
00_人生路/08_任务与待办/_系统/    # 任务（tasks）.md、待办（todos）.md、迁移记录/
.obsidian/plugins/torch/          # 插件本体 + data.json
.torch-os/                        # 索引、备份、审计、清单
.torch-os/personal-data.json      # 小组件偏好、常用城市、排序等（首次修改小组件时创建）
.torch-os/横幅背景/                # 工作台横幅图片（首次在横幅编辑器选图时创建）
.torch-os/train-tracker/          # 列车追踪时刻表（导入或从 12306 手动同步时创建）
.torch-os/love/                   # 恋爱系统数据库、加密 Key、排班表、值班备注（首次使用恋爱系统时创建）
```

`任务/` 与 `_系统/` 在「任务与待办」章节内部，章节本身也在 `CHAPTERS` 里。创建时还会
额外补建四个目录：`任务/`、`_系统/`、`.obsidian/plugins/torch/`、`.torch-os/`；其余目录
是章节路径的父目录，随章节一起出现。`.torch-os/personal-data.json`、`.torch-os/横幅背景/`
与 `.torch-os/train-tracker/` 按需创建，不预先放空文件。恋爱系统第一次打开时会在
`.torch-os/love/` 下创建 `love_analysis.db`、`xlsx/`，值班备注在首次写备注时创建；
旧版数据会从原位置复制过来，原文件保留。车次时刻表不在 `love/` 下，已独立到
`.torch-os/train-tracker/timetable.json`。

顶层规则：四库只放 Markdown 正文；系统配置、小组件个性化数据和横幅图片等非 Markdown
资源都放 `.torch-os/`。localStorage 只作为当前窗口缓存，启动时从
`.torch-os/personal-data.json` 灌入，写入后防抖回写。

## .torch-os/vault-layout.json

创建时写入的布局清单，把这次创建的四库/章节、分组与目录逐条记下来：

```json
{
  "schema": 1,
  "layout_version": "pillars-v2",
  "created_at": "2026-10-26T01:23:45+08:00",
  "source": "backend/app/vault_layout.py",
  "plugin_version": "0.99.2",
  "pillars": [
    {
      "key": "life",
      "title": "人生路",
      "dir": "00_人生路",
      "chapters": [
        { "key": "journal", "title": "日记", "path": "00_人生路/01_日记", "default_type": "journal" }
      ],
      "groups": [
        { "key": "review", "title": "反思复盘", "chapters": ["reflection", "review_week", "review_month"] }
      ]
    }
  ]
}
```

- `pillars[].chapters[]` 与 `vault_layout.CHAPTERS` 逐行对应（key / title / path /
  default_type），顺序也一致；`pillars[].groups[]` 与 `CHAPTER_GROUPS` 对应（key / title /
  章节 key 列表，只引用 key、不复制路径，只有人生路非空）；`layout_version` 与
  `plugin_version` 记录创建当时的版本。上面只展开 `life` 的一章与一个分组作示例，实际清单
  完整包含四个库与 35 章。
- 当前后端**只在创建时写这份清单，运行时不回头读它**；权威映射永远是
  `backend/app/vault_layout.py`。它的用途是给人看、给以后的迁移/校验留证据。
- 已有库不会被补写这份文件：只有「创建新的个人数据库」会生成它。

## .obsidian 预设与插件安装

| 文件 | 内容 |
|---|---|
| `.obsidian/app.json` | `{}`（不覆盖用户外观设置） |
| `.obsidian/appearance.json` | `{}` |
| `.obsidian/core-plugins.json` | 核心插件开关对象：19 个插件 id → `true`（`file-explorer`、`global-search`、`switcher`、`backlink`、`tag-pane`、`graph`、`canvas`、`properties` 等） |
| `.obsidian/community-plugins.json` | `["torch"]`（预启用） |
| `.obsidian/plugins/torch/main.js` · `manifest.json` · `styles.css` | 从仓库 `obsidian-plugin/` 复制 |
| `.obsidian/plugins/torch/data.json` | `baseUrl`、`repoRoot`（本机仓库绝对路径）、`pythonPath: ""`、`autoStart: true`、`instancePort: 0` |

在 Obsidian 里第一次打开新库：

1. 「打开文件夹作为库」，选创建结果里的路径（第一次要手动选一次）；
2. 在社区插件里信任作者并启用「薪火OS」（`community-plugins.json` 已预启用，仍需确认）；
3. 插件按当前库启动自己的后端实例：端口从 **9168** 起挑空闲端口（8765 留给浏览器模式），
   `VAULT_DIR` = 这个库，`TORCH_VAULT_SCOPED=1`，索引/备份/审计落在该库 `.torch-os/`；
4. `.obsidian\plugins` 的改动不会热加载，装/换插件后要**完全重启 Obsidian**。

插件版本取自仓库 `obsidian-plugin/manifest.json`，会同时出现在创建结果的
`plugin.version` 与清单的 `plugin_version` 里；它不随 `LAYOUT_VERSION` 变化。

## 创建的安全规则

| 情况 | 行为 |
|---|---|
| 目标不存在 | 新建文件夹并写入完整模板 |
| 目标已存在且为空 | 允许：写入模板后该目录就是新库（先确认空，再改名） |
| 目标已存在且非空 | 409，拒绝；不覆盖、不合并 |
| 目标是当前库，或当前库的上级目录 | 409，当前库永不被改动 |
| 目标在当前库内部 | 409，不能把新库嵌在当前库里 |
| 名字重复（`register_workspace: true` 且已有同名库指向别的文件夹） | 409 |
| 名字非法：空 / 超 40 字 / 含反斜杠、斜杠、冒号、星号、问号、引号、尖括号、竖线等字符 / 以空格或句点结尾 / Windows 保留名（`CON`、`PRN`、`COM1`…） | 422 |
| 父文件夹是相对路径、不存在或不是文件夹 | 422 |
| QA 隔离运行时 | 409（`vault_admin` 能力为 `false`，两个接口都不可用） |

- 注册表是仓库的 `data/workspaces.json`（`{"version": 2, "workspaces": [...]}`，`data/` 已被
  Git 忽略）。注册只记名字、文件夹与只读标记，不搬数据；同一文件夹重复注册会替换旧条目。
- 注册失败（磁盘/权限）不会回滚已建好的文件夹：返回 `warning`，`registered: false`。
- 唯一的副作用是当前库 `.torch-os` SQLite 里多一条 `vault_created` 审计（以及注册时的
  `data/workspaces.json` 条目）；当前库的笔记与目录不会被改动。
- 审计只写一条 `vault_created`：名字长度、目录数、文件数、插件版本、是否注册成功；
  不写路径、不写正文。

## API

### GET /api/vault/template

给设置页的只读模板预览；`vault_admin` 为 `false`（QA）时 409。

| 字段 | 说明 |
|---|---|
| `layout_version` | 当前 `pillars-v2` |
| `pillars` | 四个库 + 各自章节与分组（与 `GET /api/pillars` 同源） |
| `chapters` | 扁平章节表（key / pillar / title / path / default_type） |
| `directories` | 全部章节目录（排序去重） |
| `extra_directories` | `任务`、`_系统`、`.obsidian/plugins/torch`、`.torch-os` |
| `seed_files` | 种子的身份笔记路径 |
| `manifest_file` | `.torch-os/vault-layout.json` |
| `obsidian` | `.obsidian` 四个预设 + 插件 id/版本/文件 |
| `current` | 当前库路径、运行标签、是否库作用域 |
| `template_source` / `note` | 来源说明与安全边界 |

### POST /api/vault/create

```json
{ "parent_dir": "C:\\Users\\你\\Desktop", "name": "第二大脑", "register_workspace": true }
```

`parent_dir` 必须是绝对路径；`name` 会去掉首尾空白，最长 40 字符；`register_workspace`
默认 `true`。成功返回 **201**：

| 字段 | 说明 |
|---|---|
| `created` / `name` / `path` | 结果与新库绝对路径 |
| `folders` / `files` | 实际创建的目录数与文件数（新库还没有 SQLite 索引） |
| `layout_version` / `plugin` | 布局版本；插件 `version` 与安装路径 |
| `registered` / `warning` | 是否写进工作区列表；注册失败时的提示 |
| `obsidian_uri` | `obsidian://open?vault=<URL 编码的名字>`，可直接打开 |
| `next_steps` | 打开库 → 启用插件 → 回来刷新列表三步 |
| `note` | 重申不改当前库、每个库数据在自己 `.torch-os` |

错误码：422（名字非法、父文件夹是相对路径/不存在）；409（非空目录、嵌在当前库内、
目标是当前库、重名、QA 运行时、父文件夹不可写或创建失败）。

## 切换数据库

### Obsidian 插件（库作用域）

- 每个库一个后端实例：独立端口、`VAULT_DIR` 与 `.torch-os`；插件按库路径复用/启动实例。
- **切换 = 在 Obsidian 里把另一个文件夹作为库打开**。插件里没有、也不需要「切换工作区」
  按钮；设置页会显示「当前由 Obsidian 库承载」。
- 卡片下半的「切换数据库」列的是 `GET /api/workspaces`：每行有「在 Obsidian 打开」（用库
  文件夹名拼 `obsidian://open?vault=<名字>`）与「复制路径」；不在库作用域时还多一个
  「切换到此工作区」。
- 创建时注册是自动的（接口参数 `register_workspace` 默认 `true`，设置页没有单独开关）；
  新库会出现在这个列表里。
- 两个库各自用不同端口，数据不会串（插件按库复用实例）。

### 浏览器模式

- 仍按仓库 `.env` 的工作区运行。设置 →「库与工作区」点「切换到此工作区」会调用
  `POST /api/workspaces/activate`，把 `DATA_DIR`、`VAULT_DIR`、`VAULT_READ_ONLY`、
  `WORKSPACE_NAME` 写进本地 `.env`，**重启本地服务后生效**；运行中的服务不会原地换库。
- 在库作用域的插件实例里这个接口返回 409（不能在一个库的界面里切到另一个库）。
- 浏览器模式的 8765 与插件实例的 9168+ 可以同时运行，互不抢占端口。

## 修改模板

- **唯一改法**：改 `backend/app/vault_layout.py`。章节在 `CHAPTERS`
  （`(key, pillar, title, path, default_type)` 五元组），分组在 `CHAPTER_GROUPS`
  （只按章节 key 引用、不写路径），库名在 `PILLAR_TITLES` / `PILLAR_DIRS`，库顺序在
  `PILLAR_ORDER`；v1 → v2 的搬迁目录在 `V1_CHAPTER_MOVES`（只给迁移用）。
- 路径或分组结构变了就把 `LAYOUT_VERSION` 升一版（2026-10-26 已从 `pillars-v1` 升到
  `pillars-v2`）：`/api/pillars` 的章节清单、`/api/vault/template`、新库
  `vault-layout.json` 都读它。
- 创建（`ensure_layout`）、模板预览、四库页面章节表读的是同一份文件；前端没有路径表副本，
  改完刷新设置页就能看到新预览。
- **已有库不会自动跟着变**：`ensure_layout` 只在创建和迁移执行时补建**缺失的目录**，
  不会重命名已有目录，也不会补写 `.obsidian` 预设、插件或 `.torch-os/vault-layout.json`。
  要给老库换布局：先备份，在「设置 → 库结构与迁移」预览（v1 章节目录会按
  `V1_CHAPTER_MOVES` 逐文件列出）→ 执行（或手动搬文件），再自行补模板文件；
  更省事的做法是新建一个库，把笔记拷过去（新库自带完整模板）。
- 改完至少跑：`pytest tests/test_vault_admin.py tests/test_pillars.py tests/test_vault_layout.py`
  与 `node --experimental-vm-modules --test frontend/tests/rail-groups.test.mjs`。pytest 逐条核对
  创建结果与 `CHAPTERS` 一致，并守住「不碰当前库 / 不覆盖非空目录」的边界；`test_vault_layout.py`
  覆盖分组只引用章节 key 与 `V1_CHAPTER_MOVES`，rail-groups 覆盖分组行的位置与折叠渲染。

## 相关文档

- 目录与数据边界：`README.md` 的「工作区（用户文件夹）」「四库目录」。
- SQLite 表与权威源：`docs/DATA_HANDBOOK.md`。
- 功能清单与设置页行：`docs/FEATURE_MAP.md`。
- 最近验证结果：`docs/STATUS.md`。
