# 对话原文归档与 ChatGPT 资料包

## 使用

知识库首页可打开 ChatGPT 或 DeepSeek。点“粘贴分享链接”后粘贴公开 URL，或在正文兜底框粘贴所需原文；正文模式不会自动推断角色、轮次。链接模式支持轮次选择及仅保留回答。

取回后展示来源、获取时间、获取方式和完整性。确认归档时保存预览对应的快照，不重新抓取网页。直接归档不调用模型；导师整理仍是单独的提案操作。相同内容重复归档返回已有笔记；需要另一本子的副本时明确勾选独立副本。

预览有效期 24 小时，过期需重新预览。快照和导入记录保存在应用数据库同目录、按 Vault 隔离的 `share-captures-<scope>.sqlite3`。它包含私人原文，不应当作为笔记同步文件；后续存储拆分将统一接入 state 根。归档笔记带稳定 share_import_id 和来源指纹，中断重试可按此恢复。

旧 url-only archive/digest 请求仍兼容，会在请求时取回；新版界面统一提交 preview_id/content_hash。旧客户端兼容路径不享有“预览与归档同版本”保证，应逐步升级。

ChatGPT 自动取回的真实支持范围见 `MOBILE_FEASIBILITY.md`。访问限制或未知页面结构会报错；粘贴正文不宣称已与网页核验。只含回答的分享不会编造用户问题。模型字段未知时保留 unknown。

## 资料包

归档窗口内点“导出 ChatGPT 资料包”。默认不选任何内容；按目录/主题路径和修改日期筛选后逐篇勾选。日期筛选是文件修改日期，并不推断日记记录日期；筛选不会取消隐藏条目的勾选，预览列出实际包含内容。

最多按四库生成四份 Markdown，包含完整选中原文、来源路径、源哈希与时间，不自动摘要、不包含附件。默认 2 MB 总输出预算，超限拒绝并要求缩小范围。疑似密钥、系统目录、备份、微信原始目录和越界/链接路径被阻止；未检出密钥不代表已完全脱敏。

预览后源文件变化，导出返回冲突并要求重新预览。本地包位于数据库目录下 `context-packs/<vault-scope>/<pack-id>/`；manifest 留在本机。点击下载后，由用户自行上传或替换 ChatGPT 项目文件。上次导出资料变化会提示；新增笔记仍需手动选入。程序不知道用户是否实际上传，界面不显示“ChatGPT 已更新”。

## CLI

`scripts/share_link.py` 与 HTTP API 共用 capture 核心，支持分享 URL、DeepSeek 裸 ID、`--rounds`、`--answers-only`、`--json` 和 `--out`。`--text-file` 可读取本机 UTF-8 正文，不访问分享页面；`--out` 用独占创建，不覆盖已有文件。

## API

- POST `/api/knowledge/share/preview`：url/rounds/answers_only 或 pasted_text，返回 preview_id/content_hash。
- POST `/api/knowledge/share/archive`：preview_id/content_hash/confirm/chapter/title，可选 allow_copy。
- POST `/api/knowledge/share/digest`：同一预览快照，创建导师提案。
- GET `/api/knowledge/context-pack/sources`：可选 Markdown 清单，默认全不选。
- POST `/api/knowledge/context-pack/preview`：paths、可选 budget_bytes，返回源清单与全文。
- POST `/api/knowledge/context-pack/export`：同上，并提交 expected_hash。
- GET `/api/knowledge/context-pack/status`：比较上次选源哈希，上传状态始终未知。

本轮没有迁移个人数据库、重启用户后端、部署安卓插件或接入付费模型。更新后的界面与 API 需要一起加载；正常重启 Torch 后端并刷新页面后使用。
