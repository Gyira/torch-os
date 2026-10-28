# dsh 工作流规则（项目内置）

规则写在仓库里，不写进数据库，也不复制到用户目录。torch 每次启动 dsh 时注入环境：

- `DSH_AGENTS_HOME = <torch-os>/dsh` → dsh 把 `dsh/AGENTS.md` 当共享 agents 指令加载；
- `TORCH_REPO_ROOT = <torch-os>` → 规则里的开场简报脚本用它定位仓库。

所以无论当前打开哪个 Obsidian 库、从哪个工作区进「与神对话」，新会话都会自动带上同一套规则。
如果用户自己设置了 `DSH_AGENTS_HOME`，torch 尊重用户的值，项目内置规则就不会加载（需要自己放一份）。

## 规则里有什么

1. **开场**：先跑只读简报（`scripts/session_brief.py`：今日日记、未完成任务、目标/项目、收件箱、最近改动、
   最近由 dsh 归档的笔记），回答开头给两三行态势 + 最多两条建议。
2. **归档**：只有用户确认（“记一下/归档/保存这段”）才写；按分类表新建 Markdown，带
   `type/title/created/来源: dsh/tags`；只新建、不改旧、不碰任务/习惯/账目/身份等托管文件；写完回报路径。
3. **网页版分享链接（2026-10-28）**：用户从 `chat.deepseek.com` 点「分享」复制
   `https://chat.deepseek.com/share/<id>`，贴进「与神对话」。dsh 用 `scripts/share_link.py` 取回问答
   （只读接口 `GET /api/v0/share/content?share_id=`，只收提问与最终回答，丢弃思考/工具过程），
   先报「标题 + 章节 + 摘要」等确认，再按分类表落盘，`来源` 写那条链接。
4. **边界**：不移动、不删除、不批量改；迁移与清理必须用户确认。
5. **会话方式**：一个话题一个会话；跨会话靠四库，不靠聊天记录；说“接着上次”时先跑简报。

## 网页版分享链接

```powershell
# 先看摘要：轮数、字数、每轮预览（不打印全文）
python scripts\share_link.py "https://chat.deepseek.com/share/<id>" --json

# 轮次多时只取需要的轮（从 1 起），写成草稿
python scripts\share_link.py "<链接>" --rounds 1-3 --out .\tmp\share.md
```

- 网页本身是 JS 渲染，直接抓 HTML 只有空壳；脚本走公开只读接口
  `GET https://chat.deepseek.com/api/v0/share/content?share_id=<id>`。
- 只收提问的 `REQUEST` 和最终回答的 `RESPONSE`；`THINK` / `TOOL_*` / `TIP` 一律丢弃，
  所以笔记里不会出现思考过程和搜索结果堆。
- 默认整篇上限 40 万字；超了只给 `--json` 摘要，用 `--rounds` 分轮取。
- 取不到（分享已删、风控返回 HTML、接口改版）会非 0 退出并说明原因，**不猜内容**。

## 改规则

1. 改 `dsh/AGENTS.md`（分类表里的章节路径与 `backend/app/vault_layout.py` 对应）；
2. 重启 dsh（Obsidian 里禁用再启用插件，或用「与神对话」的「重启 dsh」）；
3. 跑 `tests/test_dsh_workflow.py` 和 `tests/test_share_link.py`：前者检查规则文件是否仍覆盖当前
   四库布局的章节路径、有没有残留旧路径，后者守住分享链接解析、轮次筛选与错误处理。

规则文件只对 **torch 启动的 dsh** 生效；单独运行的 dsh、别的项目不受影响。

## 相关文件

| 文件 | 作用 |
|---|---|
| `dsh/AGENTS.md` | 规则正文（项目内置） |
| `dsh/skills/torch-mentor/` | 「与神对话」导师协议：人格、检索、归档（dsh 从 `$DSH_AGENTS_HOME/skills` 自动发现） |
| `backend/app/dsh_web.py` | 启动 dsh 时注入 `DSH_AGENTS_HOME` / `TORCH_REPO_ROOT` |
| `scripts/session_brief.py` | 只读开场简报（`--memory` 记忆内核，`--full` 内核 + 态势） |
| `scripts/archive_note.py` | 归档出口：章节校验 + frontmatter + 防覆盖 |
| `scripts/share_link.py` | 网页版分享链接取回：`share_id` 解析、只读接口、按轮筛选、输出 Markdown/JSON |
| `scripts/identity_draft.py` | 身份候选投递（只写 `.torch-os/身份待确认/`，采纳前不进四库） |
| `scripts/question_draft.py` | 提问卡投递（只写 `.torch-os/提问队列/`，必须带 `--why`） |
| `backend/app/identity_inbox.py` | 身份候选的队列、采纳、丢弃 |
| `docs/MENTOR.md` | 「与神对话」的记忆架构与待决策 |
| `tests/test_dsh_workflow.py` | 规则与布局同步、环境注入的守卫测试 |
| `tests/test_mentor_workflow.py` | 导师协议、归档出口、记忆内核的守卫测试 |
