# 薪火OS · Obsidian 插件（0.99.0）

把整个工作台住进 Obsidian：一个 `薪火OS` 标签页里是产品本体（iframe 内嵌 `?embed=1&panel=1`（顶部标签条导航）），
外加自动拉起本地服务与主题跟随。**免构建**：`main.js` 是手写的 CommonJS 单文件，Obsidian 直接加载。

## 功能

- 左侧 ribbon 图标 / 命令面板“打开工作台” → 打开 `薪火OS` 视图；再次打开会聚焦已有视图；
- **健康检查与自动拉起**：打开视图时 ping `127.0.0.1:8765`，没在运行且设置了“仓库路径”时，
  用 `.venv` 的 python 以后台方式启动本地服务（`isDesktopOnly`）；
- **主题跟随**：读 Obsidian 的计算样式翻译成产品主题桥的 `--dsw-alias-*` token，
  在 iframe 加载完成、`css-change`（换主题）与 iframe 主动请求时推送；
- **同步感**：视图重新被激活且距上次加载超过 2 分钟时整页重载，读回最新记录（与 dsh 壳、网页壳
  共用同一个本地服务与同一份 Vault，改动天然互相可见）。

## 安装

用仓库里的脚本（会写进 vault 的 `.obsidian/plugins/torch/` 并预启用）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install-obsidian-plugin.ps1 -VaultPath "D:\Obsidian\我的知识库"
```

装好后重启 Obsidian（或重载插件）即可在左侧看到图标。设置页可改：本地服务地址、仓库路径、
Python 路径、是否自动拉起。

## 边界

- 插件只访问本机 `127.0.0.1` 的本地服务与你自己设置的仓库路径；不联网、不读模型 Key；
- `data.json`（含你本机的仓库路径）只存在于你的 vault 里，**不进本仓库**（`.gitignore` 已挡）；
- 手机端不支持（本地服务在电脑上，`isDesktopOnly: true`）。
