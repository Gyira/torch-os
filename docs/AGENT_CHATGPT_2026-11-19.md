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
