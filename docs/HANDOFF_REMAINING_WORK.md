# 薪火 OS：剩余优化工作交接

更新日期：2026-11-03。交接原因：用户本周模型额度将尽，希望换模型继续。
本文用于接续现有项目，不是重新设计产品。当前交接轮只更新文档，没有继续实施业务改动。

> **2026-11-03 后续更新**：本文第 4–8 节的 B3-3、B2、B4a、B4b、B5 已全部实施并通过
> 专项与全量检查。当前状态见 `docs/DESIGN_NEXT_BATCHES.md` 与各批次文档：
> `DESIGN_B3_3_CONTRACT.md`、`DESIGN_B2_SESSION_ISOLATION.md`、
> `DESIGN_B4A_ARCHIVE_CONSISTENCY.md`、`DESIGN_B4B_AUTONOMOUS_ARCHIVE.md`、
> `DESIGN_B5_QUESTION_FLOW.md`。下文保留交接时的诊断、规格与验收背景；
> 其中“当前导师仍创建 pending 卡”“B4b 还没接通”等描述已过时。
> 导师自主更新默认开启，`TORCH_MENTOR_AUTONOMOUS_ARCHIVE=0` 可回退旧待确认模式。

## 1. 接手后先读这一页

项目：`C:\Users\19814\Desktop\torch-os`，Windows / PowerShell。
技术栈：Python / FastAPI、SQLite 与文件夹中的 Markdown；前端是现有原生 JavaScript ES modules、HTML/CSS。沿用当前组件与服务，不另起框架。

**（2026-11-03 更新：以下顺序已完成，保留作为历史交接说明。）**
原计划：先做 A0 余项 + B3-3，再做 B2 → B4a → B4b → B5；B4a 必须先于 B4b。

先检查 `git status --short`、`git log -4 --oneline`，不要清理未提交文件。交接时：

- HEAD 为 `e259acb`：B3-1 角色读取与会话管理。
- `5cec9d0`：B1-R Windows 记忆并发可靠性。
- `dc711cf`：前端目录映射、迁移与自动修复。
- **B3-2 已完成、测试并重启生效，但仍在工作区，尚未提交。** 不要把未提交等同于未实现。
- B3-2 的新文件包括 `backend/app/proposal_actions.py`、`scripts/test_role_actions.py`、`docs/DESIGN_B3_2_WRITES.md`、`docs/patches/B3-2.patch`；其余改动分布在角色政策、提案、应用接线、报告入口和少量前端提示中。
- 不要重新应用 `B3-2.patch`；当前代码已包含它。它用于审阅与独立回退。
- 本文中的提交、进程和测试状态是交接快照；如用户之后提交或改代码，以实际工作区为准。

建议阅读顺序：本文 → `DESIGN_NEXT_BATCHES.md` → `DESIGN_B3_2_WRITES.md` → 下一批涉及的函数。遇到相关边界再查 `DESIGN_B3_ROLE_BOUNDARIES.md`、`DESIGN_B1_MEMORY_RELIABILITY.md`，不必通读整个仓库。

## 2. 已确认的产品理念：不要重新询问

| 锚点 | 必须保持的设计规则 |
| --- | --- |
| D1 记忆优先 | 如果只能保留一项价值，保留长期记忆、自我档案和最懂用户的人生导师。可靠保存优先于表面效果 |
| D2 AI 自主整理 | 用户希望 AI 直接更新档案；区分明确自述事实和导师观察推断；可通过对话纠正。改变看法时新判断成为当前版本，旧判断保留为成长历史 |
| D3 导师独立 | 人生导师是拥有全局业务资料读取权限的独立角色。不能把全局读取解释为全局任意写入 |
| D4 领域分工 | 专业角色只管理所属领域。私人医生与健康估算助手是同一个角色，名称就是私人医生，估算只是模式 |
| D5 文件夹映射 | 前端分类与个人数据库文件夹一一对应，固定路径统一维护，缺失自动补建。保持整洁编号，不另造一套数据目录 |
| D6 工作台 | 工作台聚合高频查看/操作；待办数据归生活运营 |
| D7 主动但可拒绝 | 导师每天可以提问补充档案，用户可以不回答、跳过或不再问 |
| D8 执行底座 | DSH 是执行与调度底座。“与神对话”用于修改项目、加功能、修 Bug，不是人生导师本身 |
| D9 保持习惯 | 桌面键鼠为主，保留品牌与核心流程；用户尤其满意恋爱系统、待办系统和文件结构 |

AI 自主更新是**已确认产品方向**，但现有代码仍有待确认卡。不要因为旧代码/旧文档写着“人工采纳”就否定该方向，也不要把方向确认当成已完成实现。

用户要求的工作方式：

- A 类：不改变外部行为的纯代码改进，给最小 diff、验证与回退；可直接进行。
- B 类：改变交互或业务行为，先给具体方案、影响、迁移成本、回退方式，再按用户确认实施。已确认的理念和 B3 三个取舍不重复问；尚未确认的新增机制只问真正影响实现的决策。
- C 类：偏离理念的反提案只讨论，不能混入代码。
- 每批写清对应锚点、是否改变外部行为/习惯、测试结果和回退，不一次性重写。
- 当前用户请求是生成交接文档；这份文档本身不代表 B2/B4/B5 的所有实现细节已获批准。

## 3. 已完成：不要重复开发

| 批次 | 已完成内容 | 证据/入口 |
| --- | --- | --- |
| UI 1–3 | 共用控件、任务/编辑器/搜索、角色抽屉的焦点、草稿、异步结果隔离等 | `UI_POLISH_BATCH1.md`、`UI_POLISH_BATCH2.md`、`UI_POLISH_BATCH3.md`；`scripts/test_ui_polish.mjs` |
| 目录对齐 | 四库、模块编号与前端映射固定；缺失目录在原路径补建；不覆盖业务文件 | `VAULT_FRONTEND_ALIGNMENT.md`；`vault_layout.py`、`love_layout.py`、`test_vault_alignment.py` |
| B1-R | 记忆/画像读写使用共同锁；修复 Windows 文件替换拒绝；保留冲突与错误反馈 | `DESIGN_B1_MEMORY_RELIABILITY.md`；`memory_store.py`、`mentor_memory.py` |
| B3-1 | 角色资料读取权限、直接报告来源、旧模型 session/历史隔离、旧忆灵停止新推理；医生合并 estimate | `DESIGN_B3_ROLE_BOUNDARIES.md`；`role_policy.py`、`ai_context.py`、`ai_roles.py` |
| B3-2 | 最终 edits 和旧提案重验、移动双端检查、受控待办/习惯执行、事件来源校验、原子领取提案、防重复执行 | `DESIGN_B3_2_WRITES.md`；`proposals.py`、`proposal_actions.py` |

容易混淆的区别：

- 共用角色抽屉 `frontend/roles.js` 的异步隔离已做；**专属导师页面 `frontend/mentor.js` 的 B2 隔离还没做**。
- B1 解决记忆/画像文件可靠性；**档案条目替代的多文件一致性 B4a 还没做**。
- B3 服务端会话隔离不等于 B2 前端回调隔离。
- 当前导师仍创建 pending 卡；**自主提交档案 B4b 还没接通**。

已批准并落实的 B3 取舍：生活管家读取整个生活运营父模块，取消默认身份档案注入；习惯摘要/创建是受控跨域能力；恋爱待办由任务服务执行。旧对话仍可看，新权限不复用旧模型上下文。旧忆灵仅查看历史。

当前政策版本已随 B4b 升为 `B4b-v1`（交接时为 `B3-2-v1`）。固定读/写范围和能力表在 `role_policy.py`，不由角色名字、风格或 Skill 扩权。私人医生 `health_doctor` 有 report/advice/estimate；`health_estimator` 只是旧运行 ID 兼容别名，没有独立角色卡。

## 4. 待办一：A0 余项 + B3-3，契约与文案一致性

优先级 P1；文档纠正为 A 类，运行时提示或可见行为变化需单列 B 类。

已知证据：`docs/MENTOR.md` 仍写“与神对话就是导师、不要独立导师页面”，与用户最新确认冲突。`dsh/AGENTS.md`、旧 skill 和部分页面提示还可能保留旧架构或“所有写入必须人工采纳”的阶段说明。`docs/AI_ROLES.md` 已局部更新，不能假设已全面校准。

待完成：

- [ ] 将旧架构文档标明历史地位，提供当前设计入口；保留有价值的历史，不删除用户记忆或历史文件。
- [ ] 核对 `docs/AI_ROLES.md`、`docs/MENTOR.md`、`docs/SELF_ARCHIVE_MENTOR_BAZI.md`、角色管理页及提示文案；以代码实际能力标注“已实现/计划中”。
- [ ] 当前人工采纳与未来自主提交分别说明，不提前宣称 B4b 完成。
- [ ] 对照 `role_policy.py`、角色卡和真实入口，核对角色名称、模式、可读范围、可执行动作、旧角色只读提示。
- [ ] 核对角色来源展示、权限拒绝反馈、只读模式、旧线程提示；沿用当前 UI，不重排页面。
- [ ] 不静默修改 `dsh/AGENTS.md` 或 skill 的运行时行为来“统一文档”；需要改运行规则时提交具体 B 类差异。

验收：用户看到的能力与实际权限相符；不恢复独立估算角色；不把 DSH 和导师混为一人。回退：只撤销该批文档/提示/契约测试，保留 B3-1/B3-2 执行限制。

## 5. 待办二：B2，导师会话与草稿隔离

优先级 P1；对应 D1/D3/D9。范围：`frontend/mentor.js`、`mentor-model.js`、`scripts/test_ui_polish.mjs`；参考已完成的 `roles.js` / `roles-model.js`。

已核对现状：`openThread`、`sendMessage`、`newConversation` 直接更新共享 state；发送时 `setBusy` 禁用输入框。B3 加入了权限切换/失败提示，但没有实现完整的前端会话世代隔离。

最小实现方案需覆盖：

1. 给打开、切换、新建、离开页面建立请求世代/会话身份；成功、catch、finally、随后 loadOverview 等回调都核对归属。
2. A 的迟到回复可以留在服务端 A 历史，不能追加到 B、替换 B 的 threadId、解除 B 的 busy、覆盖 B 的状态提示或抢焦点。
3. 页面内草稿按稳定会话身份隔离；新会话未取得服务端 ID 时也有独立本地身份。失败不覆盖后来输入；切换回来可以继续原草稿。
4. 保持 Enter/Shift+Enter 习惯、旧忆灵只读和现有超时。新消息是否跟随滚动参考共用抽屉，不能把正在阅读的用户拉到底部。
5. **等待回复期间允许编辑下一条草稿属于独立可选交互**，不能夹带实施；也不要默认并发发送第二轮或自动重发模型。

验收用例：A→B、A→新会话、打开 A/B 乱序返回、慢成功/失败、旧 finally、连续点击、页面离开/重进、中文输入法、键盘焦点与草稿恢复。用可控 Promise 和现有最小 DOM 测试方式，不添加重依赖。

迁移成本：页面内状态，无 DB 迁移；不承诺刷新后恢复未持久化草稿。回退：独立前端 diff；后端历史不改。

## 6. 待办三：B4a，当前判断与成长历史一致提交

优先级 P0，是 B4b 的硬前置。对应 D1/D2。
入口：`backend/app/self_archive.py:supersede_entry/create_entry/list_entries`、`mentor_pending.py:accept_pending`。

已核对：`supersede_entry` 先读旧条目和 revision，创建新条目，再把旧条目标为 superseded；旧条目更新异常时删除新文件作为补偿。尚无整次替代操作的跨进程锁、稳定幂等标识和中断恢复协议。

待提交的具体规格：

- [ ] 按 Vault + 旧 entry_id 锁定替代操作，提交时重读并核对 revision；外部编辑冲突保持明确失败，不能重试覆盖。
- [ ] 定义持久化操作 ID、阶段、旧/新条目关联与恢复规则；沿用 entry_id、revision、supersedes，不另建档案内容库。
- [ ] 最小恢复元数据放 `.torch-os`；不新增前端模块或个人业务文件夹。
- [ ] 明确每个中断点的读取视图：同一判断不出现两个当前有效版本，旧版始终可追溯；恢复不猜测、覆盖外部修改。
- [ ] 同操作重复请求只应用一次；两个不同纠正请求不能都用同一旧 revision 成功。
- [ ] 先定义恢复触发时机及只读模式行为，再实现；不要把未完成操作一律自动重放。

复用 `memory_store.py` 的线程/进程协调经验，但它当前只保证单个记忆/画像文件的协作读写，不能宣称已提供多文件事务。Windows 上不要重新引入“取得锁前解析目标文件”的已知问题。

验收：线程/进程并发纠正，各阶段中断、重复提交、revision 冲突、只读、磁盘故障、恢复后当前条目唯一、历史完整。只用临时库。

迁移成本：新增最小操作元数据；旧条目不批量改写。回退前先处理未完成操作，再退执行器；保留已生成版本和历史，禁止靠删历史回退。

## 7. 待办四：B4b，导师自主更新档案

优先级 P1；对应 D1/D2/D3。只有 B4a 验收后才接通。
入口：`ai_roles.py:_archive_pending`、`self_synthesize.py:synthesize`、`mentor_pending.py`、`ai_schemas.py`、`frontend/mentor.js`、`self-archive.js` / `self-archive-model.js`，以及 `dsh/roles/mentor.md`。

用户已经确认：AI 自主理解并直接更新；记录更新；自述事实/观察推断可区分；对话纠正；新判断替代旧判断并保留成长历史。不要再问“是否允许 AI 自动更新”。

待完成的实施规格与代码：

1. 复用现有 fact / inference 等条目类型、依据、来源、supersedes；事实必须有明确来源，不能把模型猜测当作用户说过的话。
2. 新的聊天与综合整理结果通过服务端校验后进入自主提交，返回逐项操作 ID、成功/失败及历史链接。不要另造第二套记忆存储。
3. 新建/替代都幂等；部分失败明确说明，不重发模型、不重复写入、不把部分成功报成全部成功。
4. 本轮更新是非阻塞反馈，可查看事实/推断、依据、当前/旧版；不强制切到“待确认”标签、不抢焦点。用户仍通过对话纠正，不随意开放绕过导师的档案直接编辑。
5. 旧 pending 卡保留，不批量自动采纳；新旧执行模式有明确兼容/回退策略，防止同一卡被重复应用。
6. 自主写范围先限导师的档案与记忆；不能从 D3 推导为任意改写恋爱/健康/待办等目录。领域角色扩大自主执行另案处理。
7. 修改角色提示、schema、执行器、前端反馈必须同批核对。B3 权限/会话指纹随能力变更更新，不能为便利删除校验。

仍需具体确认的实现取舍：上线/回退开关方式、失败后的恢复与呈现机制等；将可审阅方案一次说明，不重复理念澄清。

验收：事实/推断正确标记，对话纠正、成长历史、重复请求、部分失败、无证据结果、只读、旧卡兼容、开关回退。回退只切换执行模式，不删除已自动保存的记忆/条目。

## 8. 待办五：B5，每日提问与更新反馈

优先级 P2；对应 D2/D7/D9，依赖 B4 的理解/提交链。
入口：`mentor_questions.py:answer/draft_today/skip`、导师提问前端及 `mentor-strip.js`（按实际调用核对）。

现状：普通回答写问答记忆；采纳拟答的路径还能直接创建档案条目，两条沉淀路径不一致。跳过/不再问已经存在，应保留。

- [ ] 统一进入导师理解与档案更新链，区分用户原答与用户采纳的拟答。
- [ ] 未采纳拟答不是用户事实；没有回答不能生成答案；跳过不强迫补答、不制造欠账提示。
- [ ] 先可靠保存回答记忆，再处理档案；分别反馈两个阶段失败，允许安全恢复，避免重复写入。
- [ ] 保留旧问题及已有状态，不批量重新提问。覆盖跨日、重复提交和旧 UI 请求迟到。
- [ ] 动效 180–220ms，尊重 reduced-motion；键盘/读屏有状态反馈，成功不抢焦点。

回退：断开新的归档接线，保留已存问答、条目与更新历史，不清空问题状态。

## 9. 不在本轮默认范围内的工作

- 不把所有角色改成共享全局个人记忆，不用统一数据库替代可见文件夹。
- 不重做恋爱/待办流程，不重新编号或清理个人目录，不重新执行已完成迁移。
- 不把 B3 提案原子领取当成跨 SQLite/Markdown 的崩溃事务。进程中断留下 applying 时需核实结果；若做自动恢复，要单列规格，不能盲目重复执行。
- 不声称已完成整站 WCAG AA、60fps 或真实模型质量验收：当前主要是代码与交互回归。对后续改动页面做实际验证，未测到的如实标明。
- 不为文档校准重新跑所有耗时检查，也不为了验证调用真实模型或写入个人测试数据。

## 10. 数据路径与运行环境

实际个人 Vault：`C:\Users\19814\Desktop\BaiduSyncdisk\Torch\个人数据库`。
运行数据：上述 Vault 下的 `.torch-os`。这是用户真实生活数据，不是测试目录。

- 四库为 `00_人生路`、`01_自我档案`、`02_生活运营`、`03_知识库`。
- 自我档案三个固定部分：`01_档案`、`02_人生导师`、`03_八字系统`。
- 生活运营：`00_待办事项`、`01_恋爱系统`、`02_健康系统`、`03_财务系统`、`04_家庭系统`。
- 唯一定义来源为 `backend/app/vault_layout.py`，恋爱路径引用 `love_layout.py`；不要复制出另一套随意修改的字符串映射。
- 固定缺失目录启动时及每 10 秒补建；不恢复业务文件，不覆盖占位文件/链接，不恢复用户在前端明确删除的本子。
- 测试用 `tempfile.TemporaryDirectory`、临时 SQLite 和 fake runner；不要读取 `.env` 中的密钥或输出凭据。

最近运行的是 Obsidian 库作用域后端，地址 `http://127.0.0.1:9168`。`.env` 默认端口可能为 8765，不能直接按默认值重启。

只有代码变更需要部署时才重启，保留这些有效配置：

```text
HOST=127.0.0.1
PORT=9168
VAULT_DIR=C:\Users\19814\Desktop\BaiduSyncdisk\Torch\个人数据库
DATA_DIR=C:\Users\19814\Desktop\BaiduSyncdisk\Torch\个人数据库\.torch-os
TORCH_VAULT_SCOPED=1
VAULT_READ_ONLY=0
WORKSPACE_NAME=个人数据库
PYTHONUTF8=1
PYTHONIOENCODING=utf-8
```

命令：`.venv\Scripts\python.exe -m backend.app.run`。通过监听端口确认 PID，再核对命令属于本仓库后停止；不要沿用旧 PID 或停止全部 Python。Windows 后台启动使用 `Start-Process -WindowStyle Hidden`。

本地 HTTP 验证使用 `httpx.Client(trust_env=False)` 避免系统代理干扰；先 GET `/` 建立会话，写请求携带正确 Origin。状态入口为 `/api/config/status`、`/api/ai/status`、`/api/ai/roles`，不是 `/api/config`。

## 11. 验证基线与交付方式

以下是交接前最近代码批次的结果，本次文档交接未重新跑全套测试：

| 检查 | 最近结果 |
| --- | --- |
| `scripts/test_ui_polish.mjs` | 18 通过 |
| `scripts/test_memory_reliability.py` | 19 通过，1 个真实符号链接权限条件测试跳过 |
| `scripts/test_role_boundaries.py` | 25 通过，1 个真实符号链接权限条件测试跳过 |
| `scripts/test_role_actions.py` | 22 通过 |
| `scripts/test_vault_alignment.py` | 12 通过 |
| 完整检查 | 文本/结构、109 个模块导入导出、JS 语法、Python 编译、依赖一致性通过 |
| 线上冒烟 | 原库原端口；状态 200、越域读取 403、不存在提案 404；未调用真实模型、未创建个人测试记录 |

两个跳过不是通过；模拟目录联接与越界拦截另有已通过用例。B1 原并发追加曾重复 20 轮通过，不要放宽断言、吞错误或加盲目重试来“修复”它。

```powershell
# 在仓库根目录；按本批范围先做专项，交付前跑完整检查
$env:PYTHONUTF8='1'
node --test scripts/test_ui_polish.mjs
.venv/Scripts/python.exe scripts/test_memory_reliability.py
.venv/Scripts/python.exe scripts/test_role_boundaries.py
.venv/Scripts/python.exe scripts/test_role_actions.py
.venv/Scripts/python.exe scripts/test_vault_alignment.py
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check_all.ps1
git diff --check
```

每批完成时更新批次文档和蓝图状态，交付：最小 diff、对应原则、行为变化、迁移成本、实际测试结果、未测边界、独立回退。若前一批仍未提交，先保存批次边界，不要将所有历史改动混成一个“本批 diff”。

修改 `dsh/` 前读取实际适用的 `dsh/AGENTS.md` 和 `dsh/roles/AGENTS.md`；其中过时产品规则与用户上述明确取舍冲突时，以用户确认为准，不把旧文档当成新产品决策。

## 12. 可直接发给接手模型的启动提示

> 请接手 `C:\Users\19814\Desktop\torch-os` 的分批优化，先读 `docs/HANDOFF_REMAINING_WORK.md`。保留工作区已完成但未提交的 B3-2，不要重做 B1/B3 或目录迁移。沿用已确认设计锚点，不重新提问理念。从 A0 余项和 B3-3 的文案/权限一致性核对开始，再按 B2 → B4a → B4b → B5 推进。每批先给具体范围、原则映射、影响、迁移与回退；A 类可直接做，新增 B 类行为按已确认范围执行，未确认细节提交一次具体方案。优先最小 diff、现有组件和临时库测试，不使用真实模型/个人记录做测试。当前最核心的未完成目标，是在可靠的成长历史和事实/推断区分基础上，让独立人生导师自主更新档案。
