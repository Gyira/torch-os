# 与神对话：ChatGPT 与 DSH

- 顶部“与神对话”增加下拉，提供 ChatGPT、DSH 两项；页内也可切换，偏好随现有个人设置保存。
- DSH 继续使用原有同源页面。切到 ChatGPT 后暂停 DSH 状态轮询，保留页面。
- 桌面 Obsidian：前端只上报内容区位置，Torch 宿主校验 iframe 来源和后端 origin 后创建原生 webview。固定初始地址为 https://chatgpt.com/，没有 iframe 代理或响应头改写。
- ChatGPT 使用本机持久网页分区；没有 Node 集成、预加载脚本、页面内容提取或自动上传资料。外部浏览器登录与此分区独立。
- 内容区随尺寸及滚动调整；切页、弹窗及顶栏下拉展开时隐藏 guest，避免覆盖操作。关闭工作台清理 guest；加载失败/超时提供提示及外部打开入口。
- 浏览器版不能提供该桌面 webview，显示明确的外部打开入口。手机 Companion 本次未修改。

## 验证与使用

42 项 Node 宿主/插件路径/UI/请求测试通过；120 个模块导入导出检查及结构检查通过。`scripts/browser_agent_probe.py` 的 5 项专项浏览器验收通过，截图和报告位于 `artifacts/agent-focused/`。

全页面 `browser_material_probe.py` 中新增下拉、平台切换、浏览器降级检查通过；完整流程在既有设置弹窗关闭步骤超时，另一次出现导航指示器位置检查失败，不能视作全量通过。真实 Obsidian webview 的创建、ChatGPT 网络可达性、账号登录和验证码流程仍须用户实际验证。

本机桌面插件升级为 2.2.2 后，需要重启 Obsidian，或者停用再启用“薪火OS”并重新打开工作台。然后选择“与神对话 → ChatGPT”。只刷新前端不更新宿主代码。

## 2026-11-19 复查：空白与占位文案（2.2.3）

本机实测（Obsidian 1.12.7 / Electron webview 可用）：

- 宿主、iframe、postMessage 通道都正常；`<webview>` 能被创建并成功载入 example.com，说明嵌入能力本身没问题。
- **真正的原因在网络**：`chatgpt.com` 被 DNS 污染（系统解析到 199.59.149.239，PTR 是 `assets1-backup.twitter.jp`；223.5.5.5 回 157.240.3.8，119.29.29.29 回 45.77.186.255），直连超时；guest 里最终停在 `https://www.iana.org/help` 一类劫持页。Windows 系统代理关闭，配置里的 `127.0.0.1:7892` 也没有进程在听。
- 因此“显示不出来”是**这台机器打不开 ChatGPT**，不是插件没加载。

2.2.3 的改动：

- 新增设置项“ChatGPT 内嵌浏览器代理（仅本机）”：填写本机代理（如 `127.0.0.1:7892`）后，只用 `session.fromPartition('persist:torch-chatgpt').setProxy` 作用于 ChatGPT 分区，不动宿主主会话；留空跟随系统代理。
- 宿主不再只看 `dom-ready`：落点域名不是 chatgpt.com/openai.com 就判定为被劫持/直连失败，收起 guest，并把原因写回 iframe 的内容区（`.chatgpt-overlay` 卡片 + 重新载入/浏览器打开），避免一片空白顶掉提示。
- 前端 bounds 上报增加重试（切页、开合顶栏下拉造成的瞬时不可见不会再让占位文案一直留着），同位置高频重复不再重发。
- 验证：`scripts/test_chatgpt_host.mjs` 等 61 项 Node 测试通过；新增 `scripts/browser_agent_probe.py` 之外的 headless 检查（bounds 可见、blocked 卡片、ready 收起、DSH 回切）通过。

## 2026-11-19 复查补丁：登录页不是劫持

2.2.3 第一次落地时“落点域名不对就判失败”误伤了正常登录：点「使用 Google 登录」本来就会跳到 `accounts.google.com` 走 OAuth，结果被当成 DNS 劫持，卡片直接把登录页盖掉了（用户实测反馈）。

现在的判定：

- 落地是 chatgpt.com / openai.com → ready。
- 落地是已知登录域（`accounts.google.com`、`appleid.apple.com`、`login.microsoftonline.com` 等，见 `AUTH_HOSTS`）→ 保持 ready 且**不收起 guest**，状态写“正在登录…”。
- 已经成功进过 ChatGPT（`chatgptSeen`）之后，用户点出去的任何页面都不拦。
- 只有「一次都没到过 ChatGPT、落地域名又不认识」才判劫持并弹卡片。
- `did-fail-load` 记录失败 URL，3 秒内同一 URL 的 `dom-ready` 不再洗成成功；已进过 ChatGPT 时只提示不收起页面。
- 登录弹窗（`new-window` 命中登录域）改为在同一个 guest 里打开，避免弹到外部浏览器后丢失分区登录态。
- 重载停在登录页时重载登录页，不顺带跳回 chatgpt.com。
