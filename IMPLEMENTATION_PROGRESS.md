# 多端与 ChatGPT 整合实施记录

日期：2026-11-13。依据工作区 outputs 中的设计 1.2、开工交接及百度同步补充。

## P0

- 初始 git 工作区干净；检查了仓库与祖先目录，没有适用于本次编辑区域的 AGENTS.md。dsh 子目录指令不适用于本轮文件，本轮没有编辑 dsh。
- 新增只读盘点 CLI `scripts/inventory_workspace.py`，不读取日记/密钥正文，不打开实际业务 SQLite，不跟随链接目录。
- 真实库元数据盘点：17,856 文件、7,345,635,962 字节；四库 Markdown 448 篇、1,254,295 字节。与设计时统计的细微差异来自盘点时间变化，不据此迁移或清理文件。
- 逐目录大小、SQLite 路径、冲突副本与不可读路径报告仅在用户本机 outputs/P0-inventory-2026-11-13.json，不加入源码仓库。冲突副本均保留，权威选择留到 P2/P3 的只读对账。
- 代码权威源核对：note_editor/pillar_notes 管理 Markdown 和 revision；知识归档复用它们。微信等业务 DB 含唯一数据，原样保留；cache 只能在证据充分后重建。
- 路径依赖：config 的 DATA_DIR/VAULT_DIR/DB_PATH；LovePaths 与媒体路径；cloud_backup/backup 的 Vault 根；obsidian-plugin 的路径哈希与桌面启动；DSH 主会话另有管理目录。P2 必须逐项适配，不能简单移动 Vault。
- 合成临时库已完成 backup.create_backup → verify_backup → restore_backup，恢复到独立新目录，未碰正式库。
- 基线：45 pytest 通过；scripts/check_all.ps1 全部通过。旧文档提到的 tests/test_docs_contract.py 当前不存在，改用实际仓库检查脚本，未伪报该测试通过。

## P1 已实现的代码

- 来源契约与受限网络获取，明确 ChatGPT 结构适配、保留 DeepSeek 问答、正文兜底和仅回答模式；CLI 与 API 复用。
- 持久预览快照；新界面 archive/digest 使用 preview_id/content_hash；归档不重新抓取。
- 独立导入台账、稳定导入标记、并发幂等和“文件已写/台账未提交”恢复。新笔记通过完整临时文件与独占发布避免半份 Markdown 和同名覆盖。
- 本地 ChatGPT 资料包：显式选源、目录与修改日期筛选、完整预览、最多四库四文件、预算检查、密钥疑似阻止、排除规则、源变化检测、导出差异与下载。没有自动上传。
- 知识库入口与操作说明已更新；所有业务写入继续遵循只读限制。

## 验证证据与待完成事项

- 新增 tests/test_share_capture.py：22 项通过，包含失败 URL/重定向、结构化合成 ChatGPT、DeepSeek 过滤、快照一致、过期、并发、中断恢复、只读、资料导出下载、变化拒绝和独立恢复。
- 全量 pytest：67 项通过（45 项既有 + 22 项新增）；仓库 scripts/check_all.ps1 全部通过。存在上游 Starlette/httpx 的弃用提示，不影响本轮结果；不为消除提示改动依赖。
- 用户提供的真实 ChatGPT 样例：HTTP 200 返回仅含标题/链接的页面壳，无问答；捕获器明确失败并支持正文粘贴。真实 ChatGPT 自动取回尚未通过；不能把合成结构测试当成生产支持。
- 已准备百度只读探针和移动端验收步骤；未取得百度授权，未执行云端写入，未完成安卓真机验证。
- 百度探针缺令牌的离线路径已验证：返回 pending_authorization、network_called=false，没有访问真实账号。
- 前端完成语法、模块检查与相关仓库回归；尚未在用户正在使用的真实界面上逐项点击验收。未重启现有服务。
- 因真实 ChatGPT 取回未通过，本阶段为“P1 基础实现完成、自动抓取外部验收未完成”，不是全部最终流程上线。

## 下一阶段与回退

先复核实际 UI、处理可验证的真实 ChatGPT 页面获取方式并记录限制；无正文时继续正文导入。随后按主设计推进 P2、S0/S1；百度与移动验收前不进入正式迁移。手机实现复用相同 schema/夹具，不运行桌面 Python。

本轮没有修改正式库、账号配置或主数据库 schema，没有接入付费模型、自动上传或发布插件。未提交 git、未升级发布版本。回退可关闭新增来源/资料包 UI 并保留旧 DeepSeek 入口；已归档 Markdown 保留。新建快照台账仅含本轮预览/导入记录，不作为可随意丢弃的凭据文件。
