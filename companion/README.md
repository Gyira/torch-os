# Torch Companion 核心

尚无可安装的 Obsidian 插件。本目录是独立的跨平台版本协议核心，运行环境不依赖 Node API；Node 24 仅用于开发测试。

```powershell
cd companion
npm ci --ignore-scripts
npm test
npm run typecheck
```

核心接口与实现边界见 `../docs/STORAGE_AND_SYNC_ADR.md`。已有版本协议、持久日志和保守磁盘落盘测试；百度只读列举适配仅以模拟响应验证，没有完整云端上传下载、加密或安卓适配。禁止直接用于正式数据同步。

此模块由 Torch 原创实现，尚未复制上游百度插件的实现代码；未来传输适配需保留上游许可证及固定提交来源。

百度列举适配参考上游固定提交的 API 使用方式，来源与许可证见 `third-party/obsidian-baidu-netdisk-sync/`。未复制上游的 LWW 同步引擎。

第二切片增加了可重开的磁盘日志测试和不可变操作/回执恢复。`core/journal.ts` 仍要求外部提供原子比较写入适配器；测试模型不等于真实 Obsidian 文件系统支持。参见 ADR 第二切片补充，当前不可直接安装或同步正式笔记。

第三切片提供独立 `core/materialize.ts` 保守路径：完整新建文件且不覆盖同名文件；对已有笔记保存本机候选版本，返回待处理状态；删除不直接执行。Node 磁盘适配在 `node/files.ts`，不打包进手机。候选与日志在笔记根之外，不冒充已应用或已同步。当前没有候选审阅 UI，也没有自动安全合并。

仓库 `scripts/check_all.ps1` 已包含 npm 类型检查与测试。初次拉取后先按上面的 npm ci 安装依赖。
