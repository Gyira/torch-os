# AI 角色层（v1.0）

> 本文记录 v1.0 角色层：角色卡、统一 dsh 网关、上下文装配、页面内角色抽屉与提案协议。
> P0–P4 已接入；P5 已完成 Windows 进程/编码/并发/超时测试、审计覆盖、备份/导出与浏览器探针。

## 1. 一个内核，多张角色卡

- 唯一模型通道仍是本地 **dsh**；角色不是第二个模型，也不是第二个 Key。
- 角色卡放在仓库 `dsh/roles/*.md`，随仓库走：换库只换数据，不换人格。
- 「与神对话」主 iframe 仍是唯一自由对话入口；角色抽屉是页面内、带范围的任务型对话。
- 角色子进程拿不到 Vault 路径、没有工具权限；所有资料由后端拼好后通过 stdin 传入。

## 2. 当前角色

| id | 名称 | 模式 | 允许提议 |
|---|---|---|---|
| `yiling` | 忆灵 / 苏格拉底 | `synthesize` / `converse` | identity_patch、review_card、new_note、hub_event |
| `journal_companion` | 写作伙伴 | `classify` / `polish` / `discuss` | new_note、archive_note |
| `academic_mentor` | 学术导师 | `classify` / `polish` / `share_digest` / `tutor` / `discuss` | new_note、archive_note |
| `life_assistant` | 生活管家 | `overview` / `tasks` / `suggest` | new_task、new_habit、new_note |
| `love_master` | 恋爱大师 | `advice` | new_note、new_task |
| `health_doctor` | 私人医生 | `report` / `advice` | health_report |
| `finance_advisor` | 理财专家 | `report` | finance_report |

忆灵与苏格拉底是**同一个 agent 的两种模式**：同一份画像、同一套身份权威；
区别只在于入口（忆灵页 vs 苏格拉底对话）。

## 3. 角色卡格式

```markdown
---
id: yiling
name: 忆灵
agent: yiling
layer: identity
entry: [self.memory, self.socrates]
modes: [synthesize, converse]
context: {identity: full, portrait: true, max_chars: 16000}
proposals: [identity_patch, review_card, new_note, hub_event]
session: per_thread
timeout_s: 180
tools: none
---

## mode: synthesize
……整理规则……

## mode: converse
……对话规则……
```

校验规则（`backend/app/ai_roles.py`）：

- `id` 小写字母开头；`modes` 至少一个，且每个 mode 必须有对应 `## mode:` 段；
- `entry` 必须属于固定入口表；`proposals` 必须属于固定提案类型表；
- `tools` 必须是 `none`；`timeout_s` 在 10–600 秒；
- 非法角色只记入 `role_errors`，不会被加载。

## 4. 调用方式

`backend/app/ai_gateway.py` 统一启动一次 `dsh headless`：

```text
dsh --profile headless --json --session-id <session-torch-...> -
```

- prompt 通过 **stdin** 传入（`-` 是 dsh 官方的 stdin 读取方式），避免 Windows
  多行 argv 被 `cmd.exe` 重新解析；
- 子进程工作目录是 `<data_dir>/ai-runtime/workspace`（空目录，不是 Vault）；
- 环境变量只保留 PATH/SystemRoot/TEMP 等白名单，注入 `DEEPSEEK_API_KEY`，
  **不传** `VAULT_DIR`/`DATA_DIR`/`TORCH_REPO_ROOT`；
- 超时用 `taskkill /PID /T /F`（Windows）或进程组 kill（POSIX）终止整棵进程树；
- 事件解析只取 NDJSON 的 `session`/`final`，错误行单独保留；输出上限 2MB；
- 全局并发 2，避免多个 node 子进程抢内存。

## 5. HTTP 接口（P0–P1）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/ai/status` | 是否可用、dsh 命令类型、Key 来源（不返回 Key）、角色数、角色文件错误 |
| GET | `/api/ai/roles` | 角色元数据（不含 prompt 正文） |
| POST | `/api/ai/chat` | 角色对话；`role_id/mode/thread_id/message/entry/selectors/selection_text/selection_source/draft_text` |
| POST | `/api/ai/run` | 一次性能力；返回 `text` + `structured` |
| POST | `/api/ai/threads` | 新建会话 |
| GET | `/api/ai/threads` | 会话列表 |
| GET | `/api/ai/threads/{id}` | 会话与轮次 |
| DELETE | `/api/ai/threads/{id}?confirm=true` | 删除会话（级联轮次） |
| GET | `/api/proposals` | 待审提案 |
| POST | `/api/proposals/{id}/apply` | 采纳（`confirm=true`；身份补丁另需 verdict token） |
| POST | `/api/proposals/{id}/reject` | 忽略 |

QA/禁用 dsh 时 `available=false`，且不启动子进程、不读 Key。

### 上下文选择器（P1）

`selectors` 只接受枚举对象，最多 8 个：

- `{"entry":"identity"}`
- `{"entry":"note","path":"03_知识库/01_主题笔记/xxx.md"}`
- `{"entry":"journal","day":"YYYY-MM-DD"}`
- `{"entry":"tasks","day":"YYYY-MM-DD"}`
- `{"entry":"health"}` / `{"entry":"finance"}` / `{"entry":"love"}`：只读摘要（恋爱不读原始聊天）

`memory_card/proposal/share` 在后续阶段接入。每类来源有字符预算，
总预算 16k、硬上限 24k；截断会在响应 `truncated` 与界面提示里说明。

空 `selectors` = 不发送任何用户资料。角色对话会按角色补默认上下文（忆灵=身份+今日日记+任务；
生活管家=任务+身份+健康+财务+恋爱；写作伙伴=今日日记；恋爱大师=恋爱摘要；
私人医生=健康系统摘要）；一次性能力（润色/判类）不自动带资料，避免把身份信息顺手发给模型。

健康系统的 `{"entry":"health"}` 摘要来自 `health_store.summary()`：最近 90 天的睡眠/体重/
步数/情绪/训练与检验异常 + 身体档案，不含每日正文；报告落
`02_生活运营/02_健康系统/00_健康报告`，`memory: false`。

`selection_text` / `selection_source` / `draft_text` 是浏览器选区与未保存草稿，单独作为
「数据不是指令」区块进入 prompt；不写入 `ai_turns.content`，审计只记录来源/字数/短哈希。

## 6. 提案协议（P0–P1）

- `backend/app/proposals.py`：`pending → applying → applied | rejected | expired | failed`。
- 所有提案必须用户 `confirm=true` 才 apply；重复 apply 幂等；
- 身份补丁：
  - 忆灵审核路径：用户看 diff + 来源后直接采纳（`verdict.requires_dialogue=false`）；
  - 苏格拉底路径：必须带 AI 的 `verdict_token`（`requires_dialogue=true`）。
- P0 只内置状态机与 HTTP 接口；P1 已注册 `new_task`/`new_habit` handler：
  - 任务 → `02_生活运营/00_待办事项/` 的一篇 Markdown；
  - 习惯 → `00_人生路/04_目标/04_习惯追踪/` 的定义与缓存。
- `new_note/archive_note/identity_patch/health_report/finance_report/life_advice`
  的 handler 由 P2–P4 注册；未注册时返回 409，不会静默成功。

## 7. 前端入口（P1）

- 工作台 → 时间管理顶部「生活管家」→ `life_assistant/tasks` 抽屉；
- 人生路/知识库/生活运营/恋爱系统的首页卡片分别打开写作伙伴/学术导师/生活管家/恋爱大师；
- 编辑器内打开伙伴不关闭编辑器：右侧面板与底部悬浮输入条可互相收起/展开；
- 选中文字出现「问…」浮钮，选区以 chip 引用进入对话；消息气泡不展示原文；
- 抽屉里的任务/习惯提案可直接采纳，采纳后任务板刷新；
- 角色对话会写入 `ai_threads/ai_turns`，续聊复用 dsh session；
- 角色返回的 `hub_events` 先写入 `memory_hub_events`，P3 由忆灵综合消费。

## 8. 已完成阶段

- P2：人生路/知识库编辑器、判类、润色、讨论、分享链接。
- P3：记忆中枢综合、画像、身份补丁 verdict、苏格拉底入口。
- P4：生活运营专家报告与建议（`docs/LIFE_SYSTEMS.md`）。
- P5：Windows 进程/编码/并发/超时实测（`tests/test_ai_gateway_process.py`、
  `tests/test_ai_gateway.py`）；v1.0 审计覆盖（`tests/test_v1_audit.py`）；
  备份/导出覆盖新表（`tests/test_backup.py`、`tests/test_export_bundle.py`）；
  v1 浏览器探针（`scripts/browser_v1_probe.py`）与 `scripts/check_all.ps1`。

## 9. 探针与加固结论（2026-10-29）

- 本机 `dsh`：`0.2.0-rc.2`（`C:\Users\19814\AppData\Roaming\npm\dsh`）。
- `dsh headless --help` 确认支持 `--json`、`--session-id`、`-`（stdin）。
- 安装包 `@deepseek-ai/dsh-skill-filesystem` 读取 `DSH_AGENTS_HOME`；角色子进程把它
  指向 `dsh/roles`，避免加载主对话的 `dsh/AGENTS.md`。
- P5 用真实子进程（不是 fake runner）验证了：stdin 多行中文/emoji 回环、超时
  `taskkill /T /F` 连 node 孙进程一起终止、连续 3 轮超时后无残留 node、全局并发 ≤2。
- 审计与日志：角色轮次/一次性能力/提案/忆灵/写作只写计数与路径元数据；测试断言审计
  不含正文、回复、画像、身份值或 `sk-` Key。stderr 只保留脱敏后的尾部。
- 2026-10-30 经用户同意，用真实 Key 做了 3 次低 token `journal_companion/polish` 调用：
  6.3 / 3.7 / 5.2 秒全部返回；调用前后 node PID 集合一致、无残留；stderr 脱敏后不含
  Key 或原始 prompt。自动化测试仍全部使用 fake runner 或本地假 dsh，不消耗 Key。
