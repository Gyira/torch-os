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
3. **边界**：不移动、不删除、不批量改；迁移与清理必须用户确认。
4. **会话方式**：一个话题一个会话；跨会话靠四库，不靠聊天记录；说“接着上次”时先跑简报。

## 改规则

1. 改 `dsh/AGENTS.md`（分类表里的章节路径与 `backend/app/vault_layout.py` 对应）；
2. 重启 dsh（Obsidian 里禁用再启用插件，或用「与神对话」的「重启 dsh」）；
3. 跑 `tests/test_dsh_workflow.py`：它会检查规则文件是否仍覆盖当前四库布局的章节路径、有没有残留旧路径。

规则文件只对 **torch 启动的 dsh** 生效；单独运行的 dsh、别的项目不受影响。

## 相关文件

| 文件 | 作用 |
|---|---|
| `dsh/AGENTS.md` | 规则正文（项目内置） |
| `backend/app/dsh_web.py` | 启动 dsh 时注入 `DSH_AGENTS_HOME` / `TORCH_REPO_ROOT` |
| `scripts/session_brief.py` | 只读开场简报 |
| `tests/test_dsh_workflow.py` | 规则与布局同步、环境注入的守卫测试 |
