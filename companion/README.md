# Torch Companion

可构建的 Obsidian 跨平台测试插件，独立 ID `torch-companion`、版本 0.1.0。包含百度同步、手机首页/日志/速记、ChatGPT/DeepSeek 分享取回和正文兜底、持久预览与归档、电脑回退队列、手机资料包和恢复导出。

**当前是独立测试库验收版本。真实百度账号、安卓真机和正式迁移尚未验收；没有替换正在使用的同步。**

## 开发与安装

Node 24 及以上用于开发；手机运行不需要 Node/Python。

```powershell
npm ci --ignore-scripts
npm run typecheck
npm test
npm run build
```

输出 `dist/torch-companion/`，包含 main.js、manifest.json、styles.css 和第三方许可；打包脚本另加 INSTALL.md 与 BUILD.json，不含配置、令牌、密码或笔记。复制整个目录到独立测试库 `.obsidian/plugins/`，在 Obsidian 启用插件并首次配对。以后打开同一库自动加载，自动首页可关闭，恢复前台不抢编辑焦点。

在仓库根目录可运行 `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/package_companion.ps1 -Destination D:\Torch-Companion-Test.zip` 打包；先运行 scripts/check_all.ps1。打包仅允许上述六个文件，拒绝混入本机状态，生成主文件哈希并核对 ZIP 清单。

完整流程见 [操作手册](../docs/COMPANION_RUNBOOK.md)，实现决策见 [ADR](../docs/STORAGE_AND_SYNC_ADR.md)，实际证据见 [实施记录](../IMPLEMENTATION_PROGRESS.md)。

## 范围

- 默认四库 Markdown，单篇 1 MiB；只纳入笔记引用的小图片/PDF，单附件 1 MiB、总预算 50 MiB。未引用媒体、微信 DB、大文件、配置、缓存不参与同步。
- 另外只允许 `_Torch/收件/<UUID>.md` 和 `_Torch/回执/<UUID>.md`。工作区 UUID 由配对建立，本机设备身份独立。
- 内容和提交 AES-GCM 加密，网盘仍可见哈希名称、大小、数量和活动。口令默认仅在内存，选择记住才保存到本机配置；令牌与本机日志也必须避开其他同步工具。
- 已知基线 Markdown 经 Obsidian Vault.process 核对后写入；并发分叉保留并显式合并。删除和已有二进制变更需审阅，不按时间覆盖。
- 一个活动库只用一种同步引擎。旧 Torch 工作台可继续用；上游百度同步、百度桌面文件夹同步、Obsidian Sync 不能与 Companion 同时管理本库。
- ChatGPT 内部匿名 JSON 回退可能改版。电脑真实样例成功不代表安卓网络成功；可粘贴正文或进入电脑队列。不调用付费模型，不替换 DSH。
- 前台/运行时同步，失败退避；不保证锁屏后台运行。没有自动历史清理，10,000 提交扫描预算触发会停止，需保留历史处理。

协议、恢复和故障注入测试使用合成数据。实际安卓编辑体验、百度 API 和正式恢复仍须独立验证，不能用 mock 冒充。

## 来源

版本图、协调器、恢复日志和产品流程由 Torch 实现。MD5、multipart 辅助模块来自 owl234/obsidian-baidu-netdisk-sync 的固定审阅提交，MIT 许可在 `third-party/obsidian-baidu-netdisk-sync/` 并随安装包提供；没有使用上游按时间覆盖的同步引擎。
