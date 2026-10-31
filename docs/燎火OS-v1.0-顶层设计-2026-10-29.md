> 状态（2026-10-29）：本方案已获用户批准；**P0–P1 已落地并通过 `check_all`**，P2–P5 待实施。实际进展与测试基线见 `docs/STATUS.md`。

# 薪火OS（Torch OS）v1.0 顶层设计与落地计划

> 依据：`Torch OS个人管理系统 顶层设计架构图.jpg` + `torch OS v1.0 顶层设计说明书.txt`（2026-10-29）
> 当前代码：v0.99.0，四库已存在、DSH 已嵌入、日记闭环/记忆候选/身份采纳/恋爱系统已落地。
> 本计划只做方案，不改代码；确认后按 P0→P5 分批实施。

## 0. Context：为什么改、改到什么程度

v0.99 已经是“四库 + 本地 Markdown + DSH 单入口”的形态，但 AI 仍集中在「与神对话」一个 iframe 里，四库页面基本只读，记忆候选与身份修改分散在待确认中枢/提问卡/身份候选三套流程里。v1.0 要解决的是：

1. **AI 角色层**：每个板块有自己的 AI 角色，但全部基于同一个 DSH 内核与同一个 DeepSeek Key，不出现第二套模型通道。
2. **记忆中枢（忆灵）**：把“所有角色发生的事”汇总成用户画像，产出可审核的记忆卡；身份层只保留两条写入路径——忆灵审核、苏格拉底说服。
3. **四库差异**：人生路=个人写作，自我档案=我是谁/我现在怎样，知识库=外部知识与问答，生活运营=恋爱/健康/理财/家庭。
4. **可写可编**：人生路与知识库从“只读浏览”升级为“应用内 Markdown 编辑”（工具栏+实时预览），同时保留 Obsidian 编辑。
5. **可管可控**：工作台小组件可开关；AI 只提议、不自动执行；所有写入有 revision、审计与备份。

### 已确认的四个方向

| 决策 | 结论 |
|---|---|
| 人生路范围 | **写作优先**：日记/反思/心得/随笔/目标计划；读书笔记留在知识库；任务/习惯留在工作台。不采用 v3 的“三章收缩”，也不把任务迁 `.torch`。 |
| DeepSeek 问答 | **不 iframe 内嵌**：应用内提供“打开 DeepSeek 问答”外链 + 分享链接解析 + 学术导师整理；不碰网页登录态。 |
| AI 角色形态 | **页面内角色抽屉**：所有角色共用一套 drawer，后端统一走 dsh headless；DSH iframe 仍是唯一“总对话”入口。 |
| 编辑器 | **Markdown 工具栏 + 实时预览**：零新依赖、严格 CSP 兼容、Obsidian 可直接编辑；不做 WYSIWYG。 |

## 1. 总体架构

```
┌──────────────────────────────────────────────────────────────┐
│ 前端（vanilla JS，无构建）                                    │
│  工作台：小组件 + 待办 + 与神对话(iframe) + 忆灵条             │
│  四库页面：共享阅读器 + 共享 Markdown 编辑器 + 角色抽屉        │
│  提案卡片：采纳/调整/忽略 → 后端 apply                        │
└───────────────┬──────────────────────────────────────────────┘
                │ /api/ai/*  /api/memory-hub/*  /api/proposals/*  /api/writing/*
┌───────────────▼──────────────────────────────────────────────┐
│ 后端（FastAPI）                                                │
│  ai_gateway  ── dsh --profile headless --json -（stdin 喂 prompt）│
│  ai_roles    ── 角色注册表 + 会话 + 结构化输出                  │
│  ai_context  ── 按角色范围拼只读上下文（带来源编号/预算）        │
│  memory_hub  ── 事件 → 综合 → 画像 → 复盘卡 → 身份补丁         │
│  proposals   ── identity_patch/new_note/new_task/new_habit…    │
│  note_editor ── 人生路/知识库可写章节 + revision + 审计         │
└───────────────┬──────────────────────────────────────────────┘
                │
┌───────────────▼──────────────────────────────────────────────┐
│ 数据层：个人数据库文件夹（Vault）                               │
│  00_人生路 / 01_自我档案 / 02_生活运营 / 03_知识库  Markdown   │
│  .torch-os/：小组件、忆灵画像/卡片/事件、备份、AI 运行数据      │
│  SQLite：索引、记忆、会话、提案、审计（不承载正文）             │
└──────────────────────────────────────────────────────────────┘
```

### 1.1 四库定位（凸显差异）

| 库 | 一句话定位 | 内容 | AI 角色 |
|---|---|---|---|
| 人生路 | 我的写作与计划 | 日记、反思、心得、随笔、目标计划 | 写作伙伴 |
| 自我档案 | 我是谁、我现在怎样 | 身份、画像、记忆、状态观察 | 忆灵 / 苏格拉底 |
| 知识库 | 我学到了什么 | 问答文档、分享链接整理、主题/读书/课程/概念/方法/输出 | 学术导师 |
| 生活运营 | 我怎样生活 | 恋爱、健康、理财、家庭 | AI 生活助理 + 恋爱大师/私人医生/理财专家 |

### 1.2 AI 角色层：一个内核，多张角色卡

**不做多套模型/多套 Key。** 角色 = “同一 dsh 内核 + 角色卡 + 限定上下文 + 结构化输出契约”的薄层。

新增 `dsh/roles/*.md`（不是 dsh skill；skill 继续给主对话用）：

| 角色 id | 名称 | 模式 | 范围 | 可提议 |
|---|---|---|---|---|
| `yiling` | 忆灵 / 苏格拉底 | `synthesize` / `converse` | 身份、画像、记忆、近 14 天日记、任务/习惯摘要 | identity_patch、review_card、new_note、hub_event |
| `journal_companion` | 写作伙伴 | `classify` / `polish` / `discuss` | 当前笔记 + 近 7 天日记 | new_note、archive_note |
| `academic_mentor` | 学术导师 | `classify` / `polish` / `share_digest` / `tutor` | 知识库章节 + 分享链接正文 | new_note、archive_note |
| `life_assistant` | AI 生活助理 | `overview` / `tasks` / `suggest` | 任务/习惯/各专家报告摘要 | new_task、new_habit、new_note |
| `love_master` | 恋爱大师 | `advice` | 恋爱分析摘要/画像/日志标题（不含原始聊天） | new_note、new_task |
| `health_doctor` | 私人医生 | `report` | 体重/健康月度表 + 健康档案 | health_report |
| `finance_advisor` | 理财专家 | `report` | 记账月度表 + 财务摘要 | finance_report |
| 主 `dsh` | 与神对话 | 自由对话 | 全库（现有 MCP 只读工具 + AGENTS 规则） | 不经过角色网关 |

**忆灵与苏格拉底 = 同一个 agent 的两种模式**（同一角色文件、同一画像、同一身份权威）：
- 忆灵模式：观察/综合/出卡，不在对话里改身份。
- 苏格拉底模式：在自我档案里对话；只有 AI 明确“被说服”才生成身份补丁。
- UI 两个入口，权威一套；避免两个画像互相漂移，也避免“换个窗口就被说服”。

**角色卡格式**（YAML frontmatter + 分模式 prompt 段落）：

```markdown
---
id: yiling
name: 忆灵
agent: yiling
layer: identity
entry: [self.memory, self.socrates]
modes: [synthesize, converse]
context: {identity: full, portrait: true, active_memories: 30,
          recent_journal_days: 14, tasks: open, habits: 30d, max_chars: 16000}
proposals: [identity_patch, review_card, new_note]
session: per_thread
timeout_s: 180
tools: none
---
## mode: synthesize
你是忆灵……只基于给定资料，输出 synthesis JSON。

## mode: converse
你是苏格拉底式的朋友/哲学伙伴/人生导师/心理陪伴者……
```

后端 `ai_roles.py` 读取并校验：id 唯一、mode 合法、`entry` 在固定入口表、`proposals` 在固定 kind 表、`write_chapters` 必须真实存在、prompt 不写死个人信息。

**调用链**：
- `POST /api/ai/chat`（角色对话）→ `RoleService.chat()` → `ContextAssembler` 拼只读资料 → `ai_gateway.run_headless()` → 解析 NDJSON → 校验结构化输出 → 落 thread/turn、proposal、hub event。
- `POST /api/ai/run`（一次性能力：润色/判类/分享总结/报告/综合）→ 同一 gateway，无会话或短会话。
- 主 DSH iframe 完全不动；角色 drawer 与 iframe 不共享会话。

**dsh 调用细节（已核实本机 dsh 0.2.0-rc.2）**：
- 使用 `dsh --profile headless --json -`，prompt 从 **stdin** 传入（`-` 是官方支持的 stdin 读取方式），避免 Windows 多行 argv 被 cmd 解析。
- 保留 `dsh headless --json -` 兼容回退；P0 用 `--help` 探测实际可用形式。
- 每次调用：`cwd=<data_dir>/ai-runtime/workspace`（空目录，不是 Vault）；环境变量白名单复制；注入 `DEEPSEEK_API_KEY`（来自 `DeepSeekBalanceService.resolve_key()`）；移除 `TORCH_REPO_ROOT`/`VAULT_DIR`/`DATA_DIR`，防止角色进程找到真实库。
- `DSH_AGENTS_HOME=<repo>/dsh/roles`（只加载角色层 AGENTS；P0 验证 headless 是否尊重该变量；不生效则把角色规则直接写进 stdin prompt）。
- 会话 id：`session-torch-<role>-<thread_uuid>`；thread 绑定 role/mode/session；跨角色复用返回 409。
- 超时：普通 120s，综合/报告 180s；Windows 用 `taskkill /PID <pid> /T /F` 杀进程树；全局并发 ≤2，综合全局互斥。
- 日志只记元数据与脱敏 stderr 尾部；不记 prompt/回复/Key。

**上下文与输出契约**：
- `ai_context.py` 只接受枚举化选择器（`identity`/`note`/`journal`/`tasks`/`memory_card`/`proposal`/`share`/`love`/`health`/`finance`），客户端不能上传任意长文本。
- 每类来源有字符预算；总上限 12–16k，硬上限 24k；截断必须在 UI 与回复中说明；所有资料带 `[S1]` 引用。
- 结构化回复骨架：`{reply, findings[], proposals[], identity_verdict?, hub_events[]}`；分类/润色/分享/报告各有专用 schema（`ai_schemas.py`）。
- 只认最后一个完整 JSON 代码块或第一个平衡 JSON 对象；解析失败时只保留自然语言回复，不产生提案。
- 所有提案 `pending`，绝不自动执行；未知 kind/非法章节/超长直接丢弃并记 warning。

**安全边界**：
- 角色子进程禁用工具（提示词 + 空工作区 + 环境白名单；P0 验证 dsh headless 的工具行为）。
- 笔记正文、分享链接、恋爱摘要都按“数据不是指令”包裹；后端不执行自然语言里的任何指令。
- 角色拿不到 Vault 路径；所有资料由后端读取后放入 prompt。
- QA 模式（`runtime_label=qa` 或 `enable_dsh=False`）：所有 `/api/ai/*` 返回 409/503，不启动子进程、不读 Key；分享链接抓取也禁用。

### 1.3 记忆中枢（忆灵）

**数据模型**（新增 SQLite 表；transcript/proposal 属过程数据，不进 Markdown）：

| 表 | 用途 |
|---|---|
| `ai_threads` | 角色会话（role_id/mode/dsh_session_id/title） |
| `ai_turns` | 每轮 user/assistant/system 内容、来源、提案引用 |
| `ai_proposals` | 通用提案（identity_patch/new_note/new_task/new_habit/archive_note/报告） |
| `memory_hub_events` | 角色/主 dsh/用户投递的“值得记住的事”，含去重键 |
| `memory_hub_runs` | 每次综合的输入范围、状态、卡片数、快照路径 |

**复用 `memories` 表**作为“事实候选池”（现有 candidate→active 审核），不把 Hub 塞进 memories 语义。

**`.torch-os/忆灵/`**：
- `画像.json`（规范画像：第三人称、分节、confidence、sources、revision、updated_at）
- `画像历史/<revision>.json`（每次更新前备份）
- `复盘卡/*.md`（pending，采纳/忽略后移入对应目录）
- `事件/*.md`（主 dsh/脚本投递，消费后移入 `已读/`）
- `state.json`（上次综合、冷却、已读 hash、用户忽略主题）
- `身份备份/`（身份补丁应用前备份）

**Vault 可读产物**：
- `01_自我档案/08_忆灵观察/画像-YYYY-MM-DD-HHmm.md`（append-only 画像快照）
- `01_自我档案/08_忆灵观察/观察-YYYY-MM-DD-HHmm.md`（观察报告）
- frontmatter：`type: observation|portrait_snapshot`、`generated_by: yiling`、`run_id`、`sources`、`confidence`、`memory: false`（避免被再次提取成记忆候选）。

**事件收集**：
- 所有角色回复可带 `hub_events`，`RoleService` 校验后写入并去重（`sha256(kind|summary|source_ref|日期)`）。
- 主 dsh：新增 `scripts/memory_event.py` 写 `.torch-os/忆灵/事件/*.md`；`dsh/AGENTS.md` 与 `torch-mentor` skill 增加“结论后投递事件”的规则。
- 手动入口：自我档案忆灵页“告诉忆灵一件事”（文本+类型+来源）。
- 确定性输入（活动、日记 digest、任务/习惯、恋爱/健康/财务摘要、旧提问队列）在综合时作为“时间窗口事实”进入 context，不逐条变事件。

**综合流水线** `POST /api/memory-hub/synthesize`：
1. 校验 `confirm=true`、Key/dsh 可用、无并发综合。
2. 收集 7/30 天：新事件、日记 mood/energy、任务/习惯完成率、最近改动笔记标题、active memories 摘要、各报告摘要、旧提问队列、当前画像。
3. `yiling/synthesize` 生成：画像、观察、复盘卡、身份补丁建议。
4. 校验后写画像 JSON（带 revision、先备份）、写 Vault 快照、写卡片队列；身份补丁只创建 proposal，不直接应用。
5. 写 `memory_hub_runs` + 审计；失败保留上次画像，不半写。
6. MVP 同步请求（前端 timeout 240s）；P4 可改后台 job + 轮询，接口形状兼容。

**复盘卡类型与动作**：
- `observation`（模式/变化）、`question`（值得问的问题，替代旧提问卡）、`correction`（与旧记忆冲突）、`identity_patch`、`suggestion`（建任务/习惯）。
- 动作：**留下**（写观察/记忆笔记）、**和忆灵聊聊**（带卡片进苏格拉底）、**忽略**（记原因，不重复生成）、**生成身份修改**（仅 identity_patch）。
- 生命周期 pending → kept|dismissed|expired（30 天）；所有动作审计。

**身份补丁与两条写入路径**：

| 路径 | 触发 | 要求 | 应用 |
|---|---|---|---|
| 忆灵审核 | 综合产出 `identity_patch` 卡 | 用户在卡片上看 diff + 理由 + 来源，点“采纳修改” | 直接 apply（`confirm=true` + revision 校验） |
| 苏格拉底 | 用户在对话里说服 AI | AI 输出 `identity_verdict.decision=accept`，后端发一次性 `verdict_token`（绑定 patch hash + base_revision，24h 过期） | 必须带 token 才能 apply |
| 旧身份表单 | 下线 | UI 不再提供直接编辑 | `PUT /api/identity` 仅内部兼容，文档标 deprecated |
| 旧 dsh 身份候选 | 保留数据 | UI 不再显示“直接采纳”，改为忆灵卡片 | 走忆灵/苏格拉底 |
| 提问卡回答 | 不再直接写身份 | 改为 hub event + 观察/记忆笔记 | 不绕过审核 |

**补丁 payload**：
```json
{
  "changes": [{"field": "values", "from": "...", "to": "...", "reason": "...", "sources": ["S3"]}],
  "base_revision": "abc123",
  "confidence": 0.82
}
```
- 字段只允许 name/motto/worldview/lifeview/values/notes；长度沿用 `identity.py`。
- apply 前重新读取 identity，revision 必须等于 `base_revision`；写前备份；保留 banner/hero/mottos 字段；审计只记字段名/proposal/revision，不记内容。
- 失败返回 409（stale/冲突），不覆盖。

**取代旧提问环节**：
- 工作台移除 `workbench-question` 卡片与 `loadQuestionCard`；改为忆灵条（待审卡片/上次综合/身份补丁数）。
- `questions.py` 题库继续存在：忆灵综合读取 `questions.load_queue()`，把“值得问”转成 `question` 卡。
- `POST /api/questions/answer` 改为写 hub event + 观察/记忆笔记，不直接写身份。
- `scripts/question_draft.py` 继续可用；`questions.js`/`questions-markup.js` 保留但标记 deprecated，不再引用。

### 1.4 通用提案层

`ai_proposals` 状态：`pending → applying → applied | rejected | expired | failed`。

| kind | 应用函数 | 规则 |
|---|---|---|
| `identity_patch` | `identity.write_identity` | 忆灵直接采纳或苏格拉底 verdict + revision |
| `new_note` | `pillar_notes.create_note` | 章节允许清单；生成型带 generated_by/run_id/sources |
| `archive_note` | `pillar_notes.move_note` | 只允许人生路/知识库；revision |
| `new_task` | `tasks.create_task` | 清单存在、日期合法 |
| `new_habit` | `habits.HabitService.create` | frequency/目标天数校验 |
| `health_report` / `finance_report` / `life_advice` | 专用报告写入 | 只允许对应角色/章节 |

- 所有 apply 必须 `confirm=true`；幂等；所有写入 `data_lock`/模块锁；审计 `proposal_applied`。
- 章节允许清单：
  - 人生路写作：`reflection`、`insight_life/work/study`、`review_week/month`、`goal_year/quarter/month`、`project_plan`、`work_review_project/meeting/pitfall`、`essay`；日记走 journal API；任务/习惯禁止走通用入口。
  - 知识库：`inbox`、`topic`、`reading`、`course`、`concept`、`method_work`、`method_general`、`output`。
  - 自我档案/生活运营：通用入口禁止；观察/报告走专用服务。
- UI 卡片：`frontend/proposals.js` + `proposals-model.js`（纯函数，可测）；显示 diff、来源、置信度、`requires_dialogue`、`stale`。

### 1.5 写作层（人生路 / 知识库共享编辑器）

**共享核心，两个壳**：`note-editor.js` + `editor-model.js`，由 `life-page.js` / `knowledge-page.js` 挂载；差异只在默认章节、AI 角色、页面装饰。

- Markdown 工具栏：标题/加粗/斜体/删除线/列表/待办/引用/代码/链接/分割线；按钮插入 Markdown 语法。
- 左侧源文 + 右侧实时预览（复用 `pillar-markdown.renderMarkdownDocument`）；可切换纯预览。
- 自动草稿存 localStorage（按 path+revision），保存才写盘；保存用 `expected_revision`，409 弹冲突提示，绝不覆盖。
- 保留未知 frontmatter 键；CRLF/LF 沿用 `markdown_store` 规则；只允许 `WRITABLE_CHAPTERS`；`VAULT_READ_ONLY` 时编辑器禁用。
- AI 润色：`journal_companion/polish` 或 `academic_mentor/polish` → diff 预览 → 用户点“应用到正文”才替换；可只替换选中段。
- AI 判类/移动：`journal_companion/classify` → 1–3 张卡（类型/标题/正文/目标章节/理由）→ 用户确认 → `new_note`/`archive_note` proposal。
- “和我聊聊”：`journal_companion/discuss` drawer，上下文=当前笔记+近 7 天日记；可产生 memory note/hub event。
- “在 Obsidian 打开”保留现有 host bridge。

**写入 API**（新增，避免与现有 `/api/notes` 读接口冲突）：
- `POST /api/writing/notes`（创建）
- `PUT /api/writing/notes`（更新，`expected_revision`）
- `POST /api/writing/notes/move`（移动，`expected_revision`）
- `POST /api/writing/classify`（AI 优先，`journal_inbox` 确定性回退）
- `POST /api/writing/polish`（AI 润色）
- 日记仍只走 `/api/journal/*`；任务/习惯仍走各自模块。

**只读政策变更**：
- 人生路/知识库：应用内可写（允许清单 + revision + 原子写 + 审计）。
- 自我档案/生活运营：通用编辑器只读；AI 观察/报告走专用 proposal。
- 任务/习惯/身份：仍由专用接口/审核流程管理。
- 同步更新 `README`、`FEATURE_MAP`、`dsh/AGENTS.md`、`help-content.js`、`pillars.js` 文案与测试。

### 1.6 知识库问答（不内嵌）

- “DeepSeek 问答”tab：
  - 大按钮“在浏览器打开 DeepSeek 问答” → `https://chat.deepseek.com/`，`target=_blank rel=noopener noreferrer`；Obsidian 内用现有 `torch:open-url` host bridge 打开系统浏览器。
  - 分享链接输入框 → `POST /api/knowledge/share/preview`：只接受 `chat.deepseek.com/share/<id>`；显示标题、轮数、字数、每轮预览、Markdown 预览、建议章节。
  - 选择轮次/标题/章节 → **直接归档**（不调用 AI）或 **让学术导师整理**（`academic_mentor/share_digest`）→ 预览/编辑 → `new_note` proposal。
  - 生成后进入文档库；可“和导师继续学习”打开 drawer。
- 不读取/保存网页 Cookie；不代理登录态；不请求任意 URL（v1.0 只支持 DeepSeek 分享链接，通用网页解析后续再说）。
- 文档库：共享编辑器；AI 判类/润色；收件箱建议可转为 `archive_note` proposal。

### 1.7 生活运营

- 总览：AI 生活助理卡（最近建议 + 各专家报告摘要）+ “让生活助理汇总”。
- 建议 → `new_task`/`new_habit` proposal → 采纳后在待办事项创建。
- 恋爱系统：12 页保持不动；总览/报告/日志页增加“问问恋爱大师”按钮；上下文只取最新分析摘要/画像/日志标题，不发原始聊天；现有独立 DeepSeek Key 直连保留（唯一过渡例外）。
- 健康：现有体重/睡眠/运动月度表 + BMI（如健康档案存在）；报告写 `02_生活运营/06_健康报告`；体检/医疗仍由 Obsidian 维护。
- 财务：现有收支汇总/趋势/分类；报告写 `07_财务报告`。
- 家庭：v1.0 占位（物资清单说明 + 智能家居“后续版本”空状态），不发任何设备请求。
- 设计文档：`docs/LIFE_SYSTEMS.md`（健康/理财/家庭后续路线）。

### 1.8 工作台小组件管理

- `frontend/widget-registry.js`：`WIDGET_MANIFEST`（key/label/默认栏/说明）。
- `widget-order.js` 布局从 `{left,right}` 扩展为 `{left,right,hidden}`；旧数据 `hidden=[]`；隐藏组件不参与拖拽/空栏统计。
- 设置 → 界面 → “工作台小组件”：开关、上移/下移、换列、恢复默认；仍存 localStorage `torch-os:workbench-widget-layout`，由 `personal-data.js` 镜像到 `.torch-os/personal-data.json`。
- 隐藏组件时 `workbench.js` 跳过对应 loader（天气/列车等不请求）。
- 新增 `yiling` 小组件（右侧默认）：待审卡片数、上次综合、进入忆灵。

## 2. 页面交互逻辑（逐页）

### 2.1 工作台 `#workbench`

1. 打开 → 读取 `personal-data` → 只渲染未隐藏小组件 → 忆灵条（待审/上次综合）→ 横幅/问候/书签 → 时间管理模块 → DSH iframe。
2. 设置 → 工作台小组件 → 开关/拖拽/换列 → 立即生效；刷新后仍保持；隐藏的组件不请求数据。
3. 待办 → “AI 助手”按钮 → role drawer（`life_assistant/tasks`）→ 输入或快捷 chips（“把这段话拆成任务”“帮我排今天”“这周该放弃什么”）→ 回复 + `new_task`/`new_habit` 卡片 → 采纳 → 任务板刷新。
4. 与神对话：保持 iframe；启动/重启/新标签打开不变。

### 2.2 人生路 `#life`

页内二级导航：**写作（默认）/ 笔记本 / 时间线 / 复盘**。

- 写作：单输入框 → “AI 判类并保存” → 1–3 张卡（日记/反思/心得/随笔/目标计划；类型可改）→ “存下来”：
  - 日记 → 当天日记 `00_人生路/01_日记/YYYY-MM-DD.md` 的今日回顾；
  - 反思/心得/随笔 → 对应章节新笔记；
  - 目标/计划 → `new_note` proposal，用户选章节后创建。
- 笔记本：左章节轨（本子）→ 中间卡片列表（标题/摘要/类型/日期/标签）→ 点击进入编辑器；搜索/筛选。
- 时间线：跨章节按日期倒序；显示“今天已记”。
- 复盘：2026-10-31 取消人生路「今晚复盘」入口（前端 `journal-ritual` 已移除）；
  周/月复盘入口保留。
- 编辑器：工具栏 + 源文/预览；AI 润色 diff；AI 判类/移动；和写作伙伴聊聊；在 Obsidian 打开；无删除按钮。

### 2.3 知识库 `#knowledge`

页内二级导航：**文档库（默认）/ DeepSeek 问答 / 收件箱**。

- 文档库：章节轨 → 列表 → 共享编辑器；AI 判类/润色；收件箱建议可移动。
- DeepSeek 问答：外链按钮 + 分享链接输入 → 预览 → 直接归档或 AI 整理 → 保存 → 文档库。
- 收件箱：只读建议 + “转成移动提案”（确认后 `archive_note`）。

### 2.4 自我档案 `#self`

页内二级导航：**概览 / 身份 / 忆灵 / 苏格拉底 / 档案**。

- 概览：“忆灵眼中的你”画像摘要（第三人称、只读）；状态仪表盘（近 14 天 mood/energy、任务/逾期、习惯完成率）；待审卡前 3；身份摘要；按钮 [让忆灵整理] [与苏格拉底聊聊]。
- 身份：字段只读；revision/路径/在 Obsidian 打开；待处理身份补丁（diff、状态、采纳/去确认）；无直接编辑表单。
- 忆灵：上次综合/新事件数/时间窗口 [7/30 天] [现在整理]；复盘卡列表（观察/问题/纠正/身份补丁/建议）→ 留下/聊聊/忽略；画像历史；观察时间线；记忆候选审核；dsh 身份候选只读展示。
- 苏格拉底：全高角色对话；上下文=身份+画像+active memories+近 14 天日记；快捷提示；AI 同意后内联显示补丁卡；新对话/历史。
- 档案：现有档案/阅读器/时间线，只读。

### 2.5 生活运营 `#ops`

二级：**总览 / 恋爱 / 健康 / 理财 / 家庭 / 档案**（恋爱仍是独立 `#ops` 子页）。

- 总览：AI 生活助理卡 + 专家报告摘要；[让生活助理汇总] → 建议 + 提案卡；采纳 → 任务/习惯。
- 恋爱：12 页保持；加“问问恋爱大师”。
- 健康：体重/睡眠/运动仪表盘 + 报告列表 + [让私人医生评估]。
- 理财：收支汇总/趋势 + 报告列表 + [让理财专家评估]。
- 家庭：占位页 + 设计文档链接。
- 档案：现有生活运营章节只读。

### 2.6 设置

新增“工作台小组件”分区；其他不变。服务状态版本显示 1.0。

### 2.7 与神对话

保持 `/harness/` iframe；主 dsh 的 AGENTS.md 增加角色表、忆灵事件投递、写作政策；不新增第二个通用聊天。

## 3. 数据与 API 契约

### 3.1 新增/扩展表

- `ai_threads`、`ai_turns`、`ai_proposals`、`memory_hub_events`、`memory_hub_runs`（schema_migrations 6–9）。
- 现有 `memories` 继续作为事实候选池；`conversations/messages` 不动。
- `schema_export`/`DATA_HANDBOOK` 分组：AI 会话/提案、记忆中枢、报告；保持 groups 集合不变。

### 3.2 主要 API

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/ai/status` | 可用性/Key 来源/余额摘要/角色数 |
| GET | `/api/ai/roles` | 角色清单（不返回 prompt） |
| POST | `/api/ai/threads` | 新建角色会话 |
| POST | `/api/ai/chat` | 角色对话（role/mode/thread/message/entry/selectors） |
| POST | `/api/ai/run` | 一次性能力（polish/classify/share_digest/report） |
| GET/DELETE | `/api/ai/threads/{id}` | 读取/删除会话 |
| GET | `/api/memory-hub/overview` | 画像/状态/卡片/候选 |
| GET | `/api/memory-hub/portrait` | 规范画像 |
| GET | `/api/memory-hub/portrait/history` | 画像快照 |
| GET/POST | `/api/memory-hub/events` | 事件读取/手动投递 |
| POST | `/api/memory-hub/synthesize` | 综合（confirm/range/focus） |
| POST | `/api/memory-hub/cards/{id}/keep|dismiss` | 卡片动作 |
| GET | `/api/memory-hub/cards` | 待审卡片 |
| GET | `/api/proposals` | 提案列表（status/kind/entry） |
| POST | `/api/proposals/{id}/apply` | 采纳（confirm/edits） |
| POST | `/api/proposals/{id}/reject` | 忽略（reason） |
| POST | `/api/writing/notes` | 创建笔记 |
| PUT | `/api/writing/notes` | 更新笔记（expected_revision） |
| POST | `/api/writing/notes/move` | 移动 |
| POST | `/api/writing/classify` | AI 判类（回退确定性） |
| POST | `/api/writing/polish` | AI 润色 |
| POST | `/api/knowledge/share/preview` | 分享链接预览 |
| POST | `/api/knowledge/share/archive` | 直接归档 |
| POST | `/api/knowledge/share/digest` | AI 整理成笔记提案 |

- 身份补丁 apply：忆灵路径 `confirm=true` + revision；苏格拉底路径额外 `verdict_token`。
- 所有 AI 接口在 QA/禁用时 409/503；无 Key 409；dsh 不可用 503。
- 前端 `core.api` 增加 `timeoutMs` 选项；综合/报告使用 240s，普通对话 120s。

### 3.3 Vault / `.torch-os` 路径

- 新增章节（37→43）：
  - `00_人生路/09_随笔`（`essay`）
  - `01_自我档案/08_忆灵观察`（`yiling`）
  - `02_生活运营/06_健康报告`、`07_财务报告`、`08_生活建议`、`09_家庭与智能家居`（`family`）
- `LAYOUT_VERSION` 保持 `pillars-v2`：本次是纯增量、无旧目录搬迁；不伪造迁移版本。`ensure_layout` 会创建新目录；现有库首次写入时自动 `mkdir`。
- `.torch-os/忆灵/`：画像、历史、复盘卡、事件、state、身份备份。
- `ai-runtime/`：角色子进程空工作区/临时文件，放 `<data_dir>`，不进 Vault。

### 3.4 写入与审计

- Markdown 一律 load → revision 校验 → 原子替换（`markdown_store.guarded_write` 或模块现有协议）。
- 所有写操作 `database.audit`；审计不含正文/身份值/Key。
- AI 子进程期间不持 `data_lock`；apply/综合用锁；并发 ≤2；综合全局互斥。
- proposal apply 幂等；revision 冲突 409；失败不半写。

## 4. 文件清单

### 4.1 后端新增

| 文件 | 职责 |
|---|---|
| `backend/app/ai_gateway.py` | dsh headless 统一调用（stdin prompt）、NDJSON、超时、进程树、并发、脱敏 |
| `backend/app/ai_roles.py` | 角色注册表、RoleService、thread、结构化回复、proposal 落库 |
| `backend/app/ai_context.py` | 按角色范围拼只读上下文、来源引用、预算/截断 |
| `backend/app/ai_schemas.py` | 各模式 Pydantic 输出模型 |
| `backend/app/proposals.py` | 通用提案服务、校验、apply/reject、幂等 |
| `backend/app/memory_hub.py` | 事件、综合、画像、复盘卡、身份补丁 verdict |
| `backend/app/note_editor.py` | 人生路/知识库写入允许清单、创建/更新/移动、revision、审计 |
| `backend/app/share_link.py` | 分享链接抓取/解析/Markdown（从 scripts 提取） |
| `backend/app/love_context.py` | 只读恋爱报告摘要适配 |
| `backend/app/life_reports.py` | 健康/财务/生活报告上下文与落点校验 |

### 4.2 后端修改

| 文件 | 修改 |
|---|---|
| `backend/app/application.py` | `APP_VERSION='1.0.0'`；注册 AI/Hub/Proposals/Writing/ShareLink 路由；capabilities；注入 `ai_runner`/`roles_dir`/`enable_ai_roles`；`app.state.ai_roles` |
| `backend/app/db.py` | 5 张新表 + schema_migrations |
| `backend/app/vault_layout.py` | 新增 6 章（essay/yiling/health_report/finance_report/life_advice/family）；章节总数 43；`LAYOUT_VERSION` 不变 |
| `backend/app/journal_chat.py` | 内部改走 `ai_gateway`（stdin），保留 API 形状 |
| `backend/app/journal_inbox.py` | 增加 `essay` 类型与关键词 |
| `backend/app/pillar_notes.py` | `update_note` 支持 `expected_revision`；`create_note` 支持 `generated_by`/`extra_metadata` |
| `backend/app/identity.py` | 增加补丁合并辅助（保留 banner 字段）；审计由 proposal 层负责 |
| `backend/app/questions.py` | answer 改为 hub event + 观察/记忆笔记；标 deprecated |
| `backend/app/pillars.py` | 总览增加画像/忆灵摘要（或由 Hub 端点单独提供） |
| `backend/app/diagnostics.py` / `schema_export.py` | 新表分组 |
| `backend/app/ops.py` / `structured_notes.py` | 健康/财务摘要复用；可选 BMI/身高读取 |
| `backend/app/security.py` | 如需分享链接出站白名单（`chat.deepseek.com`） |

### 4.3 dsh / 脚本

| 文件 | 修改 |
|---|---|
| `dsh/AGENTS.md` | 角色表、写作政策、忆灵事件投递、`memory_event.py` |
| `dsh/skills/torch-mentor/SKILL.md` | 身份候选/提问改为“投事件/候选，忆灵复核” |
| `dsh/roles/AGENTS.md` | 角色子进程专用规则（无工具、只基于给定资料、JSON 契约） |
| `dsh/roles/yiling.md` | 忆灵/苏格拉底 |
| `dsh/roles/journal_companion.md` | 写作伙伴 |
| `dsh/roles/academic_mentor.md` | 学术导师 |
| `dsh/roles/life_assistant.md` | AI 生活助理 |
| `dsh/roles/love_master.md` | 恋爱大师 |
| `dsh/roles/health_doctor.md` | 私人医生 |
| `dsh/roles/finance_advisor.md` | 理财专家 |
| `scripts/memory_event.py` | 主 dsh 投递事件卡 |
| `scripts/share_link.py` | 改为薄 CLI，导入 `backend/app/share_link.py` |
| `scripts/session_brief.py` | 可选：简报增加画像/待审数量（只读） |

### 4.4 前端新增

| 文件 | 职责 |
|---|---|
| `frontend/roles.js` | 全局角色抽屉控制器 |
| `frontend/roles-model.js` | 纯函数：抽屉/消息/来源/提案渲染 |
| `frontend/roles.css` | drawer/卡片/diff 样式 |
| `frontend/proposals.js` | 提案卡片动作、apply/reject、刷新事件 |
| `frontend/editor-model.js` | 纯函数：工具栏动作、选中包裹、行前缀、脏状态、字数 |
| `frontend/note-editor.js` | 共享 Markdown 编辑器（工具栏+预览+保存） |
| `frontend/editor.css` | 编辑器/预览样式 |
| `frontend/life-page.js` | 人生路 写作/笔记本/时间线/复盘 |
| `frontend/knowledge-page.js` | 知识库 文档库/问答/收件箱 |
| `frontend/self-page.js` | 自我档案 概览/身份/忆灵/苏格拉底/档案 |
| `frontend/ops-page.js` | 生活运营 总览/健康/理财/家庭 |
| `frontend/memory-hub.js` | 忆灵概览/综合/卡片/画像历史 |
| `frontend/memory-strip.js` | 工作台忆灵条 |
| `frontend/share-link.js` | 分享链接预览/归档/AI 整理 |
| `frontend/widget-registry.js` | 小组件清单/标签/默认栏 |
| `frontend/widget-settings.js` | 设置页小组件管理 |
| `frontend/tasks-ai.js` | 待办 AI 入口与快捷动作 |

### 4.5 前端修改

| 文件 | 修改 |
|---|---|
| `frontend/index.html` | 设置导航加“工作台小组件”及分区；工作台忆灵条/忆灵组件/任务 AI 按钮；移除 question slot；引入 roles.css/editor.css |
| `frontend/app.js` | 移除 `initQuestions`；新增 role drawer/proposal 初始化；life/self/knowledge/ops 页加载器；dirty guard 扩展 |
| `frontend/workbench.js` | 忆灵条；隐藏组件跳过 loader；忆灵组件刷新 |
| `frontend/widget-order.js` | `hidden` 布局、可见性过滤、manifest |
| `frontend/widget-drag.js` | 隐藏组件不参与拖拽/空栏 |
| `frontend/settings.js` | 新设置分区 |
| `frontend/pillars.js` | 抽出共享 `mountPillarArchive()`；页内 tabs/编辑器入口；写作政策文案；保留阅读器为“档案” |
| `frontend/time-module.js` | 任务 AI 按钮/刷新事件 |
| `frontend/help-content.js` / `data-handbook.js` | 新功能/数据去向 |
| `frontend/questions.js` / `questions-markup.js` | 保留但不再引用，标 deprecated |
| `frontend/core.js` | `api()` 支持 `timeoutMs`（AI 长请求） |

### 4.6 文档与版本

- 新增：`docs/燎火OS-v1.0-顶层设计-2026-10-29.md`、`docs/AI_ROLES.md`、`docs/MEMORY_HUB.md`、`docs/WRITING_AND_KNOWLEDGE.md`、`docs/LIFE_SYSTEMS.md`。
- 修改：`docs/FEATURE_MAP.md`、`STATUS.md`、`DSH_WORKFLOW.md`、`MENTOR.md`、`VAULT_TEMPLATE.md`、`WAKE_UP_START.md`、`DATA_HANDBOOK.md`、`DEPRECATIONS.md`、`日记闭环设计`、`节奏中心设计`（章节数/旧提问引用）。
- `README.md` 第一行 → `# 薪火OS（Torch OS）· 1.0`。
- `obsidian-plugin/manifest.json` → `1.0.0`；`main.js`/`README.md` 同步。
- `backend/app/application.py` → `APP_VERSION='1.0.0'`。

### 4.7 测试新增

- 后端：`test_ai_gateway.py`、`test_ai_roles.py`、`test_ai_context.py`、`test_proposals.py`、`test_memory_hub.py`、`test_note_editor.py`、`test_share_link_api.py`、`test_role_registry.py`、`test_life_reports.py`。
- 前端：`roles-model.test.mjs`、`proposals.test.mjs`、`editor-model.test.mjs`、`widget-visibility.test.mjs`、`memory-hub.test.mjs`、`share-link.test.mjs`。

### 4.8 复用清单（优先复用，不重写）

- 存储：`markdown_store.load_note/guarded_write/revision`、`vault.parse_text/safe_note_path`、`vault_layout.validate_target/chapter_dir/safe_note_stem`。
- 笔记：`pillar_notes.list_notes/read_note/create_note/update_note/move_note`。
- 日记：`journal_review.build_digest/compose/write_note`、`journal_inbox.classify/save_cards`、`journal_chat.parse_events`。
- dsh：`dsh_web.resolve_command/spawn_argv/workflow_environment`、`DeepSeekBalanceService.resolve_key`。
- 任务/习惯：`tasks.create_task`、`task_notes.*`、`habit_markdown.*`、`HabitService.create`。
- 上下文：`workbench.collect_day`、`task_reports`、`activity.list_day_activity`、`structured_notes.summary`、`steward_policy.suggest`。
- 身份：`identity.read_identity/write_identity`、`identity_inbox.list_drafts`。
- 交互：`core.api/jsonBody/openDialog/confirmAction/toast/changed`、`pillar-markdown.renderMarkdown`、`personal-data` 镜像、`widget-order` 纯函数、`task-open` Obsidian 桥。

## 4.9 代码风格与实现约定

- **后端**：沿用现有风格——`from __future__ import annotations`；模块 docstring 用英文说明契约，产品决策与用户可见错误用中文；路由器统一 `create_<x>_router(...) -> APIRouter`；Pydantic v2（`ConfigDict(extra='forbid', str_strip_whitespace=True)`）；写操作 `run_in_threadpool` + `app.state.data_lock`（或模块自有锁）；每个写操作写 `database.audit`；Markdown 写入必须 revision 校验；日志不出现 Key/token/正文。
- **前端**：vanilla ES module；纯模型/纯函数模块不碰 DOM、不 import `core.js`（保持 node:test 可直接测）；所有拼接 HTML 必须 escape，优先 `createElement` 或现有 `escapeHtml`；新增样式放独立 CSS 文件，不用内联 style；事件用 `data-*` 属性 + 统一 dispatch；注释用中文，解释“为什么”而不只是“做什么”。
- **复用优先**：新模块先查第 4.8 节清单；能在现有 service 上加参数就不复制逻辑；不重写已验证的阅读器/拖拽/编辑器协议。
- **零依赖/零构建**：不新增 npm/前端框架；不引入 WYSIWYG；不改变现有启动方式。
- **每个新模块至少写一段模块级说明 + 关键公共函数 docstring**，方便后续接手（用户要求“写了注释，方便读代码”）。

## 5. 分期实施与验收

### P0 基础与探针（2–3 天）
版本/章节/DB 新表/角色注册表骨架/gateway（fake runner）/`/api/ai/status|roles`/proposal 骨架/契约测试。
**验收**：`check_all.ps1` 全绿；不启动真实 dsh；P0 探针报告（stdin、`--profile headless`、`DSH_AGENTS_HOME`、进程树、无工具）。

### P1 角色层 + 工作台（4–5 天）
RoleService 全链路、上下文、thread、结构化输出、审计、余额/QA 门禁；role drawer；小组件管理；任务 AI 提案。
**验收**：任意页可开 scoped drawer；thread 隔离；无 Key/QA 正确拒绝；隐藏组件刷新后仍隐藏且不请求；任务提案采纳生成 md。

### P2 写作核心 + 人生路/知识库（6–8 天）
编辑器/写作 API/人生路 tabs/知识库 tabs/分享链接/判类/润色/讨论/提案。
**验收**：revision 冲突 409；Obsidian 内容一致；CSP 无 inline；分享只接受 `chat.deepseek.com`；AI 不可用时编辑器仍可保存；分类回退可用。

### P3 记忆中枢 + 自我档案（5–7 天）
Hub 事件/综合/画像/卡片/身份补丁/verdict；自我档案 tabs；移除旧提问卡；`memory_event.py`；AGENTS/skill 更新。
**验收**：画像/卡片/快照正确；忆灵补丁直接采纳；苏格拉底无 verdict 不能 apply；revision 冲突 409；工作台忆灵条；旧提问 UI 不再出现。

### P4 生活运营 AI（4–5 天）
ops tabs/生活助理/恋爱大师/私人医生/理财专家/报告落盘/提案建任务习惯/健康财务仪表盘/家庭占位/文档。
**验收**：报告进对应章节；采纳建议创建任务/习惯；恋爱系统不回退；恋爱大师不含原始聊天；占位页明确。

### P5 加固与发布（3–4 天）
Windows 进程/编码/并发/超时实测；审计覆盖；备份/导出；README/FEATURE_MAP/STATUS/模板/插件；browser smoke；`check_all.ps1`。
**验收**：全量测试通过；连续 3 次角色调用无残留 node；日志无 Key/正文；八条需求手动验收；文档契约全绿。

> 估算合计约 24–32 个工作日；每期可独立验收，不要求一次做完。建议确认后先做 P0–P1，再决定 P2 之后的节奏。

## 6. 版本、迁移与契约测试

- `APP_VERSION='1.0.0'`；README 首行；WAKE_UP_START；插件 manifest/main/README。
- 章节 37→43；`LAYOUT_VERSION` 保持 `pillars-v2`（纯增量、无搬迁）；更新 `VAULT_TEMPLATE`/`STATUS`/章节数断言。
- `test_docs_contract.py`：新增 capability：`ai_roles`、`memory_hub`、`proposals`、`note_editor`、`share_link`、`widget_visibility`、`life_assistant`、`life_reports`；FEATURE_MAP 同步。
- `test_nav_manifest.py`：6 个顶层 view 不变；life/self/ops/knowledge 的二级 tabs 不改变 `/api/nav`。
- `test_schema_export.py`：DATA_HANDBOOK 新表；groups 集合不变。
- `test_dsh_workflow.py`/`test_mentor_workflow.py`：角色文件与 AGENTS 表同步；章节路径真实；无个人信息。
- 旧只读断言改为“self/ops 只读，life/knowledge 可写”；questions 身份写入改为 hub event。
- `single-chat-surface.test.mjs`：仍只有一个 DSH 总对话；role drawer 不计为新的通用聊天。

## 7. 验证方式

1. `scripts/check_all.ps1`（pytest + node --test + compileall + pip check + integrity）。
2. 新增测试子集：`pytest tests/test_ai_gateway.py tests/test_ai_roles.py tests/test_memory_hub.py tests/test_proposals.py tests/test_note_editor.py tests/test_share_link_api.py`。
3. 手动端到端：
   - 设置 → 小组件开关 → 刷新/重启 → 隐藏保持；隐藏组件不发请求。
   - 待办 AI → 提任务 → 采纳 → 生成任务笔记；滴答同步不受影响。
   - 人生路写作 → AI 判类 → 保存 → 正确章节；Obsidian 打开一致；在 Obsidian 改动后回应用保存 → 409 冲突。
   - AI 润色 → diff → 应用；CSP 无 console 报错。
   - 知识库分享链接 → 预览 → 归档；AI 整理 → 提案 → 保存；外链在系统浏览器打开。
   - 自我档案 → 忆灵综合（fake/真实）→ 卡片 → 身份补丁采纳；苏格拉底辩论 → verdict → apply；revision 冲突。
   - 生活运营 → 生活助理建议 → 采纳 → 任务/习惯；健康/财务报告落盘。
   - DSH iframe 仍可用；角色 drawer 与主对话会话隔离；日志无 Key/token/正文。
4. Windows 专项：`dsh --profile headless --json -` stdin 多行；超时杀进程树；连续 3 次无残留 node。

## 8. 风险与 P0 探针

| 风险 | 处置 |
|---|---|
| dsh headless 版本/参数变化 | P0 探针 `--profile headless`、stdin、`--session-id`；保留 `dsh headless` 回退 |
| headless 是否加载 AGENTS.md | P0 用临时角色目录验证 `DSH_AGENTS_HOME`；不生效则角色规则直接进 prompt |
| Windows 多行 argv | 使用 stdin `-`；不走 argv 传 prompt |
| 每轮 5–30s、无流式 | UI 进度/超时/禁止重复提交；综合 240s；P4 再考虑 job/轮询 |
| 上下文过大/幻觉 | 预算/截断/来源引用；解析失败不产生提案；提示“库里没有” |
| Prompt 注入（笔记/分享/恋爱摘要） | 数据区块隔离；无工具；空工作区；环境白名单；schema 校验 |
| 身份安全 | 忆灵直接采纳需用户点击；苏格拉底需 verdict；revision+备份；旧表单下线 |
| CSP 无 inline | 新 CSS 文件；DOM 用 createElement/escapeHtml；不引入 WYSIWYG |
| 契约测试敏感 | 每期同步改 FEATURE_MAP/能力/章节/文档/测试 |
| 测试基线大 | 每期跑 `check_all.ps1`；新增 fake runner，QA 不调真模型 |
| 恋爱系统例外 | v1.0 保留独立 Key 直连；文档标“唯一过渡例外”；v1.1 再评估 |
| 自动综合成本/隐私 | 只手动触发 + 确认；不做自动定时综合；审计不存正文 |

## 9. v1.0 明确不做

- WYSIWYG 富文本库、图片/附件编辑器（后续版本）。
- chat.deepseek.com iframe/登录态/Cookie 集成；只做外链 + 分享链接。
- 完整健康系统（血压设备/体检导入）、完整财务系统（账户/预算）、家庭 IoT 控制；只做仪表盘/报告/占位。
- 向量检索/RAG、自动定时综合、自动执行提案、流式输出。
- 恋爱系统迁移到统一 dsh 网关（保留现状）。
- 任务迁 `.torch`、人生路 v3 三章收缩（已确认不做）。
- 删除旧 questions/memories/旧表；只标 deprecated。
- 多用户/云同步。

## 10. 需要你确认的点（方案层面）

1. **忆灵与苏格拉底按“同一个 agent、两种模式”实现**——这是推荐方案；如果你希望两个独立角色，请在批准前说明。
2. **记忆中枢的身份补丁直接采纳**（审核通过即可写），**苏格拉底的身份补丁必须 verdict**——按你说明书的“唯二写入口”设计。
3. **人生路/知识库开放应用内编辑**，自我档案/生活运营仍只读——这是 v1.0 的写入政策变更，会改 README/AGENTS/测试。
4. **新增 6 个章节**（随笔、忆灵观察、健康报告、财务报告、生活建议、家庭）——纯增量，不做迁移。
5. **先做 P0–P1 还是全量开工**——建议确认后先做 P0–P1（角色层+工作台+小组件），验收后再决定 P2 之后的节奏。
