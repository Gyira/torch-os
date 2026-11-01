# WeFlow WCDB 组件产物

把从开源项目构建出的 `wcdb_api.dll` 放在本目录：

- 来源：<https://github.com/olansoft/weflow-wcdb-api-oss>
- 构建：`powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_weflow_wcdb.ps1`
- 或下载 `.github/workflows/build-weflow-wcdb.yml` 的 artifact。

应用设置里的「微信组件修复」会优先使用本目录的 `wcdb_api.dll`；
也可以每次手动选择文件。

本目录的 DLL 被 `.gitignore` 忽略，不进入仓库。
