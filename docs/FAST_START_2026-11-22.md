# 工作台秒开（2026-11-22，2.3.0）

## 问题

Obsidian 插件的 `WorkbenchView.onOpen()` 原先先等 Python 健康检查通过，再给 iframe
设置 `src`；失败时整页被“无法启动工作台”的卡片顶掉。健康检查是 30 次 × 1 秒，
而插件不读后端日志，所以真实错误（例如库仍是旧编号目录）被藏在一个 30 秒超时后面。

## 结构

```
Obsidian iframe
  http://127.0.0.1:<稳定 uiPort>          LocalUiServer（obsidian-plugin/main.js）
    / , /assets/*  → <repo>/frontend      秒开，不需要 Python
    /api/*         → 127.0.0.1:<backend>  HTTP 流式代理
    /harness*      → 127.0.0.1:<backend>  HTTP + WebSocket 升级代理
    /__torch/*     → 状态 / 启动 / 重启 / 日志 / 缓存 / 目录迁移
```

- `BackendController`：`idle → starting → ready | failed`。打开工作台后由静态 `/`
  响应完成时后台触发；首次 `/api` 请求也兜底触发。
- 监听子进程 `exit`，按本次启动前记录的日志偏移读取新增日志，快速分类失败。
- 代理自己持有后端会话 Cookie（后端只在 `GET /` 发出，名字含 DB 路径 + 端口），
  不暴露给页面；HTTP 与 WebSocket 都把 `Host`/`Origin` 改写成后端 authority。
- `UiCache`：只缓存 GET + JSON + ≤1MiB 的只读接口，放
  `%LOCALAPPDATA%/torch-os/obsidian-plugin/ui-cache/<vaultId>`，不进库/网盘。
- 前端 `service-gate.js`：轮询 `/__torch/service`，服务启动中等待并只重试一次；
  失败显示具体原因、重试/日志/整理旧目录；就绪时发 `torch:service-ready`，重跑当前数据加载器。
- 浏览器直开 Python 服务时 `/__torch/*` 404，服务门控自动进入 direct 模式，行为不变。

## 失败分类

覆盖：`repo_missing`、`python_missing`、`numbered_vault`、`module_missing`、
`port_conflict`、`vault_mismatch`、`permission`、`database`、`config_invalid`、
`timeout`、`exited`、`session_failed`、`backend_unreachable`。

日志偏移是必须的：日志按端口 append，同一端口会复用文件；只有读本次启动之后新增的
部分，才不会把上一次的 traceback 当成新错误。

## 旧编号目录迁移

- 当前库旧根：`00_人生路 / 01_自我档案 / 02_生活运营 / 03_知识库`。
- 后端在 `backend/app/application.py` 主动拒绝启动；插件的 `numbered_vault` 失败会显示
  “整理旧目录”。
- `POST /__torch/migrate/preview` 只读运行 `scripts/unnumber_vault.py`，返回目录/文件数；
  `POST /__torch/migrate/apply` 需要显式 `confirm` 且数量与最近预览一致，后台 job 执行。
- 脚本自带 `.torch-os/backups/directory-names/<stamp>/`（manifest、原文、staged 修改、
  SQLite 备份）并支持 `--rollback`。
- 迁移期间建议暂停网盘同步并避免在 Obsidian 里编辑该库。

## 插件的单文件约束

`main.js` 仍是免构建 CommonJS 单文件；`LocalUiServer` / `BackendController` / `UiCache` /
`VaultMigrator` 都在里面，并通过 `module.exports._test` 暴露给 Node 测试。安装器仍然只
复制 `manifest.json` / `main.js` / `styles.css` 三个文件。

## 验证

```powershell
node --check obsidian-plugin\main.js
node --test scripts\test_local_ui_server.mjs
node --test scripts\test_service_gate_api.mjs
node --test scripts\test_obsidian_plugin_paths.mjs scripts\test_request_reliability.mjs scripts\test_chatgpt_host.mjs
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\check_all.ps1
```

手工验证：

1. 健康库：打开工作台界面立即出现，数据随后填入；dsh 对话、下载、打开笔记正常。
2. 旧编号库：2–3 秒内出现具体原因；迁移后数据恢复。
3. 停掉后端：缓存页面可见并标注；后端恢复后自动刷新。
4. 浏览器直开 8765：无服务门控，行为与旧版一致。
