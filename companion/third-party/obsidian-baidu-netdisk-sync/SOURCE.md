# 百度 API 使用参考

来源：https://github.com/owl234/obsidian-baidu-netdisk-sync

固定提交：8b4e03b35ad42558a00cc9753465fc1d1f105a3f（manifest 1.0.13）。

参考 `src/baidu/client.ts` 的 XPan 文件列表参数和令牌错误码处理；Torch 重新实现了受限 HTTP 端口、严格分页验证、令牌刷新后重建请求和错误脱敏。没有复制上游 LWW 规划器。随附上游 MIT LICENSE 以保留来源和许可说明。

该参考不代替真实百度账号与接口验收；当前没有执行真实上传、删除或账号授权。
