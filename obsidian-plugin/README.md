# 薪火OS · Obsidian 插件（2.2.1）

把整个工作台住进 Obsidian：一个 `薪火OS` 标签页里是产品本体（iframe 内嵌 `?embed=1&panel=1&host=obsidian`），
外加按库隔离的数据目录、自动拉起本地服务、主题跟随与打开任务文档。**免构建**：`main.js` 是手写的
CommonJS 单文件，Obsidian 直接加载。

## 功能

- 左侧 ribbon 图标 / 命令面板“打开工作台” → 打开 `薪火OS` 视图；再次打开会聚焦已有视图；
- **在哪个库工作，哪个库就是工作区（2026-10-17）**：
  - 插件读取当前库的绝对路径，计算 `vault_id`；
  - 先探测已经存在的、服务同一个库的后端实例；没有就用 `.venv` 的 Python 启动一个
    （`PORT` 从 9168 起自动选空闲端口、`VAULT_DIR=当前库`、`DATA_DIR=当前库/.torch-os`、
    `TORCH_VAULT_SCOPED=1`；8765 留给浏览器/手动模式，两者可同时运行）；
  - 每个库一个实例，各自的 SQLite/索引/记忆/备份互不相通；界面里不再提供“切换工作区”；
  - **仓库路径、Python 路径和实例端口是“这台电脑”的属性**：只保存在本机
    `%LOCALAPPDATA%\torch-os\obsidian-plugin\<vault_id>.json`（macOS/Linux 对应配置目录），
    不写进随库同步的 `data.json`；换设备时会自动查找本机仓库和 `.venv`，不会拿另一台设备的
    绝对路径互相覆盖；
  - 自动查找会看环境变量 `TORCH_REPO_ROOT`、插件里上次的本机路径、用户主目录/桌面/文档、
    Vault 同级目录和常见 `tools` 目录；优先选带 `.venv` 的仓库。找不到时可在设置里手动填写，
    该值也只保存在本机；
  - 启动日志在 `当前库/.torch-os/logs/torch-backend-<port>.log`。
- **打开任务文档**：iframe 发 `torch:open-note`（源 + 实例 origin 校验），插件只允许
  `00_人生路/07_任务与待办/` 下的新任务笔记，或 `_系统/任务（tasks）.md` /
  `_系统/待办（todos）.md`（旧 `00-System` 路径也兼容），
  在新标签页打开；文件不存在时给出提示。
- **打开网页书签（2026-10-28）**：工作台问候语右侧的书签栏发 `torch:open-url`，
  插件校验来源与协议（只放行 http/https）后交给系统默认浏览器（Edge），
  不在 Obsidian 里开内嵌网页——网页版的登录态和免费额度都在浏览器里。
- **1.0 界面能力**：应用内写作、页面角色抽屉（忆灵/苏格拉底等）、提案卡片与忆灵面板
  都在 iframe 内的产品本体里；插件只负责宿主桥、按库实例与主题，不调用模型、不读 Key。
- **主题跟随**：读 Obsidian 的计算样式翻译成产品主题桥的 `--dsw-alias-*` token，
  在 iframe 加载完成、`css-change`（换主题）与 iframe 主动请求时推送；
- **同步感**：视图重新被激活且距上次加载超过 2 分钟时整页重载。

## 安装

用仓库里的脚本（会写进 vault 的 `.obsidian/plugins/torch/` 并预启用）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install-obsidian-plugin.ps1 -VaultPath "D:\Obsidian\我的知识库"
```

装好后重启 Obsidian（或重载插件）即可在左侧看到图标。设置页可改：仓库路径（仅本机）、
Python 路径（仅本机）、是否自动拉起、候选服务地址（只有服务的正是当前库时才会复用，
否则另起当前库的实例）。两台设备共用同一个库时，各自第一次打开会自动查找仓库；也可以点
「自动检测」或手动填写一次，路径只留在那台电脑。

## 边界

- 插件只访问本机 `127.0.0.1` 的本地服务与你自己设置的仓库路径；不联网、不读模型 Key；
- 随库同步的 `data.json` 只保留 `baseUrl` / `autoStart` 等共享设置；仓库路径、Python 路径和
  实例端口只存在本机配置目录，不会跨设备同步；
- 多个库同时打开时各自一个后端进程；Obsidian 关闭后进程可能继续存在，下次复用；
  不自动杀进程；
- 手机端不支持（本地服务在电脑上，`isDesktopOnly: true`）。
