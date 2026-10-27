# 与神对话：一个记得住我的导师

> 2026-10-27 设计定稿。这份文档回答一个问题：**上下文是有限的，怎么让 dsh 每次都懂我。**

## 一句话

不靠"更长的上下文"，靠三件事：**一张几百字的记忆内核 + 按需检索 + 每次把结论写回库**。
注意力留在当事人身上，记忆留在磁盘上。

## 为什么是 skill，不是页面、不是插件

| 方案 | 结论 | 原因 |
|---|---|---|
| 在前端加一个"导师"页面 | ✗ | 工作台里的「与神对话」已经就是完整的 dsh 界面（`frontend/dsh-pane.js`）；再加一页等于两个灵魂 |
| 写一个 dsh 插件/预设 | △ | 能做，代价是改 `~/.dsh/profiles/<profile>/cordis.patch.yml`——那是**机器配置**，不在仓库里，换机器就丢，也违背"规则跟着项目走" |
| **本地 skill**（选它） | ✓ | `dsh/skills/torch-mentor/` 落在 `$DSH_AGENTS_HOME/skills`，dsh 自动发现、热加载、不重启；规则在仓库里，换库换机器都跟着走 |

已经验证：写完 `SKILL.md` 后，**同一个会话里**下一次模型步骤的 skill 目录就出现了 `torch-mentor`，无需重启。
入口在 `dsh/AGENTS.md` §1 第 3 条：关于"我"的问题先加载它。

## 变与不变

```
规则（仓库，永不随库变）        数据（数据库，随时可换）
dsh/AGENTS.md                  Vault 的 180 篇 Markdown
dsh/skills/torch-mentor/       01_自我档案 / 日记 / 任务 / 目标
scripts/session_brief.py       索引库 .torch-os
scripts/archive_note.py        （只是缓存，可重建）
```

规则里**一个字都不写我是谁**。名字、三观、目标、专业、偏好，全部运行时从库里读。
规则只写"怎么读、怎么记、怎么问"。所以换库 = 换记忆，不换方法；同一个方法能带起任何一个新库。

## 四层记忆

| 层 | 是什么 | 在哪 | 成本 |
|---|---|---|---|
| L0 人格 | 三重身份、语气、边界、检索协议 | `dsh/skills/torch-mentor/SKILL.md` | 按需加载，约 2k token |
| L1 内核 | 我是谁、在忙什么、最近 7 天、最近结论、记忆密度 | `session_brief.py --memory` | 每次会话约 600–900 字 |
| L2 检索 | 四库全文 + 已确认记忆 | `Grep` / `mcp__torch__memories` | 每次问答最多 3 次，只读命中段 |
| L3 回流 | 结论润色成笔记，回到 L1/L2 能命中的位置 | `archive_note.py` → 四库 | 每次归档 1 篇 |

循环：**L1 让我开口就认识你 → L2 让我答得准 → L3 让下次的 L1 更厚。**
不闭环的那部分，就永远是"换了个对话就不懂我"。

### 为什么 L1 必须很短

内核卡是每个会话都要付的固定成本。全塞进去 = 每次都烧一遍 token，且越塞越糊。
现在的取舍：身份 6 项 + 3 条主线 + 3 条日记 + 5 条结论 + 一行密度，**超过 2000 字就是设计错误**（有测试钉住）。

### 为什么笔记第一行必须是 `> 摘要`

L1 只读摘要，不读正文。摘要写不出结论的笔记，等于没写——检索阶段它会被跳过。
这条约定由 `archive_note.py` 提示、由 `references/note-templates.md` 约束。

## 身份候选：对话里说的「我是谁」，不能自动进档案

一句话听错，就能把错误的三观写进内核。所以身份信息走**三段式**，中间一定隔着我这一次点击：

```
对话里出现稳定事实（称呼/三观/底线/长期偏好/重要经历）
  │
  ├─ dsh：scripts/identity_draft.py add --kind field|memory --text … --evidence …
  │       只写 `.torch-os/身份待确认/YYYYmmdd-HHMMSS-<kind>-<hash>.md`，不碰四库、不碰数据库
  │
  ├─ 我：自我档案右栏「身份待确认」逐条看（含我的原话，便于复核）
  │
  ├─ 采纳 ─→ kind=field  ：写进身份卡对应字段（字段已有内容就拒绝覆盖，让我自己去合并）
  │           kind=memory ：新建一篇 `01_自我档案/02_记忆与自传` 笔记
  │          两种情况卡片都归档到 `已采纳/`，留痕可查
  │
  └─ 丢弃 ─→ 卡片挪进 `已丢弃/`，四库一个字不动
```

为什么放 `.torch-os/` 而不是四库：

- `index_policy` 对 `.torch-os` 是 `skip`，候选不会变成索引噪音，也不会被当成"我的日记"；
- dsh 只写文件，不需要连数据库、不需要会话 id、不会和 Obsidian 抢写；
- 「采纳」是唯一写入点，四库页面其余部分仍然只读——和 `docs/FEATURE_MAP.md` 的边界一致。

接口：`GET /api/identity/drafts`、`POST /api/identity/drafts/{name}/accept|reject`；
自我档案的 `GET /api/pillars/self/overview` 会顺带返回 `drafts`，面板一次请求就够。

## 数据流

```
开场   session_brief.py --full
         ├─ build_memory_card(vault)   L1
         └─ build_brief(vault)          态势
问答   Grep 四库 / mcp__torch__memories   L2
收尾   三问 → 提案 → 我点头
         ├─ 身份/经历类 → identity_draft.py → .torch-os/身份待确认/ → 我在自我档案点头
         └─ 结论/知识类 → archive_note.py --chapter … --body-file …
              └─ backend.app.pillar_notes.create_note
                   → 四库章节 + frontmatter（chapter/type/status/source）
                      → 下次 L1 的「最近结论」里出现
```

`archive_note.py` 不自己写盘：它复用 `pillar_notes.create_note`，所以章节校验、`safe_note_stem`、
`-2` 去重、frontmatter 字段和界面创建的笔记完全一致。分类与措辞归模型，机械部分归代码。

## 换库时会发生什么

1. 规则不动（仓库里的 `AGENTS.md` + skill）；
2. 内核卡立刻反映新库：身份、目标、日记、归档全是新库的；
3. 记忆密度大概率从"厚"掉到"薄"→ skill §7 触发"先认识我"访谈，而不是假装认识；
4. 旧库的索引缓存（`.torch-os`）不参与，四库 Markdown 才是事实来源。

## 还没定的事（需要你拍板）

1. **追加还是新建**：同一主题第 5 次出现时，是继续新建一篇，还是允许往既有主题笔记的
   `## 对话记录` 小节追加？现在 AGENTS.md 的规矩是"只新建"——安全，但会长成一堆日期孤儿。
   追加需要解决并发与备份，得先改 `markdown_store.guarded_write` 的入口。
2. **要不要一个独立预设**：把 `standard` 预设克隆成"导师"，人设写进 `dsh-persona`。
   好处是身份更硬；代价是 profile 配置不在仓库里，得在每台机器上重配一次，而且所有代码活也得跟着换预设。
3. **隐私边界**：`02_生活运营` 的健康/账务/证件目前也在检索范围内。要不要在 skill 里给这几章加
   "只在被问到、且不引用到对外产物"的硬限制？（现在只有软约束）
4. **每日提醒**：dsh 的 `schedule` 只在会话里生效；真正的每日提醒归工作台 `scheduler.py`。
   两套要不要打通，是产品决定，不是 skill 决定。
5. **采纳之后要不要顺手生成记忆候选**：现在 `kind=memory` 采纳只建笔记，不写 `memories` 行，
   所以 `mcp__torch__memories` 还查不到它（Grep 查得到）。要打通得在采纳时补一次
   `upsert_document + replace_document_memories`，但那批候选还要再确认一次——双层确认值不值得，得你定。

## 怎么验证它没退化

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_mentor_workflow.py tests\test_dsh_workflow.py -q
```

`test_mentor_workflow.py` 钉住四件事：

- skill 在 `dsh/skills/` 下、frontmatter 合法、正文目录必须是 `vault_layout` 里真实存在的章节；
- 规则里不许出现个人事实（"华东交通""铁路""考研"这些词出现即失败）；
- `archive_note.py` 只落四库章节、绝不覆盖、`--dry-run` 不落盘、缺摘要会报警；
- 内核卡读得到 `mottos` 列表，且不会把空日记模板当成"今天写了"。

## 相关文件

| 文件 | 作用 |
|---|---|
| `dsh/skills/torch-mentor/SKILL.md` | 导师协议正文（人格、检索、归档、边界） |
| `dsh/skills/torch-mentor/references/note-templates.md` | 各章节的笔记骨架与摘要写法 |
| `dsh/AGENTS.md` | 全局规则；§1 第 3 条是加载 skill 的入口 |
| `scripts/session_brief.py` | `--memory` 内核卡 / `--full` 内核 + 态势 / 默认态势 |
| `scripts/archive_note.py` | 归档出口（`--list-chapters` / `--dry-run`） |
| `scripts/identity_draft.py` | 身份候选投递（`add` / `list`），只写 `.torch-os/身份待确认/` |
| `backend/app/identity_inbox.py` | 候选队列的读取、采纳（写身份字段 / 记忆与自传）、丢弃 |
| `frontend/identity-inbox.js` | 自我档案右栏的「身份待确认」卡片（纯函数，`node --test` 可测） |
| `tests/test_mentor_workflow.py` | 上面这些的守卫 |
| `tests/test_identity_inbox.py` | 候选队列：不越界、不覆盖、丢弃不动四库 |
| `frontend/tests/identity-inbox.test.mjs` | 面板渲染与转义 |
