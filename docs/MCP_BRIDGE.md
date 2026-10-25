# 只读工具桥（MCP）

薪火OS 把本机只读能力通过 stdio MCP 桥提供给 DeepSeek Harness。桥接层只转发
`GET /api/tools/*`，不开放写操作。

## 启动

先启动本地服务（默认 `http://127.0.0.1:8765`），再启动桥：

```powershell
.\.venv\Scripts\python.exe scripts\mcp_bridge.py
```

桥使用本地会话与回环地址；不会把工具调用发往外部。具体 MCP 客户端配置由客户端
自己的工作区负责，本仓库只提供这个 stdio 入口。

### 接进 DeepSeek Harness（2026-10-26 已验证）

在 `$DSH_HOME/profiles/<profile>/cordis.patch.yml` 里插入一个 stdio 服务器。注意：顶层
条目默认是「覆盖已有 id」，**新增服务器必须写在 `insert` 列表里**，否则只会得到
`patch: entry "mcp-torch" not found` 的警告：

```yaml
- insert:
    - id: mcp-torch
      name: "@deepseek-ai/dsh-mcp-client"
      config:
        serverName: torch
        transport: stdio
        command: 'C:\path\to\torch-os\.venv\Scripts\python.exe'
        args:
          - 'C:\path\to\torch-os\scripts\mcp_bridge.py'
          - '--base-url'
          - 'http://127.0.0.1:9168'
        cwd: 'C:\path\to\torch-os'
        toolCallTimeoutMs: 20000
        failOnStartupError: false
```

- `--base-url` 指向当前工作台实例：Obsidian 插件模式用插件挑的端口（默认从 `9168` 起），
  浏览器模式用 `.env` 的 `8765`；端口变了要同步改这里。
- 改完先 `dsh --profile web --dump-config` 确认能看到 `mcp-torch` 且没有 not-found 警告。
  `web` profile 默认开着 HMR，正在运行的 dsh 会自动重载 patch，不需要重启。
- 工具名是 `mcp__torch__<tool>`（例如 `mcp__torch__today`），契约仍是只读；
  后端没起时只是这些工具不可用，dsh 本身照常启动（`failOnStartupError: false`）。
- 手动确认桥本身可用：`POST /api/bridge/selftest`，请求体 `{"base_url":"http://127.0.0.1:9168"}`。

## 工具目录（12 个）

| 工具 | 读取内容 |
|---|---|
| `today` | 某一天的结构化概况：日记字段、Markdown 任务计数与到期标题、习惯状态、身体记录标记、草稿数 |
| `memories` | 按问题检索已确认且来源有效的记忆，含来源路径与置信标记 |
| `goals` | 人生路目标笔记与未完成 Markdown 任务 |
| `habits` | 每个启用习惯最近 N 天的完成/跳过/未记录计数 |
| `journal` | 一段日期里哪些天有记录、是否写了下一步 |
| `profile` | 认识我的计数快照：可用/待确认记忆、资料数、进行中目标、未完成任务 |
| `vault` | Vault 路径、是否 Obsidian 库、Markdown 篇数、跳过目录、只读模式 |
| `body` | 来自健康月度 Markdown 表的最近 N 天汇总：有记录天数、睡眠与运动分钟合计 |
| `documents` | 已索引资料摘要：总数、最近索引标题与路径、未索引变化 |
| `imports` | 资料导入健康：导入包、已导入笔记、历史版本、索引待刷新与本地附件计数 |
| `vault_audit` | Vault 只读体检：检查篇数与各类问题计数 |
| `identity` | 姓名、座右铭、三观与自由补充（来自 Vault 的身份文件） |

## 边界

- 全部工具是只读 GET；每次调用只写一条计数审计，不写正文。
- 不提供确认记忆、建任务、写日记等写操作。
- 工具只在本机回环地址与本地会话下可用。
- `profile` 是计数快照工具，不是已退役的 profile 页面；`learning` 工具已随旧学习后端退役。
- 工具目录可通过 `GET /api/tools` 查看；桥自检见 `POST /api/bridge/selftest`。
