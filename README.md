# 薪火OS（Torch OS）

> 一个**本地优先**的个人 AI 工作台：记录、记忆、目标与时间管理都住在你自己的电脑上。
> 数据是本体，AI 是可选的一层——它读你的数据，但从不擅自改你的数据。
>
> *A local-first personal AI workbench. Your data stays on your machine, in plain Markdown.*

本地服务跑在 `127.0.0.1`，用 Obsidian（或任意浏览器）当界面；记忆、日记、任务都以 **Markdown 文件**
为数据本体，SQLite 只做索引与审核状态。不联网也能用；想用大模型时再显式打开。

## 能干什么

- **工作台**：问候语 + 横幅（座右铭/统计）+ 时钟、5 日天气、黄历；
  以及一整块**时间管理模块**——
  - **任务**：二级侧边栏（任务看板 / 清单管理 / 标签管理）、回车即建、日期·时间·优先级·标签·清单五个属性、
    任务行单击选中与多选批量（改日期/优先级/标签/移动/置顶/复制/删除）、右键菜单、分组与排序；
  - **日历**：七种视图一站齐——**年 / 月 / 周 / 日 / 日程 / 多日（2·3·5·7 天）/ 多周（2·3·4 周）**，
    一排按钮直接切；任务按清单颜色着色；有时间的任务在时刻轴上按时长摆放（重叠自动并排），
    没时间的进「全天」区；凌晨空白时段折成一行；点某处空白就能在那一刻加任务；
  - **习惯打卡**与**四象限**（艾森豪威尔四格 ↔ 高/中/低/无优先级）。
- **数字分身**：三观与座右铭、画像、记忆（**候选 → 人工确认**，永不自动确认）、
  目标与任务、习惯、身体记录、八字参考（传统文化角度的自我反思）。
- **第二大脑**：Obsidian 笔记库的索引与检索、资料导入（Markdown/Wolai 导出，先预览后选篇）、
  日记文件化（一天一篇，Obsidian 直接编辑）。
- **AI 对话（可选）**：把本机只读工具桥接给支持 MCP 的对话端（如 Claude Code），
  或配置任意 OpenAI 兼容接口；Key 只放在本机 `.env` 里，不随仓库、不进备份。

## 截图

（虚构示例数据，QA 隔离工作台实拍）

| 工作台 | 日历 · 月视图 |
|---|---|
| ![工作台](assets/screenshot-workbench.png) | ![月视图](assets/screenshot-calendar-month.png) |

| 日历 · 周视图 | 时间管理 · 任务页 |
|---|---|
| ![周视图](assets/screenshot-calendar-week.png) | ![任务页](assets/screenshot-tasks.png) |

## 快速开始（Windows / Python 3.12）

首次安装**四步**，全在本机完成：不需要管理员权限、不需要 Node、默认不联网。

| # | 做什么 | 靠什么 |
|---|---|---|
| 0 | 确认机器上有 Python 3.12 | `python --version`（没有就装，见下） |
| 1 | 初始化环境：venv + 依赖 + `.env` + 示例数据 | 双击 `安装.cmd`（唯一的安装入口） |
| 2 | 起本地服务 | `scripts\start.ps1` |
| 3 | 把工作台装进 Obsidian（可选，但推荐） | `scripts\install-obsidian-plugin.ps1` |

### 0. 前置：Python 3.12

**仓库里不含 Python**，第一件事是确认它真的在：

```powershell
python --version    # 期望看到 Python 3.12.x
```

没装的话，症状是命令弹出微软商店、或报 “Python was not found”——那是 Windows 自带的空壳别名，等于没装。装一个（按用户安装，不要管理员权限）：

```powershell
winget install --id Python.Python.3.12 --source winget --scope user
```

装完**新开一个终端**再验一次（PATH 要新会话才刷新）。已有别的 Python 也行，把后面命令里的 `python`
换成 `py -3.12` 或解释器全路径即可。图形安装器也依赖它——没装时双击 `安装.cmd` 会给出安装命令并打开下载页。

### 1. 初始化环境

先把仓库拿到本地：`git clone <本仓库地址>`，或下载 ZIP 解压。然后在资源管理器里打开仓库文件夹，
**双击根目录的 `安装.cmd`**——这是唯一的安装入口，会打开图形安装器：

- 建 `.venv`，按 `requirements.lock.txt` 装依赖：先体检本机，版本已经对上就跳过，不重复安装、不重复走网络；
- 缺依赖时逐条安装，日志就在窗口里；官方源失败可自动换清华镜像重试一次；
- 顺手生成 `.env`；示例数据可选（已有库不会被删东西，只有同名文件会被覆盖）；
- 可选：直接选一个 Obsidian 库，把插件装进去；
- 装完点「启动工作台」，浏览器直接打开 `http://127.0.0.1:8765`。

安装器只做安装，不碰你的笔记；不想选的步骤留空即可。**重复打开是安全的**：已完成的步骤会自动跳过，
以后要补依赖、补示例数据、或者再装一个 Obsidian 库，重新双击 `安装.cmd`、只勾需要的项就行。
安装器本体在 `scripts\install_gui.py`（只用标准库，依赖装好之前就能跑）。

想单独确认依赖装没装齐（只读、不联网，不装任何东西）：
`.\.venv\Scripts\python.exe scripts\check_deps.py`。

### 2. 起服务

用图形安装器装完的，直接点安装器里的「启动工作台」即可；手动启动用：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start.ps1
```

打开 <http://127.0.0.1:8765>。默认只监听本机回环地址，不对外开放。

### 3. 装进 Obsidian（可选）

安装器里勾选「把工作台装进 Obsidian」，选择**库根目录**——有 `.obsidian` 文件夹的那一层。
安装器只写两处：`.obsidian\plugins\torch\`（插件本体，外加预填了本机仓库路径的 `data.json`）
和 `community-plugins.json`（预启用），不碰库里任何笔记。

想再装进另一个库：重新打开 `安装.cmd`，只勾这一项、选那个库即可（其它步骤会自动跳过）。

装完在 Obsidian 里：

1. **完全重启 Obsidian**——`.obsidian\plugins` 的改动不会热加载；
2. 首次会弹「信任仓库作者并启用插件」，点确认；
3. 左侧 ribbon 出现**薪火OS**图标，或 `Ctrl+P` 搜「打开工作台」。

插件会自动 ping `127.0.0.1:8765`，没在跑就用「仓库路径/.venv/Scripts/python.exe」把服务拉起来，
所以**第 1 步必须先做完**，否则视图只会显示连不上。服务地址、仓库路径、Python 路径、是否自动拉起，
都能在插件设置页改。

### 卡住了看这里

| 现象 | 原因与处理 |
|---|---|
| `python` 弹出微软商店 / “Python was not found” | 只有商店别名，真身没装 → 回第 0 步；装完要新开终端 |
| 双击 `安装.cmd` 提示找不到 Python | 图形安装器也需要 Python 3.12 → 按窗口里的 `winget` 命令装好，重新双击 |
| 图形安装器里依赖装到一半失败（`ReadTimeoutError` 等） | 确认「自动换清华镜像重试」是勾着的，再点「开始安装」；仍失败就看窗口日志里的 pip 原始报错 |
| 想确认依赖装没装齐 | `.\.venv\Scripts\python.exe scripts\check_deps.py` —— 不联网，逐条比对 `requirements.lock.txt` 并点名缺哪条 |
| `No module named venv` | 用的是嵌入式/精简版 Python → 换 python.org 或 winget 的完整版 |
| Obsidian 左侧没有图标 | 没完全重启；或选库时选错了一层（要选有 `.obsidian` 的根目录）；或处于限制模式 |
| 打开视图提示连不上服务 | 第 1 步没做完（缺 `.venv`），或 8765 端口被别的程序占着 |

- 数据默认落在 `data\`（数据库、Vault、导入、备份），整个目录都被 Git 忽略。
  想用自己已有的 Obsidian 库，在 `.env` 里改 `VAULT_DIR` 指向它即可（界面里也能改）。
- 前端是原生 ES 模块 + 一个 CSS 文件，**不需要 Node 构建**；Node 仅用于离线前端测试。

## 设计原则

1. **能用 Markdown 就用 Markdown**：待办、任务、日记都是 vault 里的普通 `.md` 文件，
   Obsidian 里手改同样生效；产品只做「外科手术式写回」——按行定位、原子替换、冲突就报错让页面刷新。
2. **记忆永不自动确认**：任何自动流程只能写「候选」，确认/拒绝只能由人在界面里点。
3. **数据不出本机**：默认全离线（本地检索回答）；要用模型时显式勾选，Key 只存本机 `.env`。
4. **只留一个 AI 入口**：界面负责手动操作，对话负责自然语言，两边读写同一份数据。

## 目录结构

```
backend/app/       FastAPI 本地服务（任务、日记、记忆、目标、习惯、身体、天气、备份…）
frontend/          原生 ES 模块前端（时间管理模块的日历七视图在 tasks-calendar*.js）
obsidian-plugin/   把工作台内嵌进 Obsidian 的薄插件（免构建）
scripts/           启动、图形安装器、依赖体检、自检、发布自检、浏览器探针、校验
tests/             Python 行为测试（后端）
frontend/tests/    前端模块与交互测试（Node 内置 test runner）
examples/          全虚构的示例资料（QA 隔离工作台用它起步）
安装.cmd           双击打开图形安装器（唯一的安装入口）
```

## 开发与测试

```powershell
# 全量校验：Python 测试 + 文本/结构守卫 + 模块面 + 前端语法与测试 + 编译 + 依赖一致性
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\check_all.ps1

# 只跑后端
.\.venv\Scripts\python.exe -m pytest -q

# 只跑前端
node --experimental-vm-modules --test frontend/tests/*.test.mjs
```

想要一个**隔离验收环境**（独立库、全虚构数据，随便折腾）：

```powershell
.\scripts\start-qa.ps1          # http://127.0.0.1:8766
```

## 数据与隐私

- 私人数据全部在 `data\`（也被 `.gitignore` 忽略）：SQLite、Vault、导入原包、日志、备份与恢复副本。
- 模型 Key 只写在本地 `.env`；界面只显示「是否已配置」，从不回显。
- 仓库里没有任何个人数据；`examples/` 下的所有资料都是虚构的。

## 参考与致谢

- 任务行的行内标记沿用 **Obsidian Tasks 插件**的通用写法（`📅` / `⏫🔼🔽` / `✅` / `➕` / `📌`），
  所以将来装那个插件也能读同一份文件。
- 「时间管理模块」的交互形态参考了**滴答清单**（仅借鉴界面结构与操作习惯，没有使用其任何代码或数据）。
- 界面观感参考了 Apex Dashboard 的横幅/天气卡片布局。

## 协议

[MIT](LICENSE) © 2026 Gyria
