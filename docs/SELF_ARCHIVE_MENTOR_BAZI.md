# 自我档案 · 人生导师 · 八字系统（重构设计）

> 状态：设计已确认，进入实施前的唯一规格来源。**2026-11-03 更新：核心链已落地。**
> 日期：2026-11-01；最近核对：2026-11-03。
> 适用范围：`01_自我档案` 全部前端、后端、数据目录；旧的忆灵 / 苏格拉底 / 身份待确认 / 记忆中枢收编进本设计。
> 实施顺序见 `docs/SELF_ARCHIVE_MENTOR_BAZI_PLAN.md`。
> 如果上下文被压缩：先读本文件与 PLAN，再按 PLAN 的阶段继续，不要凭记忆重做已确认的决策。
>
> **当前实现状态（2026-11-03）**
>
> - B3-1/B3-2：角色读取边界、写入目标校验、受控动作已完成；
> - B4a：`supersede_entry` 已有按 Vault + 旧条目 id 的跨进程锁、提交前 revision 复查、
>   持久化操作记录与 `.torch-os/self-archive-ops/` 恢复；中断时不会同时显示两个当前版本；
> - B4b：导师对话与全量整理默认**直接提交**经校验的档案更新；`TORCH_MENTOR_AUTONOMOUS_ARCHIVE=0`
>   可回退为旧待确认卡；旧卡不批量自动采纳；
> - B5：每日问答先写问答记忆，再整理进档案；两阶段分别反馈；跳过不生成答案。
>
> 下文“待确认”“逐条确认”描述旧兼容模式或历史设计；默认路径已改为导师整理、用户通过对话纠正。

---

## 0. 一句话

把「自我档案」从一个笔记集合，升级为**由人生导师维护、用户可通过对话纠正、可以辩论修正的长期身份档案**；导师有自己的分层记忆，能从人生路 / 生活运营 / 知识库取材，缺失的信息用每日三问补；八字系统作为自我档案下的第三个独立系统。默认由导师整理并提交经校验的更新，旧待确认模式保留为兼容与回退路径。

---

## 1. 已确认的需求决策

| # | 决策 | 结论 |
|---|---|---|
| 1 | 档案章节 | 参考文档精简版：画像 + 11 个本子（基础事实、身体能量、行为习惯、情绪压力、动机需求、三观决策、关系边界、自我叙事、矛盾盲区、人生时间线、验证更新） |
| 2 | 旧数据 | 迁移归位；迁移前预览，备份后移动；原文保留 |
| 3 | 三部分导航 | 页面内左侧栏：自我档案（可展开本子）/ 人生导师 / 八字系统 |
| 4 | 对话与记忆 | 记忆、待确认、提问落 `01_自我档案/02_人生导师/` 的 Markdown；原始对话轮次仍存本机 SQLite |
| 5 | 每日三问 | 工作台常驻条 + 人生导师页；每天 3 条，可答可跳过 |
| 6 | 档案更新 | 「更新自我档案」按钮做全量整理；对话中导师也可产生小更新；默认经服务端校验后直接提交，旧模式可回退为待确认 |
| 7 | 八字范围 | 完整八字系统：四柱/十神/藏干/五行/大运/流年/AI 解读/历史；复用 `lunar_python` |
| 8 | 旧忆灵 | 收编保留：入口改名人生导师并指向新页；旧观察/复盘卡/身份候选留作历史，不再产出 |
| 9 | 记忆结构 | 分层：核心记忆 + 记忆时间线 + 问答记忆；三库按需检索 |
| 10 | 旧身份卡 | 保留称呼/座右铭/横幅；世界观/人生观/价值观迁移到「三观与决策」本子，新档案为唯一权威 |
| 11 | 回收站样式 | 只改工作台 → 回收中心页面 |
| 12 | 导师聊天 | 多会话 + 新对话；默认一个长期对话，可另开话题 |

---

## 2. 设计原则

1. **档案是唯一权威，导师是唯一写入者。** torch 界面不提供档案的手动编辑/删除；Obsidian 或直接打开文件夹可以改。所有应用内写入都必须来自人生导师，并经过用户确认。
2. **五类信息 + 置信度。** 每个条目必须标注：`fact` 事实 / `behavior` 行为证据 / `inference` 推断 / `narrative` 自我叙事 / `hypothesis` 待验证假设；置信度 `high` / `medium` / `low`。没有证据的抽象标签不写入。
3. **第三人称理解，第二人称对话。** 导师在整理档案时用「他/她」描述用户；在聊天时直接对用户说话，但依据来自第三人称档案。
4. **允许空白，允许修正。** 不确定就留空；旧结论可以被新条目取代（supersede），不删历史。
5. **文件即数据。** 自我档案下所有私人数据都在 `01_自我档案/` 内，Markdown 可读、可在 Obsidian 编辑；SQLite 只存原始对话与运行状态。
6. **文件夹与前端一一对应。** 左栏的每个本子/页面都有确定的文件夹或文件；不在隐藏目录里制造第二套真相。
7. **AI 输出是数据不是指令。** 模型返回的 JSON 必须校验；非法结构不落盘；档案写入前再次检查 revision。
8. **不诊断、不贴病理标签。** 命理、星座、MBTI 只作为「自我叙事」层，不当结论；八字有明确免责声明。

---

## 3. 信息架构

### 3.1 页面结构

```
自我档案（主应用模块）
├── 顶部标题（回归最顶部，复用 .pillar-head）
│   ├── 四库 · 自我档案 / 自我档案 / 把经历沉淀成「我是谁」
│   └── 右侧：搜索、更新自我档案、在 Obsidian 打开
└── self-shell（页面内左侧栏 + 内容区）
    ├── 左栏
    │   ├── 自我档案（可展开）
    │   │   ├── 档案总览（本子网格 + 下方画像）
    │   │   ├── 基础事实
    │   │   ├── 身体与能量
    │   │   ├── 行为与习惯
    │   │   ├── 情绪与压力
    │   │   ├── 动机与需求
    │   │   ├── 三观与决策
    │   │   ├── 关系与边界
    │   │   ├── 自我叙事
    │   │   ├── 矛盾与盲区
    │   │   ├── 人生时间线
    │   │   └── 验证与更新
    │   ├── 人生导师（可展开）
    │   │   ├── 对话
    │   │   ├── 记忆
    │   │   ├── 待确认（数字角标）
    │   │   └── 今日提问（数字角标）
    │   └── 八字系统（可展开）
    │       ├── 我的命盘
    │       ├── AI 解读
    │       └── 历史
    └── 内容区
```

- 顶部标题必须在所有内容之上；现状的 `self-hub` 在标题上方的问题随本次重构移除。
- 左栏是可展开/折叠的两级结构，折叠状态存 `localStorage`（键 `torch:self-rail`），不写数据库。
- 「档案总览」是自我档案组的默认页：先本子网格，再下方「现状画像」。画像不再置顶。
- 「人生导师」和「八字系统」是独立页面，不混在档案本子里。
- 主应用侧边栏仍然只有「自我档案」一个一级入口；不把三部分塞进主侧边栏。

### 3.2 数据目录（与前端一一对应）

```
01_自我档案/
├── 01_档案/
│   ├── 身份（identity）.md          # 署名称呼/座右铭/横幅配置（横幅继续用）
│   ├── 00_画像.md                   # 现状画像（生成物；覆盖前备份）
│   ├── 01_基础事实/                 # self_facts
│   ├── 02_身体与能量/               # self_body
│   ├── 03_行为与习惯/               # self_behavior
│   ├── 04_情绪与压力/               # self_emotion
│   ├── 05_动机与需求/               # self_motivation
│   ├── 06_三观与决策/               # self_values
│   ├── 07_关系与边界/               # self_relationship
│   ├── 08_自我叙事/                 # self_narrative
│   ├── 09_矛盾与盲区/               # self_paradox
│   ├── 10_人生时间线/               # self_timeline
│   └── 11_验证与更新/               # self_experiments
├── 02_人生导师/
│   ├── 记忆/
│   │   ├── 核心记忆.md              # 每次对话都注入的精简长期记忆
│   │   ├── 记忆时间线.md            # 按日期追加的重要事件/决定/变化
│   │   └── 问答记忆.md              # 每日三问与对话中问答的记录
│   ├── 待确认/
│   │   ├── 档案补丁/                # 待用户确认的档案修改
│   │   ├── 已采纳/
│   │   └── 已驳回/
│   ├── 提问/
│   │   ├── 题库.md                  # 人工可编辑的固定题库
│   │   ├── 动态题库.md              # 全量整理时补充的缺口问题
│   │   ├── YYYY-MM-DD.md            # 当天的 3 张问题卡（含拟答/状态）
│   │   └── 历史/                    # 过去的问题卡（按日期）
│   └── 历史/
│       ├── 画像/                    # 画像快照（每次更新前的旧版）
│       └── 旧忆灵观察/              # 从 08_忆灵观察 迁移来的历史
└── 03_八字系统/
    ├── 命盘.md                      # 出生信息 + 四柱 + 口径（可编辑）
    ├── 解读/                        # 每次 AI 解读一篇 Markdown
    └── 历史/                        # 旧版命盘/解读备份（迁移用）
```

- `01_自我档案/01_档案/` 是本子根目录；11 个本子文件夹固定存在，不能删除。
- `00_画像.md` 与 `身份（identity）.md` 是特殊文件，不属于任何本子文件夹。
- 旧目录 `01_内核与三观`、`02_记忆与自传`、`03_想法与灵感`、`04_情感与关系`、`05_意义与目标`、`06_沉淀与创作`、`07_我的八字参考`、`08_忆灵观察` 在迁移完成后不再写入；备份放 `01_自我档案/99_重构备份-YYYYMMDD/`，确认无误后可手动删除。

### 3.3 前端模块（新增/替换）

| 文件 | 作用 |
|---|---|
| `frontend/self.js` | 自我档案三部分外壳、左栏、路由、加载 |
| `frontend/self-archive-model.js` | 纯函数：本子定义、条目分组、画像渲染、徽章 |
| `frontend/self-archive.js` | 档案总览 / 本子页 / 条目卡片 / 辩论入口 |
| `frontend/mentor-model.js` | 纯函数：对话消息、待确认卡片、问题卡、记忆摘要的 markup |
| `frontend/mentor.js` | 人生导师页：会话列表、聊天、待确认、记忆、今日提问 |
| `frontend/mentor-strip.js` | 工作台常驻条（替换 memory-strip） |
| `frontend/self-bazi-model.js` | 纯函数：命盘渲染、五行强度、大运/流年、历史行 |
| `frontend/self-bazi.js` | 八字系统页面 |
| `frontend/self.css` | 以上所有样式（或并入 styles.css，优先独立文件） |

替换/删除：
- `frontend/memory-hub.js` 不再被 `app.js` 初始化；保留文件与后端接口作为旧数据读取兼容。
- `frontend/memory-strip.js` 由 `mentor-strip.js` 替代。
- `frontend/pillars.js` 不再渲染 `self` 模块；`self-view` 走 `self.js`。
- `frontend/index.html` 的 `self-view` 改为 `self-shell` 容器；工作台的 `workbench-memory` 改为 `workbench-mentor`。
- `frontend/app.js` 的 `PANE_LOADERS["self-view"]` 改为 `initSelf()/loadSelf()`；`GO_TARGETS` 的 `identity/profile/memory/bazi` 指向 `self` 并带子页。

---

## 4. 自我档案

### 4.1 本子清单

| 顺序 | key | 文件夹 | 标题 | 副标题 | 主要条目类型 | 参考章节 |
|---|---|---|---|---|---|---|
| 0 | `self_portrait` | `00_画像.md` | 现状画像 | 缩略版「我是谁」 | 生成报告 | 总览 |
| 1 | `self_facts` | `01_基础事实/` | 基础事实 | 时间、钱、居住、身份、结构性处境 | fact / behavior | L1 |
| 2 | `self_body` | `02_身体与能量/` | 身体与能量 | 睡眠、运动、饮食、身体信号、能量账本 | fact / behavior / inference | L2 |
| 3 | `self_behavior` | `03_行为与习惯/` | 行为与习惯 | 守时、回复、拖延、消费、压力动作 | behavior / fact | L3 |
| 4 | `self_emotion` | `04_情绪与压力/` | 情绪与压力 | 底色、触发点、防御、冲突修复 | behavior / inference / hypothesis | L4 |
| 5 | `self_motivation` | `05_动机与需求/` | 动机与需求 | 驱动力、最怕什么、奖励/惩罚、反复抱怨 | inference / narrative / fact | L5 |
| 6 | `self_values` | `06_三观与决策/` | 三观与决策 | 世界观、人生观、价值排序、红线、决策规则 | narrative / fact / inference | L6 |
| 7 | `self_relationship` | `07_关系与边界/` | 关系与边界 | 信任、付出、边界、依恋、冲突修复 | behavior / inference / fact | L7 |
| 8 | `self_narrative` | `08_自我叙事/` | 自我叙事 | 标签、人生故事、自述与行为对照 | narrative | L8 |
| 9 | `self_paradox` | `09_矛盾与盲区/` | 矛盾与盲区 | 自述不一致、反复卡住、他人反馈、待验证假设 | inference / hypothesis | L9 |
| 10 | `self_timeline` | `10_人生时间线/` | 人生时间线 | 阶段、关键选择、后悔与骄傲 | fact / narrative | L10 |
| 11 | `self_experiments` | `11_验证与更新/` | 验证与更新 | 验证实验、更新记录、定期复盘 | hypothesis / behavior / fact | L11 |

本子定义是硬编码的：不得在应用内重命名、删除、移动根目录；用户可在 Obsidian 里改正文，但不能改前端定义的 key/文件夹映射。

### 4.2 条目模型

一个条目 = 一篇 Markdown，放在对应本子文件夹下。文件名：`条目-YYYYMMDD-HHMMSS-<短id>.md`。

```yaml
---
type: self_archive_entry
entry_id: ent_ab12cd34ef56
section: self_values
section_title: 三观与决策
entry_type: inference        # fact | behavior | inference | narrative | hypothesis
confidence: medium           # high | medium | low
status: active               # active | superseded | archived
title: 被临时改计划时容易烦躁
evidence: 近三个月 3 次改计划后先沉默、回复变慢；朋友反馈「变脸明显」
counterexample: 对方生病等不可控原因时接受度较高
verify: 下次改计划时记录身体反应、回复延迟、是否表达不满
sources: ["S1", "S2"]
origin: mentor               # mentor | question | migration | user-answer
supersedes: ""               # 被本条目取代的旧 entry_id
created: 2026-11-01T12:00:00+08:00
updated: 2026-11-01T12:00:00+08:00
revision: abcd1234
---

# 被临时改计划时容易烦躁

我在被临时改计划时容易烦躁。证据是……反例是……待验证的是……
```

规则：
- 正文是完整陈述；`title` 是短标签，用于列表。
- `entry_type` 五类；抽象标签必须带 `evidence`，否则不写入。
- `confidence` 低/中/高；没有跨情境证据不写「高」。
- 修正不删除旧条目：新条目 `supersedes` 旧 `entry_id`，旧条目 `status: superseded`，默认列表只显示 active；「看历史」显示全部。
- `revision` 是文件内容哈希（沿用 `markdown_store.revision`），用于并发/冲突检测。
- 用户可以在 Obsidian 里直接改正文或 frontmatter；应用重新读取时以文件为准。非法 frontmatter 的条目按「未分类」显示但不参与 AI 上下文。

### 4.3 画像

`01_档案/00_画像.md`：

```yaml
---
type: self_portrait
updated_at: 2026-11-01T12:00:00+08:00
revision: abcd1234
sources: ["S1", "S3"]
confidence: 0.6
---
# 现状画像

一句话画像：……
当前状态：……
反复出现的模式：……
最近的关键变化：……
待验证：……
```

- 长度上限 1200 字；它是缩略版，不复制本子细节。
- 生成时机：只「更新自我档案」或用户手动刷新时；不随聊天自动改。
- 覆盖前把旧版复制到 `02_人生导师/历史/画像/画像-YYYYMMDD-HHMMSS.md`。
- 页面位置：档案总览的本子网格**下方**，不置顶。
- 画像不可在 torch 手动编辑；可在 Obsidian 改，但下一次生成会覆盖（历史里保留旧版）。

### 4.4 读取与展示

- 档案总览：11 个本子卡片（标题、条目数、最近更新、待确认角标）；下方画像。
- 本子页：按条目类型分组（事实/行为/推断/叙事/假设），每条显示标题、置信度徽章、正文摘要、证据、来源；展开看全文。
- 每条右上角提供「和导师辩论这条」：切到人生导师对话，预填「我觉得「标题」这条不对，因为……」，并带上条目 ID；不直接改档案。
- 本子页顶部：本子标题、条目数、最后更新、「在 Obsidian 打开文件夹」。
- 不提供新建/编辑/删除按钮；只提供「和导师说」「在 Obsidian 打开」。

### 4.5 写入规则

1. 只有 `mentor` 角色能写档案；默认路径是对话/全量整理产生 `archive_updates`，服务端校验后直接提交。
   旧模式（`TORCH_MENTOR_AUTONOMOUS_ARCHIVE=0`）会把补丁写入 `02_人生导师/待确认/档案补丁/`，
   由用户确认后提交；旧卡不批量自动采纳。
2. 事实条目必须有明确来源；没有来源的内容只能写成推断。模型不能把自己的猜测写成用户原话。
3. 新建条目：直接写新文件，`origin` 记录来源；操作有幂等键，重复请求只应用一次。
4. 取代条目：按 Vault + 旧条目 id 加锁，提交前重读 `revision` 与 `base_revision`；不一致返回 409，
   不覆盖外部编辑。旧条目保留为 `superseded`，新条目成为当前版本。
5. 多文件中断：`.torch-os/self-archive-ops/` 保存最小操作元数据；恢复只在旧 revision 未变时完成，
   否则报告冲突并隐藏未完成的当前版本。只读模式不执行恢复写入。
6. 审计只记录本子 key、条目 id、动作、来源数，不记录正文。
7. `VAULT_READ_ONLY=1` 时所有写入返回 409。

### 4.6 辩论机制

- 用户不同意某条：在档案条目上点「和导师辩论这条」，或在对话里直接说。
- 导师规则：
  1. 先复述它为什么这么写（引用来源）；
  2. 追问具体反例/情境/时间；
  3. 只有用户给出足够具体的反例，或指出来源本身错误，才输出 `archive_verdict.decision = "accept"`；
  4. 没被说服时输出 `reject` 或 `undecided`，并说明还缺什么证据；
  5. 绝不因为用户重复或生气而改口。
- `accept` 时输出替代条目：本子、旧条目 id、新文本、类型、置信度、证据、反例、待验证、理由、来源。
- 默认路径：后端校验来源与条目版本后直接提交，旧条目变 `superseded`；对话内显示本轮
  事实/推断、依据、操作 ID 与历史链接。旧模式回退时补丁进入待确认，可改文本后采纳或驳回。
- 每日三问：先写问答记忆，再进入同一档案链。用户原答与用户采纳/改后的拟答分别标注来源；
  未采纳的拟答不会变成用户事实；没有回答不会生成答案；跳过不产生补丁或欠账。

### 4.7 迁移

#### 4.7.1 映射

| 旧路径 | 新位置 | 条目类型 | 备注 |
|---|---|---|---|
| `01_内核与三观/身份（identity）.md` | `01_档案/身份（identity）.md` | — | 保留 name/motto/mottos/hero；worldview/lifeview/values/notes 另建条目 |
| `01_内核与三观/` 其它笔记 | `06_三观与决策/` | narrative | 原文保留 |
| `02_记忆与自传/` | `10_人生时间线/` | narrative | 原文保留 |
| `03_想法与灵感/` | `09_矛盾与盲区/` | hypothesis | 原文保留；不确定内容标低置信度 |
| `04_情感与关系/` | `07_关系与边界/` | narrative | 原文保留 |
| `05_意义与目标/` | `05_动机与需求/` | narrative | 原文保留 |
| `06_沉淀与创作/01_工作方法/` | `03_行为与习惯/` | behavior | 原文保留 |
| `06_沉淀与创作/02_职业原则/` | `06_三观与决策/` | narrative | 原文保留 |
| `07_我的八字参考/我的八字参考.md` | `03_八字系统/命盘.md` + `解读/我的八字参考.md` | — | 解析 frontmatter 四柱/出生信息；正文作为一篇解读 |
| `07_我的八字参考/八字速查卡.md` | `03_八字系统/解读/八字速查卡.md` | — | 原文保留 |
| `08_忆灵观察/` | `02_人生导师/历史/旧忆灵观察/` | — | 只读历史，不再产出 |
| `.torch-os/忆灵/画像.json` | `02_人生导师/历史/画像/旧忆灵画像.json` | — | 历史 |
| `.torch-os/忆灵/复盘卡/` | `02_人生导师/历史/旧忆灵复盘卡/` | — | 历史 |
| `.torch-os/身份待确认/` | `02_人生导师/历史/旧身份候选/` | — | 历史 |

#### 4.7.2 迁移流程

1. `GET /api/self/migration/preview`：返回检测到的旧目录、文件数、目标路径、冲突（同名文件）、备份位置。
2. 前端在自我档案页顶部显示迁移卡；点「预览迁移」查看清单。
3. 点「开始迁移」→ `POST /api/self/migration/apply`（`confirm=true`）：
   - 先复制整个 `01_自我档案/` 到 `01_自我档案/99_重构备份-YYYYMMDD/`；
   - 创建新目录；
   - 逐文件移动/转换；同名冲突保留原文件并编号；
   - 写 `.torch-os/self-archive-layout.json` 标记（schema、时间、moved、conflicts、backup）；
   - 迁移完成后旧目录只保留空壳/未识别文件，不删除备份。
4. 幂等：标记存在且没有旧目录时直接返回 `already_done`。
5. 迁移不自动清空身份卡的三观字段；只在新档案里生成对应条目。清空动作单独由「更新自我档案」或用户手动完成（默认保留，避免误删）。
6. 迁移失败不破坏原文：所有移动先复制后删除；任何异常停止并返回已完成的清单。

> 迁移是「预览 + 确认」模式，不在启动时静默搬文件。

---

## 5. 人生导师

### 5.1 角色

`dsh/roles/mentor.md`（由 `yiling.md` 改名而来）：

```yaml
---
id: mentor
name: 人生导师
agent: mentor
layer: identity
entry: [self.mentor, self.archive]
modes: [converse, synthesize, bazi, question_draft]
context:
  archive: full
  core_memory: full
  recent_journal_days: 30
  tasks: open
  habits: 30d
  max_chars: 40000
proposals: [archive_patch, memory_note, hub_event]
session: per_thread
timeout_s: 300
tools: none
---
```

- `converse`：大聊天；第二人称；依据档案/记忆/三库回答；可产出 `archive_verdict`、`memory_notes`、`questions`。
- `synthesize`：全量整理；第三人称；产出画像、档案补丁、记忆更新、缺口问题。
- `bazi`：个人八字解读；只读命盘 + 档案摘要 + 核心记忆；产出结构化解读。
- `question_draft`：为今天 3 个问题生成拟答；只读三库与档案，不写档案。
- `ENTRIES` 新增 `self.mentor`、`self.archive`、`self.bazi`；保留 `self.memory`、`self.socrates` 作为旧数据兼容。

### 5.2 对话页

布局：

```
人生导师
├── 顶部：标题 + 副标题 + [新对话] [更新自我档案] [刷新]
└── 三栏
    ├── 左：会话列表（最近 N 条，按更新时间；旧忆灵会话单独分组）
    ├── 中：消息流（Markdown + 来源折叠 + 补丁卡片 + 提问卡片）
    └── 右：上下文面板（可收起）
        ├── 今日提问（3 条，角标）
        ├── 待确认（档案补丁，角标）
        ├── 记忆（核心/时间线/问答 摘要 + 在 Obsidian 打开）
        └── 本次对话用到的资料（sources）
```

- 会话：`ai_threads.role_id = mentor`，`mode = converse`；「新对话」清空当前状态，下一条消息创建新 thread。
- 会话列表接口：扩展现有 `GET /api/ai/threads` 支持 `role_id` 过滤；旧 `yiling` 会话显示为「旧忆灵对话」，只读查看（不允许继续发送）。
- 消息流：用户/AI 气泡、Markdown 渲染、来源折叠、耗时；AI 回复中的补丁/问题以卡片插入。
- 输入：多行，Ctrl/⌘+Enter 或 Enter 发送（中文输入法 composing 时不发）；发送时带上当前档案/记忆选择器。
- 失败：保留草稿；显示可读错误；不把错误当 AI 回复写入。
- 手机/窄屏：三栏折叠成「对话 + 抽屉」。

### 5.3 分层记忆

#### 核心记忆.md

- 上限 3000 字；每次 `converse` / `synthesize` / `bazi` 都注入。
- 内容：称呼、当前阶段、核心价值观/决策规则、重要关系、长期目标、反复出现的模式、雷区/边界、需要导师特别注意的事。
- 每行尽量带来源/日期，例如「当前在准备考研（2026-10，S2）」。
- 写入：全量整理时由导师重写（旧版备份到历史）；或导师在对话中明确输出 `core_memory_update` 时追加/替换对应小节（需用户确认，因为影响大）。

#### 记忆时间线.md

- 追加式；每条：日期 + 一句话事件/决定/变化 + 来源（对话 thread/turn、日记日期、用户回答）。
- 上限不强制；超过 200 条时由整理流程把旧条目压缩成季度摘要（保留原文到 `历史/`）。
- 写入：对话中导师输出 `memory_notes` 且高信号时自动追加；每日三问回答自动追加；全量整理补充。
- 格式：

```markdown
## 2026-11-01

- [决定] 决定今年先准备考研，不换工作。（来源：对话 thread_xxx / 2026-10-31 日记）
- [关系] 和母亲聊到职业选择，她支持先稳定。（来源：用户回答 q_xxx）
```

#### 问答记忆.md

- 追加式；每条：问题、回答、日期、来源、是否已整理进档案。
- 每日三问回答、对话中导师的追问回答都写这里。
- 格式：

```markdown
## q_20261101_001 · 三观与决策

- 问题：冲突时你实际怎么选？
- 回答：……
- 日期：2026-11-01
- 已整理进档案：是/否（entry_id）
```

#### 上下文预算

| 来源 | 默认 | 全量整理 |
|---|---|---|
| 档案（active 条目摘要） | 12k | 20k |
| 核心记忆 | 3k | 3k |
| 记忆时间线（最近 60 条） | 6k | 8k |
| 问答记忆（最近 40 条） | 4k | 6k |
| 最近日记（14/30 天） | 8k | 12k |
| 任务/习惯 | 3k | 4k |
| 知识库最近笔记 | 4k | 6k |
| 合计 | ~40k | ~59k |

- 不把「自我档案」全部条目原样塞进上下文；本子条目用 title + 摘要 + 来源；聊天需要细节时再按选择器读取。
- `ai_context.assemble` 新增 entry：`self_archive`、`mentor_memory`、`recent_notes`、`ops_summary`、`bazi_chart`。
- 资料编号统一 `[S1]`…，导师回答中引用来源；UI 折叠显示。

### 5.4 待确认

位置：`02_人生导师/待确认/档案补丁/*.md`；处理后移入 `已采纳/` / `已驳回/`。

卡片格式：

```yaml
---
type: mentor_pending
pending_id: pend_ab12cd34
kind: archive_patch
status: pending              # pending | accepted | rejected | superseded
created: 2026-11-01T12:00:00+08:00
section: self_values
section_title: 三观与决策
action: create               # create | supersede
entry_id: ""                 # supersede 时的旧条目 id
entry_type: inference
confidence: medium
title: 被临时改计划时容易烦躁
old_text: ""
new_text: 我在被临时改计划时容易烦躁。……
evidence: 近三个月 3 次改计划后先沉默、回复变慢
counterexample: 对方生病等不可控原因时接受度较高
verify: 下次改计划时记录身体反应与回复延迟
reason: 用户给出了三次具体反例，导师被说服
sources: [S1, S2]
base_revision: ""
verdict: {decision: accept, reason: "……"}
---
```

UI：
- 卡片显示：本子、类型、置信度、新文本（可编辑）、旧文本（supersede 时）、证据/反例/待验证、来源、导师理由。
- 按钮：`采纳` / `改后采纳`（编辑后提交）/ `驳回`。
- 驳回后写 `已驳回/`，不删内容。
- 采纳后刷新档案本子与画像状态；工作台常驻条待确认数 -1。
- 同一 `pending_id` 只能处理一次；并发处理返回 409。

### 5.5 每日三问

#### 题库

`02_人生导师/提问/题库.md`（首次运行从内置题库生成，可手工编辑）：

```markdown
---
type: mentor_question_bank
version: 1
---

## 三观与决策

- [q_values_001] 冲突时你实际怎么选？｜类型=narrative｜优先级=90｜提示=写真实选择，不写应该
- [q_values_002] 有哪件事，是你宁可吃亏也不做的？｜类型=narrative｜优先级=85｜提示=底线比理想有用

## 关系与边界

- [q_rel_001] 你能拒绝别人吗？拒绝后要内疚多久？｜类型=behavior｜优先级=80
```

- 行格式：`- [id] 问题｜类型=...｜优先级=...｜提示=...`；解析失败的行忽略并记录日志。
- 首批题库来自参考文档附录 A，按 11 个本子分组；每本 5–10 题。
- `动态题库.md`：全量整理生成的缺口问题（带来源），格式相同，可手工删除。

#### 每日选择

- 每天第一次请求时选 3 条：
  1. 目标本子为空/薄弱的题库问题优先；
  2. 动态题库中未问过的问题；
  3. 其余随机（排除已答、已跳过冷却 7 天、永久不再问、最近 30 天问过）。
- 写入 `02_人生导师/提问/YYYY-MM-DD.md`：

```yaml
---
type: mentor_questions
date: 2026-11-01
status: open                # open | done
---
## q_20261101_001
- 问题：……
- 本子：self_values
- 类型：narrative
- 提示：……
- 来源：题库
- 拟答：……（可空）
- 拟定来源：[S1, S2]
- 状态：pending            # pending | answered | skipped | declined
```

- 当天打开导师页/工作台条读取这个文件；跨天自动创建新文件。
- 状态与冷却：以每日文件为准；`.torch-os/人生导师/state.json` 只存「最近一次生成日期 / 跳过冷却 / 永久拒绝」的快速索引，可从文件重建。
- 跳过：当天标记 skipped；7 天内不再问。
- 不再问：标记 declined；永久不再问。
- 回答：写问答记忆 + 可选写档案条目（见 5.5.1）；标记 answered。

#### 拟答

- 导师页「今日提问」里有「生成导师拟答」按钮（每天最多一次；也可在设置里开自动）。
- 后端用 `mentor/question_draft` 一次调用处理 3 条：读三库 + 档案，返回每条是否有足够证据、拟答、来源、建议条目类型。
- 有证据的显示「导师拟答」+ 来源 + `确认写入档案` / `改后确认`；没有证据的显示「库里没有，想自己答吗？」。
- 确认写入：
  - 写 `问答记忆.md`；
  - 直接创建档案条目（`origin: question`），因为用户点击确认就是确认；
  - 可选 `entry_type` 默认用问题的类型。
  - 不再进待确认，避免二次确认。
- 用户自己回答（无拟答）：先写问答记忆，再按问题的目标本子整理进档案条目；两阶段分别反馈，
  档案失败可重试且不会重复写问答记忆。

#### 工作台常驻条

- `#workbench-mentor`（替换 `#workbench-memory`）：
  - 左：`人生导师` 标签 + 今日剩余问题数；
  - 中：第一条未答问题（截断）；
  - 右：待确认数 + 「去看看」；
  - 点击切到 `self` 模块的 mentor 子页。
- 不加载模型、不生成拟答，只读今日文件与待确认计数，保证工作台快。

### 5.6 更新自我档案（全量整理）

入口：
- 自我档案顶部「更新自我档案」；
- 人生导师顶部同名按钮；
- 档案总览画像旁的「刷新」。

流程：
1. 确认对话框：说明会读取三库、调用 dsh、可能 1–3 分钟、消耗 token；只生成待确认，不自动改档案。
2. `POST /api/self/synthesize`（`confirm=true`, `range_days=14|30`）。
3. 后端组装上下文（见 5.3 预算），调用 `mentor/synthesize`。
4. 模型输出 JSON：
   ```json
   {
     "portrait": "Markdown 画像（<=1200 字）",
     "portrait_confidence": 0.6,
     "archive_patches": [
       {
         "section": "self_values",
         "action": "create|supersede",
         "entry_id": "",
         "entry_type": "narrative",
         "confidence": "medium",
         "title": "……",
         "new_text": "……",
         "evidence": "……",
         "counterexample": "……",
         "verify": "……",
         "reason": "……",
         "sources": ["S1"]
       }
     ],
     "memory_updates": [
       {"kind": "timeline|core", "text": "……", "sources": ["S2"]}
     ],
     "question_candidates": [
       {"section": "self_relationship", "prompt": "……", "entry_type": "behavior", "priority": 60, "why": "……", "sources": ["S3"]}
     ],
     "gaps": ["self_body"]
   }
   ```
5. 后端校验：
   - 画像写 `00_画像.md`（旧版备份）；
   - `archive_patches` 写待确认卡片（去重：同 section+title+new_text 不重复）；
   - `memory_updates` 追加到记忆时间线/核心记忆（核心记忆整体替换需确认；timeline 直接追加）；
   - `question_candidates` 追加到动态题库；
   - 返回计数、画像、来源、truncated。
6. 前端：成功后 toast「画像已更新，N 条补丁待确认」，跳到待确认 tab；失败保留旧画像，显示错误。
7. 如果 AI 不可用：返回 503，提示先配置 dsh；不写任何文件。

### 5.7 提示词要点

`converse`：
- 你是这个人的长期人生导师；你记得他的档案、记忆与最近生活。
- 回答先接住问题，再给判断；需要时引用档案/日记，用「我记得你之前写过……」自然表达，保留来源编号。
- 你以第三人称理解他，但对他用第二人称说话。
- 不诊断、不贴病理标签；不说教；一次只问一个关键问题。
- 当他说档案某条不对：
  1. 先复述你为何这么写；
  2. 追问具体情境、时间、反例；
  3. 只有足够具体、能推翻来源或形成更强解释时，才 `archive_verdict.decision = "accept"`；
  4. 没被说服就 `reject`/`undecided`，说明还缺什么；
  5. 不因为情绪、重复、威胁而改口。
- 发现值得长期记住的事，输出 `memory_notes`（kind=observation/decision/relationship/goal_change/correction）。
- 需要更多信息时，输出 `questions`（最多 2 条），不要一次列清单。
- 输出 JSON（自然语言放 `reply`）：
  ```json
  {
    "reply": "……",
    "sources": ["S1"],
    "archive_verdict": {
      "decision": "accept|reject|undecided",
      "section": "self_values",
      "entry_id": "ent_xxx",
      "action": "create|supersede",
      "entry_type": "inference",
      "confidence": "medium",
      "title": "……",
      "old_text": "……",
      "new_text": "……",
      "evidence": "……",
      "counterexample": "……",
      "verify": "……",
      "reason": "……",
      "sources": ["S1"]
    },
    "memory_notes": [{"kind": "decision", "text": "……", "sources": ["S1"]}],
    "questions": [{"prompt": "……", "target_section": "self_body"}]
  }
  ```

`synthesize`：
- 第三人称；只写有证据的；画像只写缩略版；补丁必须给 section/type/confidence/evidence/sources；没有证据写 gaps，不补脑。
- 不要把「这一次的心情」写成「他就是这样的人」。
- 不直接改档案；只输出 JSON。

`bazi`：
- 本地排盘是权威；不改四柱/十神/藏干/大运。
- 结合档案与记忆做个性化解读，但明确哪些是命理推断、哪些是现实证据。
- 不宿命论、不预测灾祸、不做医疗/投资决策；结尾边界声明。

### 5.8 与三库的关系

- 人生路：最近日记、反思、目标、项目；默认 30 天。
- 生活运营：未完成任务、习惯、健康摘要、财务摘要（可选；默认带任务/习惯，健康/财务仅在用户开启后带）。
- 知识库：最近 30 天笔记 + 与当前问题相关的搜索结果；不整库全塞。
- 所有资料都是只读；导师不能通过工具改三库；只能生成档案补丁/记忆。
- 三库里已经写过的内容不重复搬进档案；档案里用「引用 + 摘要 + 来源」指向原文。

---

## 6. 八字系统

### 6.1 数据

`03_八字系统/命盘.md`：

```yaml
---
type: bazi_profile
name: 王衡岳
sex: male                  # male | female
birth_date: 2003-09-10
birth_time: "00:00"        # 24 小时制；未知按 12:00 并提示
birth_place: 江西赣州章贡区
timezone: Asia/Shanghai
pillars_manual: ""         # 可选：癸未 辛酉 丙戌 戊子；填了优先于生日排盘
created: 2026-11-01
updated: 2026-11-01
---
# 我的命盘

（可写自己的理解；AI 不修改这里）
```

- 命盘由后端 `lunar_python` 计算；`pillars_manual` 有值时优先使用。
- 出生时间未知时按 12:00 排盘，并在 UI 顶部显示「时柱仅供参考」。
- 旧 `我的八字参考.md` 的四柱/日主/格局/喜忌迁入此文件的 frontmatter/正文，正文另存为一篇解读。

`03_八字系统/解读/`：
- 每次生成一篇：`YYYYMMDD-HHMMSS-<主题>.md`。
- frontmatter：`type: bazi_reading`, `topic`, `title`, `date`, `chart_revision`, `sources`.
- 正文：AI 结构化解读（Markdown）。
- 历史列表读取该文件夹；删除进回收/备份，不直接 `rm`（用回收站或重命名 `.deleted`）。

### 6.2 排盘引擎

- 从 `backend/app/love_bazi.py` 抽出共享模块 `backend/app/bazi_engine.py`：
  - 天干地支/五行/藏干/十神表、`ten_god`、`pillar`、`normalize_pillars`、`person_from_birth`、`dayun` 计算。
  - `love_bazi.py` 改为 import 共享模块，保持现有 API 与测试不变。
- 新增 `backend/app/self_bazi.py`：
  - 读取/写入 `命盘.md`；
  - `chart_from_profile()`；
  - AI 解读（`mentor/bazi`，run_once）；
  - 解读历史读写。
- `lunar_python` 已在依赖中；不新增依赖。

### 6.3 页面

布局（参考 ziwei + love bazi）：

```
八字系统
├── 顶部：我的命盘 / 出生信息 / [编辑出生信息] [生成解读]
├── 左/主：
│   ├── 命盘卡：四柱（年/月/日/时），天干地支五行色，十神，藏干
│   ├── 五行强弱条（金木水火土）+ 日主/身强弱/格局/喜忌
│   ├── 大运时间轴（当前大运高亮）
│   └── 流年表（未来 10 年，tone: up/down/flat）
└── 右/侧栏（380px）：
    ├── 主题按钮：综合 / 性格与天赋 / 事业与学业 / 财富 / 感情 / 健康 / 大运流年
    ├── AI 解读输出（Markdown）
    ├── 追问输入（可选，进入导师对话并带命盘上下文）
    └── 边界声明
```

- 输入：姓名、性别、公历生日、出生时间、出生地；`<details>` 可直接填四柱；草稿存 localStorage。
- 排盘展示复用 love bazi 的 `.bazi-*` 样式思路，但独立命名 `self-bazi-*`，避免影响恋爱系统。
- 历史：解读列表（日期/主题/摘要），点击查看、删除（确认）、导出 HTML。
- 移动端：命盘纵向滚动，解读侧栏变底部抽屉。

### 6.4 AI 解读

- 角色：`mentor`，mode `bazi`；entry `self.bazi`。
- 上下文：本地命盘（必带）+ 核心记忆 + 档案摘要（可选开关，默认开）+ 用户选的主题。
- 输出 JSON：
  ```json
  {
    "title": "……",
    "summary": "……",
    "sections": [{"title": "……", "body": "……"}],
    "favor": ["木", "火"],
    "avoid": ["金", "水"],
    "years": [{"year": "2026", "gz": "丙午", "text": "……", "tone": "up|down|flat"}],
    "advice": [{"title": "……", "body": "……"}],
    "boundary": "八字是传统文化参考，不替代现实决策。"
  }
  ```
- 不修改四柱/十神/藏干/大运；`favor/avoid` 只作为AI解读，不覆盖本地计算。
- 每次生成写一篇 Markdown；历史可回看。
- 明确免责声明：不是诊断、不是投资/婚姻决策依据。

---

## 7. 顶部标题、生活运营图标、回收中心

1. **自我档案顶部标题回归顶部**：`index.html` 的 `self-view` 改为：
   ```html
   <section id="self-view" class="view pillar-view" data-module="self" hidden>
     <div id="self-shell" class="self-shell">
       <header id="self-head" class="pillar-head">...</header>
       <div id="self-body" class="self-body"></div>
     </div>
   </section>
   ```
   header 由 `self.js` 渲染在最上方；旧 `self-hub` 移除。
2. **生活运营图标**：`PILLAR_META.ops.icon` 从 `生` 改为 `营`；`index.html` 主侧边栏的 `<span aria-hidden="true">生</span>` 也改为 `营`。
3. **回收中心**：`index.html` 的 `recycle-view` 头部改成 `.pillar-head` 结构（eyebrow `四库 · 回收中心`，title `回收中心`，subtitle 说明，右侧刷新按钮）；不再用 `.recycle-head` 的独立样式，或保留类名但套 pillar-head 样式。

---

## 8. API 设计

### 8.1 自我档案

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/self/overview` | 本子计数、画像、待确认数、今日问题数、迁移状态 |
| GET | `/api/self/books/{book_key}` | 本子信息 + active 条目（`?history=1` 含 superseded） |
| GET | `/api/self/entries/{entry_id}` | 单条 + 来源 + 历史版本 |
| GET | `/api/self/migration/preview` | 迁移预览 |
| POST | `/api/self/migration/apply` | `confirm=true`；备份、移动、标记 |
| GET | `/api/self/pending` | 待确认卡片列表 |
| POST | `/api/self/pending/{pending_id}/accept` | `confirm=true`, `new_text?` |
| POST | `/api/self/pending/{pending_id}/reject` | 归档到已驳回 |
| POST | `/api/self/synthesize` | `confirm=true`, `range_days=14|30` |

### 8.2 人生导师

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/mentor/overview` | 记忆摘要、待确认数、今日问题、最近会话、上次画像 |
| GET | `/api/mentor/questions/today` | 今天 3 卡（无则创建） |
| POST | `/api/mentor/questions/draft` | `confirm=true`；生成导师拟答 |
| POST | `/api/mentor/questions/{id}/answer` | `confirm=true`, `text`, `accept_suggested?` |
| POST | `/api/mentor/questions/{id}/skip` | `forever=false` |
| GET | `/api/mentor/memory` | 核心/时间线/问答的路径与内容摘要 |
| GET | `/api/mentor/pending` | 同 `/api/self/pending` 的别名（前端统一从 mentor 取） |
| GET | `/api/mentor/threads` | `role_id=mentor` 的会话列表 |
| POST | `/api/ai/chat` | 复用；role_id=mentor, mode=converse；返回 `pending` 卡片 |
| GET | `/api/ai/threads?role_id=mentor` | 扩展现有接口 |

### 8.3 八字

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/self/bazi/profile` | 命盘 + 计算后的 chart |
| PUT | `/api/self/bazi/profile` | 保存出生信息/四柱（revision 校验） |
| POST | `/api/self/bazi/chart` | 用表单值试算（不保存） |
| POST | `/api/self/bazi/generate` | `confirm=true`, `topic`；AI 解读并写 Markdown |
| GET | `/api/self/bazi/readings` | 解读历史 |
| GET | `/api/self/bazi/readings/{name}` | 单篇 |
| DELETE | `/api/self/bazi/readings/{name}` | 删除（确认；备份） |
| GET | `/api/self/bazi/readings/{name}/html` | 导出 HTML（可选） |

### 8.4 兼容

- 旧 `/api/memory-hub/*`、`/api/identity/*`、`/api/questions/*` 保留，但新 UI 不调用；文档标为旧接口。
- 旧 `ai_threads.role_id = yiling` 的会话在导师页显示为历史，不可继续发送。
- 旧 `/api/ai/chat` 角色 `yiling` 不再注册；如果旧客户端调用，返回 404/提示改用 mentor。

---

## 9. 后端模块

| 文件 | 内容 |
|---|---|
| `backend/app/self_archive.py` | 本子定义、条目读写、revision、画像、待确认、synthesize 编排 |
| `backend/app/mentor_memory.py` | 核心/时间线/问答记忆读写 |
| `backend/app/mentor_questions.py` | 题库解析、每日选择、拟答、回答/跳过 |
| `backend/app/mentor_pending.py` | 待确认卡片的 Markdown 存储与状态机 |
| `backend/app/self_migration.py` | 旧档案预览/迁移/备份/标记 |
| `backend/app/bazi_engine.py` | 从 love_bazi 抽出的共享排盘引擎 |
| `backend/app/self_bazi.py` | 个人命盘 profile、解读生成、历史 |
| `backend/app/ai_context.py` | 新增 selectors：self_archive / mentor_memory / recent_notes / ops_summary / bazi_chart |
| `backend/app/ai_schemas.py` | 新增 ArchiveVerdict / MemoryNote / QuestionDraft / SynthesisResult |
| `backend/app/ai_roles.py` | ENTRIES、mentor 默认选择器、chat 返回 pending、threads 过滤 |
| `backend/app/application.py` | 注册新路由 |
| `backend/app/vault_layout.py` | 新自我档案章节、legacy 旧章节、ensure_self_layout |
| `backend/app/identity.py` | 身份文件新路径 `01_自我档案/01_档案/身份（identity）.md`，旧路径只读兜底 |
| `dsh/roles/mentor.md` | 人生导师角色（替换 yiling.md） |

---

## 10. 前端模块

| 文件 | 内容 |
|---|---|
| `frontend/self.js` | 三部分外壳、左栏、子页路由、加载与事件委托 |
| `frontend/self-archive.js` | 总览/本子/条目/画像/迁移卡 |
| `frontend/self-archive-model.js` | 本子定义、条目分组、画像与徽章 markup（纯函数，node --test） |
| `frontend/mentor.js` | 导师页三栏、会话、消息、待确认、记忆、今日提问 |
| `frontend/mentor-model.js` | 消息/卡片/问题 markup（纯函数） |
| `frontend/mentor-strip.js` | 工作台常驻条 |
| `frontend/self-bazi.js` | 八字页 |
| `frontend/self-bazi-model.js` | 命盘/五行/大运/流年 markup（纯函数） |
| `frontend/self.css` | 以上样式 |
| `frontend/index.html` | self-view、workbench-mentor、recycle-head、生活运营图标 |
| `frontend/app.js` | self 加载器、GO_TARGETS、初始化新模块、移除 memory-hub/strip |
| `frontend/pillars.js` | self 分支移除/改为不渲染；ops icon 改营 |
| `frontend/recycle-center.js` | 不需要逻辑改动；头部在 index.html |

---

## 11. 旧功能收编

| 旧功能 | 处理 |
|---|---|
| 忆灵 `memory_hub` 画像/复盘卡/事件 | 不再产出；旧数据迁移到 `02_人生导师/历史/旧忆灵观察/`；`/api/memory-hub` 保留只读兼容 |
| 苏格拉底抽屉 | 入口改名人生导师，指向新导师页；旧 `roles.js` 仍服务其他角色 |
| 身份待确认 `identity_inbox` | 不再产出；旧卡片迁移到历史；不删除 |
| 主动提问 `questions.py` | 新系统用 `mentor_questions.py`；旧接口保留兼容，不调用 |
| 身份补丁 proposals | 不再用于自我档案；旧 proposal 数据保留；新档案用 Markdown 待确认卡 |
| 工作台 `memory-strip` | 替换为 `mentor-strip` |
| 自我档案旧章节轨 | 由新的三部分左栏替代；旧本子数据迁移 |
| 身份卡表单 | 不再显示三观字段；保留 name/motto/mottos/hero 供横幅 |
| 旧 `yiling` 角色 | 新对话用 mentor；旧会话只读历史 |

---

## 12. 迁移与备份

- 备份位置：`01_自我档案/99_重构备份-YYYYMMDD/`（整棵自我档案树）。
- 标记：`.torch-os/self-archive-layout.json`。
- 迁移不删除备份；不自动清空旧身份卡三观字段（默认保留；用户可在 Obsidian 删）。
- 迁移失败：保留原文，返回已完成清单与冲突；可重跑。
- 迁移前预览：显示每个旧文件的目标本子、类型、冲突；用户确认后才执行。
- 迁移期间禁止并发写：使用后端进程内锁；`VAULT_READ_ONLY` 时只允许预览。

---

## 13. 测试

### 后端

- `tests/test_self_archive.py`：本子定义、条目读写、revision、supersede、画像备份、待确认 accept/reject/冲突。
- `tests/test_self_migration.py`：预览、备份、移动、幂等、冲突保留、旧路径兜底。
- `tests/test_mentor_memory.py`：核心/时间线/问答追加、上限、来源。
- `tests/test_mentor_questions.py`：题库解析、每日 3 条、冷却、跳过/不再问、拟答、回答写入。
- `tests/test_self_bazi.py`：命盘读写、四柱手动优先、排盘、解读历史、删除备份。
- `tests/test_ai_roles.py` 扩展：mentor 角色加载、entries、threads 过滤、archive_verdict 生成待确认。
- `tests/test_ai_context.py`：新 selectors 的预算与来源。

### 前端（node --test）

- `frontend/tests/self-archive-model.test.mjs`：本子分组、条目类型、画像渲染、待确认卡。
- `frontend/tests/mentor-model.test.mjs`：消息、问题卡、记忆摘要。
- `frontend/tests/self-bazi-model.test.mjs`：四柱渲染、五行条、大运/流年。
- 现有 `bazi-page.test.mjs` 保持恋爱八字不回归。

### 手工验收

1. 迁移预览 → 执行 → 旧数据归位、备份存在、旧目录不再写入。
2. 档案总览：本子网格 + 画像在下方；标题在最顶部。
3. 和导师辩论一条：导师先追问；给反例后生成待确认；采纳后旧条目 superseded。
4. 更新自我档案：画像更新、补丁进待确认、记忆时间线追加、缺口进动态题库。
5. 每日三问：工作台条显示 3 条；导师页可生成拟答；确认后写问答记忆 + 档案条目；跳过/不再问生效。
6. 八字：填写出生信息 → 排盘 → AI 解读 → 历史可回看/删除；既有四柱迁移正确。
7. 旧忆灵/苏格拉底入口不存在或指向新导师；旧数据可从历史查看。
8. 只读模式：所有写入 409。
9. 移动端：三栏折叠可用。

---

## 14. 风险与边界

- **上下文超长**：全量整理 59k 预算接近上限；超时/截断时保留旧画像，不写半成品；UI 显示 truncated。
- **模型不遵守 JSON**：所有输出先 `extract_json` + Pydantic 校验；失败不落盘。
- **导师幻觉**：档案补丁必须带 sources；没有来源的标 `hypothesis`/低置信度；用户确认是最后闸门。
- **Obsidian 并发编辑**：条目 revision 校验；冲突返回 409，不覆盖。
- **迁移误伤**：先备份、预览、逐文件移动、冲突保留；备份不自动删。
- **隐私**：三库内容只在调用 dsh 时发送给用户配置的模型；八字出生信息同样；文档中明确。
- **命理边界**：不宿命、不预测灾祸、不做医疗/投资/婚姻决策；八字只是参考。
- **旧功能删除风险**：先收编保留，不物理删除旧数据；新 UI 不调用旧接口。
- **多窗口**：档案写入用 revision；待确认卡状态用 pending_id 唯一性；并发处理 409。

---

## 15. 暂缓/不做

- 不做应用内档案手动编辑、删除、新建本子。
- 不做自动定时全量整理（只手动按钮；未来可加提醒）。
- 不做向量检索（先用结构化上下文 + 关键词/最近笔记；后续可接）。
- 不把每轮对话都写成 Markdown（原始对话在 SQLite；长期记忆在 Markdown）。
- 不做紫微斗数排盘（除非后续另立需求；本设计只做八字）。
- 不做自动清空旧身份卡三观字段（保留，避免误删）。
