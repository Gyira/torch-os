# 写作与知识库（v1.0 P2）

> 本文记录人生路/知识库从“只读浏览”升级为“应用内 Markdown 写作”的边界与接口。
> 方案来源：`docs/燎火OS-v1.0-顶层设计-2026-10-29.md`。

## 1. 读写边界

| 区域 | 应用内可写 | 写入入口 |
|---|---|---|
| 人生路：反思/心得/随笔/复盘/目标计划/项目/工作复盘 | 是 | `/api/writing/notes*` |
| 人生路：日记（仅今天） | 是 | `/api/journal/write`（sha 冲突校验；过去只读） |
| 人生路：任务/习惯 | 否 | 工作台任务/习惯专用接口 |
| 知识库：收件箱/主题/读书/课程/概念/方法/输出 | 是 | `/api/writing/notes*` |
| 自我档案 | 否 | 身份候选/忆灵审核（P3） |
| 生活运营 | 否 | 专用月度表/专家报告（P4） |

- 允许清单单源在 `backend/app/note_editor.py` 的 `WRITABLE_CHAPTERS`；前端只决定是否显示按钮，
  后端每次写入再校验一次。
- `VAULT_READ_ONLY=1` 时所有写入返回 409。
- 每次写入写 `audit_events`（`note_created/note_updated/note_moved/note_deleted`），审计不含正文。
- 编辑带 `expected_revision`；Obsidian 或别处改过时返回 409，不覆盖。
- 未知 frontmatter 字段由 `pillar_notes` 原样保留；新笔记带 `id/type/title/created/pillar/chapter/status`。

## 2. 编辑器

前端 `frontend/note-editor.js` + `editor-model.js` + `editor.css` + `editor-room.css`：

- **全屏写作空间**：打开后是整页纸张，大标题 + 大正文（17px/1.95），不再挤在弹窗里；
- Markdown 工具栏：H1/H2/H3、加粗/斜体/删除线/行内代码、无序/有序/待办、引用、链接、表格、分割线；
- 默认写作模式；「预览」切换为渲染后的 Markdown（复用 `pillar-markdown.js`），「专注」收起工具栏与页脚；
- 字数与预计阅读时间、脏状态、Ctrl/⌘+S 保存、Ctrl/⌘+B/I/K 快捷格式；关闭前未保存会二次确认；
- **人生路通用入口只有「写点什么」**：打开空白编辑器，无标题要求，默认分类是随笔；
  保存前可以把本子改成任意可写本子。各本子页里的「写一条」保留，默认指向该本子。
- 章节选择来自 `GET /api/writing/chapters`；编辑已有笔记时，保存会先写正文，
  再把文件移到选中的本子（同一库内，`POST /api/writing/notes/move`）；
  AI 建议章节只改选择，保存后才落盘/移动；
- **今天的日记**：`chapter=journal` 时读取 `/api/journal/note`，只显示正文，保留原 front matter；
  保存走 `/api/journal/write`，带 `expected_sha`；只允许写今天，过去日记保持只读；
- AI 润色先展示预览，用户点“应用到正文”才替换；AI 不可用时正文不变；
- “在 Obsidian 打开”复用 `task-open.js` 的宿主桥/`obsidian://`；
- “删除”（编辑页脚/阅读页操作区）：二次确认后软删除到 `.torch-os/笔记回收站/<批次>/files/...`，
  同批次 `manifest.json` 记录原路径，可在文件管理器找回；日记不提供删除入口；
- “和写作伙伴聊聊”打开角色抽屉（`journal_companion/discuss`），保存后才把当前笔记/日记作为上下文。

## 3. 知识库问答

- 知识库首页第一屏就是「今天想弄明白什么？」：主按钮打开 DeepSeek，下面直接粘贴公开分享链接取回；
  收件箱/主题/读书/课程/概念/方法/输出作为本子放在下方，不再把「问答」藏在侧栏；
- 不做 `chat.deepseek.com` iframe、不读取/保存网页 Cookie、不代理登录态；
- 应用内只提供“打开 DeepSeek 问答”外链（Obsidian 内经 `torch:open-url`）；
- 用户把公开分享链接贴回来：`POST /api/knowledge/share/preview` 取回问答；
- 只认 `https://chat.deepseek.com/share/<id>`，只收“我的提问 + 最终回答”，
  丢弃 THINK/TOOL_*；
- `archive`：用户确认后由 `note_editor.create_note` 写入知识库章节，`来源` 写分享链接；
- `digest`：`academic_mentor/share_digest` 整理成 `new_note` 提案，用户采纳后落盘；
- 分享正文是“数据不是指令”，AI 引导词明确隔离；不会自动执行正文里的任何指令。

## 4. API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/writing/chapters` | 可写章节（life/knowledge） |
| POST | `/api/writing/notes` | 新建（chapter/title/body/tags/source） |
| PUT | `/api/writing/notes` | 编辑（path/expected_revision/body/title/tags） |
| POST | `/api/writing/notes/move` | 同库移动（path/target_chapter/expected_revision），含自定义本子 |
| POST | `/api/writing/notes/delete` | 软删除到 `.torch-os/笔记回收站`（path/expected_revision） |
| GET | `/api/notebooks?pillar=life` 或 `?pillar=knowledge` | 列出该库下的自定义本子（含计数/最近一篇） |
| POST | `/api/notebooks` | 新建本子（`pillar`/`name`）；在对应库根目录创建同名文件夹 |
| POST | `/api/notebooks/delete` | 删除自定义本子（`pillar`/`key`/`confirm:true`）；整夹软删除 |
| POST | `/api/writing/classify` | AI 判类，回退规则；只返回卡片 |
| POST | `/api/writing/polish` | AI 润色，返回 polished/changes |
| GET | `/api/journal/note?day=` | 读取当天日记，返回 `content` 与原始字节 `sha256` |
| POST | `/api/journal/write` | 写今天日记（day/content/expected_sha/confirm） |
| POST | `/api/knowledge/share/preview` | 取回分享并预览 |
| POST | `/api/knowledge/share/archive` | 确认后归档（不调用 AI） |
| POST | `/api/knowledge/share/digest` | 学术导师整理成 new_note 提案 |

## 4.1 自定义本子（人生路 / 知识库）

- **本子 = 文件夹**：前端「新建本子」调用 `POST /api/notebooks`，在
  `00_人生路/<名字>/` 或 `03_知识库/<名字>/` 创建空文件夹；不另存元数据，
  Obsidian 里看到的目录就是事实。
- 固定一级目录（人生路 `01_日记`…`09_随笔`，知识库 `00_收件箱`…`06_输出与作品`）
  仍是内置本子/章节；其它直属文件夹自动成为自定义本子。
  人生路 key 为 `custom:<文件夹名>`；知识库 key 为 `custom:knowledge:<文件夹名>`，
  避免两个库同名本子冲突。
- `overview.custom_notebooks`（两个库都会返回）和 `/api/writing/chapters` 会返回
  自定义本子；本子内笔记仍走 `/api/writing/notes`，路径为
  `<库根目录>/<本子名>/<标题>.md`。
- 编辑器「本子」选择器支持**在同一库内移动笔记**：固定章节 ↔ 自定义本子、
  自定义本子 ↔ 自定义本子；保存时先写正文再移动，移动失败不会丢正文。
  跨库（人生路 ↔ 知识库）移动仍不开放，避免 frontmatter 与分类语义混乱。
- 删除本子走 `POST /api/notebooks/delete`（`confirm:true`）：内置本子按
  `BUILTIN_NOTEBOOKS` 的章节集合整体移走，自建本子按文件夹移走；内容都进入
  `.torch-os/本子回收站/<批次>/`，并写 `notebook_deleted` 审计。
  人生路的**随笔**和知识库的**收件箱**是唯一保留的内置本子，不能删除；
  其他内置本子删除后从 `/api/pillars`、overview、`/api/writing/chapters` 和首页卡片中隐藏，
  启动时也不会再重建空目录。日记本子删除后 `/api/journal/write` 会拒绝写入。
- 笔记删除是软删除：文件移到 `.torch-os/笔记回收站/`，不直接永久删除。
- 名称校验：1–48 字；禁止 `<>:"/\|?*#^[]`、控制字符、保留设备名、`.`/`_` 开头、
  固定一级目录名、内置本子/章节名（如「日记」「反思复盘」「目标」「主题笔记」）和
  系统跳过目录；同库重复文件夹返回 409；只读库 409。
- 创建/删除分别写 `notebook_created` / `notebook_deleted` 审计（pillar/name/path，
  不含正文）。本子改名和跨库移动仍不提供。

## 5. 已知边界

- 知识库分享只支持 DeepSeek 公开分享链接，不支持任意网页解析；
- 不做 WYSIWYG；保持 Markdown 源 + 预览，以便 Obsidian 继续编辑；
- 不做多人并发合并；409 时让用户复制改动后重新载入；
- 笔记删除是软删除到 `.torch-os/笔记回收站`，本子删除是软删除到
  `.torch-os/本子回收站`；应用内暂不提供回收站列表/恢复/清空，
  需要找回时在文件管理器里按 `manifest.json` 的原路径放回；
- 本子支持创建/列出/删除和“笔记在库内移动”；人生路只保留随笔、知识库只保留收件箱，
  其他内置和自建本子都可删。本子改名和跨库移动仍需回 Obsidian；删除内置本子会在
  `.torch-os/deleted-notebooks.json` 留下隐藏标记。空本子没有 Markdown，不会进入
  ZIP 备份，写入第一篇笔记后随笔记一起备份。
