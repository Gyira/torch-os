# WeFlow 微信组件（WCDB）修复

> 对应功能：恋爱系统 → 微信聊天；对应界面：设置抽屉 → 微信组件修复。

## 1 问题

WeFlow 随包分发的 `resources/resources/wcdb/win32/x64/wcdb_api.dll`
是闭源预编译组件，内置到期校验。到期后：

- `InitProtection` 返回 `-101`
- `wcdb_init` 失败
- WeFlow 启动/同步报错 101

它属于第三方组件的授权/到期保护。**本项目不修改、不破解、不提供绕过补丁。**

## 2 合法替代

使用开源项目重新实现的 WCDB C ABI 包装器：

- 项目地址：<https://github.com/olansoft/weflow-wcdb-api-oss>
- 它自带腾讯 WCDB `v2.1.15` 的 Windows/macOS 预编译库与构建说明；
- 编译产物 `wcdb_api.dll` 没有到期校验；
- 替换的是 WeFlow 的包装层，不修改微信数据库，也不修改 WeFlow 业务代码。

## 3 构建（Windows x64）

前置：Visual Studio 2022（C++ 桌面开发）、CMake >= 3.16、vcpkg。

```powershell
git clone https://github.com/olansoft/weflow-wcdb-api-oss.git
cd weflow-wcdb-api-oss
# 按仓库 README 的 Windows 3.1 节生成 WCDB.lib 并构建：
#   lib /def:deps\win32\x64\WCDB.def /machine:x64 /out:deps\win32\x64\WCDB.lib
#   cmake -S . -B build-win-x64 -G "Visual Studio 17 2022" -A x64 ...
#   cmake --build build-win-x64 --config Release
```

产物：`build-win-x64\Release\wcdb_api.dll`。

本仓库也提供：

- `scripts/build_weflow_wcdb.ps1`：检查前置并执行上述构建；
- `.github/workflows/build-weflow-wcdb.yml`：在 GitHub Actions 里构建并上传 artifact。

构建完成后，把 `wcdb_api.dll` 放到：

```
assets/weflow/wcdb_api.dll
```

torch 的「微信组件修复」会优先使用这个文件；也可以每次手动选择。

## 4 一键替换与回滚

1. 完全退出 WeFlow（包括托盘）。
2. 恋爱系统 → 顶部齿轮 → 微信组件修复。
3. 点「重新检查」确认路径与当前哈希。
4. 点「选择已构建的 DLL 并替换」；如果把 DLL 放在 `assets/weflow/`，也可以点
   「使用内置 DLL 一键替换」：
   - 备份当前 DLL 到 `01_恋爱系统/_归档/weflow-component/<时间>/`；
   - 校验新文件是 PE（`MZ` 头）且不超过 64MB；
   - 原子替换；
   - 记录旧/新 SHA-256。
5. 重启 WeFlow，回到微信聊天页测试连接。
6. 如果出问题，点「回滚」，把最近一次备份放回。

> 替换前必须退出 WeFlow；否则 Windows 会锁定 DLL，替换失败。

## 5 安全边界

- 不上传微信数据库、密钥或 Token 到本项目服务器；
- 不读取微信密码；
- 开源组件与腾讯 WCDB 的许可证/使用条款由用户自行确认；
- issue 评论区中直接改十六进制跳过 `InitProtection` 的补丁**不在本项目支持范围内**。
