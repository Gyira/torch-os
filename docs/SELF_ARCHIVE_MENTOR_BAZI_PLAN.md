# 自我档案 · 人生导师 · 八字系统（实施计划）

> 配套设计：`docs/SELF_ARCHIVE_MENTOR_BAZI.md`（唯一规格来源）。
> 日期：2026-11-01。
> 用法：按阶段执行；每阶段先写测试再写实现；每阶段结束跑对应测试并更新本文档的「进度」。
> 如果上下文被压缩：读设计文档 + 本文档，按当前未完成阶段继续；不要重做已确认的决策。

---

## 总览

| 阶段 | 内容 | 主要产出 | 验收 |
|---|---|---|---|
| P0 | 数据布局与档案存储 | `vault_layout` 新章节、`self_archive.py`、迁移 | 迁移可预览/执行/回滚备份；条目读写/取代/revision 测试通过 |
| P1 | 人生导师后端 | 角色、context、记忆、待确认、每日三问、synthesize | 后端测试全过；API 可手工调用 |
| P2 | 自我档案前端 | 三部分外壳、左栏、总览、本子、画像、迁移卡 | 浏览器可见；标题在顶部；画像在本子下方 |
| P3 | 人生导师前端 | 三栏对话、会话、待确认、记忆、今日提问、工作台条 | 对话/辩论/采纳/提问闭环 |
| P4 | 八字系统 | 共享引擎、个人命盘、AI 解读、历史、前端 | 排盘/AI/历史可用；旧八字笔记迁移 |
| P5 | 收编与全局样式 | 旧入口收编、顶部标题、回收中心、生活运营图标 | 旧忆灵入口不存在/指向新页；样式一致 |
| P6 | 回归与文档 | 全量测试、文档更新、手工验收 | 测试全绿；文档与实现一致 |

依赖：P0 → P1/P2 可并行；P1 → P3；P2 → P3；P0 → P4；P5 依赖 P2/P3；P6 最后。

---

## P0 数据布局与档案存储

### P0-1 章节与路径

文件：`backend/app/vault_layout.py`

- 新增常量：
  ```python
  SELF_ROOT = "01_自我档案"
  SELF_ARCHIVE_ROOT = f"{SELF_ROOT}/01_档案"
  SELF_MENTOR_ROOT = f"{SELF_ROOT}/02_人生导师"
  SELF_BAZI_ROOT = f"{SELF_ROOT}/03_八字系统"
  SELF_IDENTITY_RELATIVE = f"{SELF_ARCHIVE_ROOT}/身份（identity）.md"
  SELF_PORTRAIT_RELATIVE = f"{SELF_ARCHIVE_ROOT}/00_画像.md"
  ```
- `CHAPTERS` 新增 11 个 self 章节（key 与路径见设计 4.1）；旧 self 章节加入 `LEGACY_CHAPTER_KEYS`（不创建、只读兜底）。
- `CHAPTER_GROUPS` 可为 `self` 添加分组（可选；新前端不用 generic rail，但搜索/编辑器可能用）。
- 新增 `SELF_BOOKS` 常量（key/title/folder/entry_types），或放在 `self_archive.py`，vault_layout 只放路径。推荐放在 `self_archive.py`，vault_layout 只提供路径函数。
- 新增 `ensure_self_layout(vault_dir)`：创建 11 个本子文件夹、`02_人生导师/记忆|待确认/档案补丁|已采纳|已驳回|提问|历史/画像|历史/旧忆灵观察`、`03_八字系统/解读|历史`；`ensure_layout` 调用它。
- 新增 `self_identity_relative()` 等函数；`identity.py` 的 `_identity_path` 新路径优先，旧路径兜底（`01_内核与三观/身份（identity）.md`、`01-Identity/...`）。

验收：
- 空库启动后目录齐全；旧库不会自动创建旧 self 章节空目录。
- `identity` 读写新路径；旧路径仍可读。

### P0-2 档案条目存储

文件：`backend/app/self_archive.py`（新建）

```python
BOOKS = (
  ("self_facts", "基础事实", "01_基础事实", ("fact","behavior")),
  ("self_body", "身体与能量", "02_身体与能量", ("fact","behavior","inference")),
  ("self_behavior", "行为与习惯", "03_行为与习惯", ("behavior","fact")),
  ("self_emotion", "情绪与压力", "04_情绪与压力", ("behavior","inference","hypothesis")),
  ("self_motivation", "动机与需求", "05_动机与需求", ("inference","narrative","fact")),
  ("self_values", "三观与决策", "06_三观与决策", ("narrative","fact","inference")),
  ("self_relationship", "关系与边界", "07_关系与边界", ("behavior","inference","fact")),
  ("self_narrative", "自我叙事", "08_自我叙事", ("narrative",)),
  ("self_paradox", "矛盾与盲区", "09_矛盾与盲区", ("inference","hypothesis")),
  ("self_timeline", "人生时间线", "10_人生时间线", ("fact","narrative")),
  ("self_experiments", "验证与更新", "11_验证与更新", ("hypothesis","behavior","fact")),
)
```

实现：
- `book_path(vault, key) -> Path`
- `list_entries(vault, key, *, include_history=False) -> list[dict]`
- `read_entry(vault, entry_id) -> dict`（按本子索引/扫描）
- `create_entry(vault, *, book_key, entry_type, confidence, title, text, evidence, counterexample, verify, sources, origin, supersedes="") -> dict`
- `supersede_entry(vault, old_entry_id, new_payload, *, base_revision) -> dict`：校验 revision，旧条目 `status=superseded`，新条目 `supersedes=old_id`。
- `read_portrait(vault)`, `write_portrait(vault, text, ...)`：备份旧版到 `02_人生导师/历史/画像/`。
- `entry_revision(path) -> str`：`markdown_store.revision`。
- 所有写入 `ensure_vault_writable`；路径 `safe_note_path`；原子写。
- 审计：`self_archive_entry_created`, `self_archive_entry_superseded`, `self_archive_portrait_updated`；只记 key/id/来源数。

验收测试 `tests/test_self_archive.py`：
- 11 个本子路径正确。
- create 写入 frontmatter/正文，list 能读回。
- supersede 校验 revision；冲突 409；旧条目 status 变 superseded。
- portrait 覆盖前备份。
- 只读模式拒绝写入。

### P0-3 待确认存储

文件：`backend/app/mentor_pending.py`（新建）

- 卡片路径：`02_人生导师/待确认/档案补丁/<stamp>-<id>.md`。
- `create_pending(vault, payload) -> dict`；去重键 `section|action|entry_id|title|new_text`。
- `list_pending(vault)`；`read_pending`；`accept_pending(vault, pending_id, *, edited_text="", confirm=False)`：
  - 读取卡片；校验 status=pending；
  - 调 `self_archive.create_entry` 或 `supersede_entry`；
  - 卡片移到 `已采纳/`，frontmatter status=accepted、result_entry_id；
  - 返回值。
- `reject_pending`：移到 `已驳回/`，status=rejected。
- 并发：文件锁/`os.replace`；同一 pending 处理两次第二次 409。
- 审计：`mentor_pending_accepted/rejected`。

验收测试：accept create/supersede；edit text；reject；重复处理 409；只读 409。

### P0-4 迁移

文件：`backend/app/self_migration.py`（新建）

- `preview(vault) -> dict`：检测旧 self 目录；生成 plan（source→target, kind, conflicts, backup）。
- `apply(vault, *, confirm=False)`：
  - `confirm` 必填；
  - `VAULT_READ_ONLY` 拒绝；
  - 备份整棵 `01_自我档案` 到 `99_重构备份-YYYYMMDD-HHMMSS/`；
  - 创建新目录；
  - 逐文件处理（映射见设计 4.7.1）；同名冲突编号保留；
  - 身份卡迁移：name/motto/mottos/hero 保留；worldview/lifeview/values/notes 生成 `06_三观与决策` 条目（`origin: migration`），旧字段保留在身份文件（不自动清空）；
  - 八字：解析旧 frontmatter 生成 `03_八字系统/命盘.md` + 解读 Markdown；
  - 旧忆灵观察/复盘卡/身份候选移到历史；
  - 写 `.torch-os/self-archive-layout.json`；
  - 返回 moved/conflicts/backup。
- 幂等：标记 schema 存在且无旧目录 → `already_done`。
- 失败：返回已完成清单；不删除原文；可重跑。

验收测试 `tests/test_self_migration.py`：
- 预览不写盘；apply 备份存在；旧文件归位；冲突保留；幂等；只读拒绝。

### P0-5 身份路径

文件：`backend/app/identity.py`

- `IDENTITY_RELATIVE = "01_自我档案/01_档案/身份（identity）.md"`。
- `_identity_path` 新路径优先；旧路径只读兜底。
- `questions.py` / `identity_inbox.py` 引用 `_identity_path` 的地方自动跟随；旧功能保留。
- 更新 `tests/test_identity.py` 等路径断言（如有）。

验收：横幅/身份读写正常；旧库可读；新库写新路径。

---

## P1 人生导师后端

### P1-1 角色

文件：`dsh/roles/mentor.md`（由 `yiling.md` 改名）；`backend/app/ai_roles.py`

- 角色 frontmatter 见设计 5.1；四个 mode 段落：converse / synthesize / bazi / question_draft。
- `ENTRIES` 新增 `self.mentor`, `self.archive`, `self.bazi`；保留旧 entries。
- `RoleService._default_selectors`：mentor → `[{"entry":"self_archive"},{"entry":"mentor_memory"},{"entry":"journal"},{"entry":"tasks"},{"entry":"recent_notes"}]`。
- `_max_chars` mentor 默认 40000（synthesize 可 60000）。
- `threads(role_id="")` 支持过滤；`thread(thread_id)` 不变。
- `chat` 返回 `pending`（新待确认卡）与 `memory_notes_written`。
- 旧 `yiling` 角色不再注册；文档标为历史。
- `ai_schemas.py`：
  - `ArchiveVerdict`（字段见设计 5.7）
  - `MemoryNote`（kind/text/sources）
  - `QuestionDraft`（prompt/target_section）
  - `Reply` 增加 `archive_verdict`, `memory_notes`, `questions`。
  - `SynthesisResult`（portrait/portrait_confidence/archive_patches/memory_updates/question_candidates/gaps）。
- `_create_proposals`：mentor 的 `archive_verdict.decision == "accept"` → 调 `mentor_pending.create_pending`，不再走 `identity_patch`；`memory_notes` → `mentor_memory.append_timeline`；`questions` → 追加动态题库（或返回给前端）。

验收测试：
- mentor 角色可加载；entries 合法。
- 模拟 reply：accept 生成待确认；reject 不生成。
- 待确认卡内容含 section/new_text/sources/verdict。
- 旧 yiling 会话列表可读。

### P1-2 上下文选择器

文件：`backend/app/ai_context.py`

新增：
- `self_archive`：读 `self_archive.overview_context()`（active 条目：本子/类型/标题/正文/证据/来源），预算 12k。
- `mentor_memory`：读 `mentor_memory.context()`（核心 3k + 时间线 6k + 问答 4k），预算 13k。
- `recent_notes`：读最近 30 天知识库笔记（标题+摘要），预算 6k；可选 `days`。
- `ops_summary`：任务/习惯/健康/财务摘要，预算 6k。
- `bazi_chart`：读 `self_bazi.chart_context()`，预算 6k。
- `SUPPORTED_ENTRIES` 扩展；`FUTURE_ENTRIES` 清空对应项。

验收测试：每个 selector 有内容/空库提示；预算截断标记；来源编号稳定。

### P1-3 分层记忆

文件：`backend/app/mentor_memory.py`

- 路径常量；`ensure_memory`。
- `read_core`, `write_core`（备份旧版到历史）；`append_timeline(entries)`；`append_qa(qa)`；`history(date)`。
- `context()` 返回拼接文本 + 来源元数据。
- 上限：核心 3000 字（超出截断/报错？写时截断并提示）；时间线单条 300 字；问答单条 800 字。
- 时间线去重：同日期+文本 hash。
- 写入都 `ensure_vault_writable`。

验收测试：追加/读取/上限/去重/只读。

### P1-4 每日三问

文件：`backend/app/mentor_questions.py`

- `BANK`（内置 ~60 题，按 11 本子）；首次 `ensure_bank` 写 `提问/题库.md`。
- `parse_bank(text)`：按 `## 本子标题` + `- [id] 问题｜类型=...｜优先级=...｜提示=...` 解析；容错。
- `ensure_today(vault, date=None)`：读/建 `提问/YYYY-MM-DD.md`；选 3 条（规则见设计 5.5）。
- `list_today`, `draft_today(role_service)`：调用 mentor/question_draft，写拟答/来源。
- `answer(vault, qid, text, *, accept_suggested=False)`：
  - 写问答记忆；
  - 若 accept_suggested 或问题有 target section：写档案条目（`origin: question`）；
  - 更新当天卡片状态；
  - 返回 entry/path。
- `skip(vault, qid, forever=False)`：更新状态；`state.json` 冷却索引。
- `rebuild_state(vault)`：从历史文件重建。
- 只读拒绝写入。

验收测试：题库解析、每日 3 条、冷却、永久拒绝、回答写记忆/条目、跳过。

### P1-5 synthesize

文件：`backend/app/self_archive.py` 或 `mentor_synthesize.py`

- `synthesize(role_service, vault, *, range_days=14)`：
  - 组装上下文（设计 5.3）；
  - `role_service.run_once("mentor","synthesize", prompt, entry="self.archive", selectors=[...])`；
  - 解析 `SynthesisResult`；
  - 写画像（备份）、待确认卡、时间线、动态题库；
  - 返回 `{portrait, patches, memory_updates, questions, sources, truncated}`。
- 失败：不写半成品；返回 502/503。
- 需要 `confirm=true` 的 API 包装。

验收测试：mock role 返回固定 JSON → 文件落盘正确；非法 JSON 不写；AI 失败保留旧画像。

### P1-6 路由

文件：`backend/app/self_archive_api.py`（或 `self_archive.py` 内）、`application.py`

- 实现设计第 8 节 API；预览/确认/错误码；只读 409。
- `application.py` 注册 `create_self_router(...)`、`create_mentor_router(...)`、`create_self_bazi_router(...)`。
- 路由依赖 `journal_data_guard`（与现有数据接口一致）。

验收：TestClient 覆盖主要路径。

---

## P2 自我档案前端

### P2-1 外壳与路由

文件：`frontend/index.html`、`frontend/app.js`、`frontend/self.js`

- `self-view` 改为 `self-shell`（header + body）。
- `PANE_LOADERS["self-view"] = () => { initSelf(); loadSelf(); }`。
- `GO_TARGETS`：
  - `identity/profile/memory` → `self` 总览；
  - `bazi` → `self` + `bazi` 子页；
  - 可选 hash `#self/mentor`、`#self/bazi`。
- `self.js`：
  - 左栏分组（自我档案/人生导师/八字系统），二级展开/折叠；localStorage。
  - 子页路由：`overview`, `book:<key>`, `mentor:<tab>`, `bazi:<tab>`。
  - 事件委托：本子点击、折叠、迁移、更新、辩论、待确认操作、提问操作。
  - 加载状态/错误/toast。
- 顶部 header：复用 `.pillar-head`，图标 `我`，eyebrow `四库 · 自我档案`，右侧搜索/更新自我档案/在 Obsidian 打开。

验收：模块切换正常；刷新保留子页（hash 或 state）；窄屏可用。

### P2-2 总览与本子

文件：`frontend/self-archive.js`、`frontend/self-archive-model.js`、`frontend/self.css`

- `BOOKS` 常量与后端一致（测试校验）。
- 总览：
  - 本子网格（11 卡）显示标题/副标题/条目数/最近更新/待确认角标；
  - 网格下方画像卡（Markdown 渲染、更新时间、来源折叠、`更新自我档案` 按钮）；
  - 顶部迁移卡（若 preview 显示待迁移）。
- 本子页：
  - 标题/条目数/最后更新/在 Obsidian 打开；
  - 按 entry_type 分组；条目卡（标题、置信度、正文、证据/反例/待验证、来源、`和导师辩论这条`）；
  - `看历史` 切换 active/superseded。
- 空状态：说明「只有人生导师能写；点更新自我档案或去对话」。
- 画像/条目 Markdown 渲染复用 `pillar-markdown.js`。

### P2-3 迁移卡与更新

- 迁移预览 modal：列出源→目标、冲突、备份位置；确认执行；执行中状态；完成后刷新。
- `更新自我档案`：确认 modal → 调 `/api/self/synthesize` → 成功 toast + 跳待确认；失败保留旧画像。
- 待确认（档案页也可显示角标，详情在导师页）。

### P2-4 测试

- `self-archive-model.test.mjs`：BOOKS、分组、画像/条目/待确认 markup。
- 手工：总览、本子、辩论入口、迁移、更新。

---

## P3 人生导师前端

### P3-1 页面骨架

文件：`frontend/mentor.js`、`frontend/mentor-model.js`、`frontend/self.css`

- 三栏：会话列表 / 消息流 / 上下文面板；窄屏折叠。
- 顶部按钮：新对话、更新自我档案、刷新。
- 右面板 tabs：今日提问 / 待确认 / 记忆 / 资料。
- 加载 `/api/mentor/overview`。

### P3-2 对话

- 复用 `/api/ai/chat`（role_id=mentor, mode=converse, entry=self.mentor）。
- 会话列表：`/api/mentor/threads`；点击加载 `/api/ai/threads/{id}`；旧 yiling 分组只读。
- 消息渲染：Markdown、来源、耗时、待确认卡内联。
- 发送：Enter / Ctrl+Enter；composing 保护；失败保留草稿。
- 辩论入口：从档案条目带入预填文本；消息里带 entry_id/title。

### P3-3 待确认

- 列表 `/api/mentor/pending`；卡片显示 new/old、证据、来源、理由；`采纳/改后采纳/驳回`。
- 采纳后刷新档案/画像/角标；错误 toast。

### P3-4 今日提问

- 3 卡；状态 pending/answered/skipped/declined；拟答/来源；`生成导师拟答`（每天一次）。
- 操作：确认写入/改后确认/自己回答/跳过/不再问。
- 工作台条 `mentor-strip.js`：读 `/api/mentor/questions/today` + `/api/self/pending` 计数；点击进入导师页。

### P3-5 记忆

- 显示核心/时间线/问答摘要 + 路径 + 在 Obsidian 打开；不提供 torch 编辑。

### P3-6 测试

- `mentor-model.test.mjs`：消息、待确认、问题卡、记忆摘要。
- 手工：对话/辩论/采纳/提问/工作台条。

---

## P4 八字系统

### P4-1 共享引擎

- 新建 `backend/app/bazi_engine.py`，从 `love_bazi.py` 抽取常量与函数；`love_bazi.py` 改为 import。
- 现有 `tests/test_love_bazi.py`（若有）必须继续通过；没有则补一个最小测试。
- 不改变恋爱八字 API 字段。

### P4-2 个人命盘

文件：`backend/app/self_bazi.py`

- 路径：`03_八字系统/命盘.md`、`解读/`、`历史/`。
- `read_profile`, `write_profile`（revision）、`chart_from_profile`, `generate_reading(role_service, topic)`, `list_readings`, `read_reading`, `delete_reading`（备份）。
- 解析旧 `07_我的八字参考` 的 frontmatter/正文（迁移在 P0，P4 可补解析）。
- API 见设计 8.3。

### P4-3 前端

文件：`frontend/self-bazi.js`、`frontend/self-bazi-model.js`、`frontend/self.css`

- 表单：姓名/性别/生日/时间/地点/四柱手动；草稿；试算；保存。
- 命盘：四柱表、五行条、十神/藏干、日主/身强弱/格局/喜忌、大运时间轴、流年表。
- 侧栏：主题按钮、AI 解读、追问、免责声明。
- 历史：解读列表/查看/删除/导出。

### P4-4 测试

- `tests/test_self_bazi.py`；`frontend/tests/self-bazi-model.test.mjs`。
- 手工：排盘与旧八字参考一致（癸未 辛酉 丙戌 戊子）。

---

## P5 收编与全局样式

- `index.html`：
  - 主侧边栏生活运营图标 `生` → `营`；
  - `self-view` 新壳；
  - 工作台 `workbench-memory` → `workbench-mentor`；
  - 回收中心头部改 `.pillar-head`。
- `frontend/app.js`：初始化 `mentor-strip`；移除 `initMemoryHub`/`loadSelfHub`；`self-view` 新加载器。
- `frontend/pillars.js`：`PILLAR_META.ops.icon = "营"`；self 不再走 generic 渲染（或在 `loadPillarModule` 里短路）。
- `frontend/memory-hub.js`/`memory-strip.js`：保留文件但不再引用；`memory-event-dialog` 可移除或保留（推荐移除旧对话框，新导师页有自己的提问 UI）。
- 旧忆灵入口：`roles.js` 的 `memory-hub` 按钮移除；工作台条指向新页。
- 文档：更新 `docs/STATUS.md`、`docs/FEATURE_MAP.md`、`docs/VAULT_TEMPLATE.md`、`docs/DATA_HANDBOOK.md`、`docs/MEMORY_HUB.md`（标为旧）、`docs/AI_ROLES.md`。

验收：
- 工作台条显示导师；点击进入导师页；
- 回收中心标题样式统一；
- 生活运营图标为营；
- 旧忆灵面板不再出现。

---

## P6 回归与文档

- 跑：
  ```bash
  .venv/Scripts/python.exe -m pytest tests/test_self_archive.py tests/test_self_migration.py tests/test_mentor_memory.py tests/test_mentor_questions.py tests/test_self_bazi.py tests/test_ai_roles.py tests/test_ai_context.py -q
  node --test frontend/tests/self-archive-model.test.mjs frontend/tests/mentor-model.test.mjs frontend/tests/self-bazi-model.test.mjs
  .venv/Scripts/python.exe -m pytest -q
  node --test frontend/tests/*.test.mjs
  ```
- 手工验收清单（设计第 13 节）。
- 更新设计文档的「状态」与本文档的「进度」。
- 检查 `.gitignore` 是否包含新数据目录（不应忽略 `01_自我档案`）。

---

## 实施记录（边做边填）

| 日期 | 阶段 | 完成内容 | 测试 |
|---|---|---|---|
| 2026-11-01 | 设计 | 设计文档 + 实施计划 | — |
| 2026-11-01 | P0 | 自我档案目录/条目/画像/待确认/迁移/身份新路径 | `test_self_archive`、`test_self_migration` 通过 |
| 2026-11-01 | P1 | mentor 角色、分层记忆、每日三问、全量整理、API | `test_mentor_memory/questions/flow`、`test_ai_roles` 通过 |
| 2026-11-01 | P2 | self 三部分外壳、左栏、总览/本子/画像/迁移卡 | `self-archive-model` 测试；浏览器探针通过 |
| 2026-11-01 | P3 | mentor 三栏/会话/待确认/记忆/三问/工作台条 | `mentor-model` 测试；浏览器探针通过 |
| 2026-11-01 | P4 | bazi_engine、个人命盘/解读/历史、八字页 | `test_self_bazi`、`self-bazi-model` 通过 |
| 2026-11-01 | P5 | 收编旧入口、顶部标题、回收中心、生活运营图标 | `memory-hub`/`self-archive` 前端测试；v1 浏览器探针通过 |
| 2026-11-01 | P6 | 文档更新与回归 | 目标测试 56 + 相关回归 84 通过；全量待重跑 |

---

## 上下文恢复提示

如果新会话里上下文被压缩，按以下顺序恢复：

1. 读 `docs/SELF_ARCHIVE_MENTOR_BAZI.md`（需求与规格）。
2. 读本文件的「总览」和当前阶段。
3. 用 `git status` 看已改文件；用 `grep -n "TODO(P0)\|TODO(P1)..."` 找未完成标记（实现时在代码里保留阶段注释）。
4. 先跑当前阶段测试，再继续；不要重做已完成阶段。
5. 所有文件操作使用绝对 Windows 路径。
