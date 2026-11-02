# B4a：当前判断与成长历史的一致提交

日期：2026-11-03。分类：B 类后端一致性；对应 D1、D2。B4b 的硬前置。

## 实施

1. **按条目串行化**：`self_archive.supersede_entry` 以 `Vault + 旧 entry_id`
   为键，使用 `memory_store.operation_lock()` 的线程锁 + 跨进程文件锁。
   两个不同的纠正请求不能同时用同一个旧 revision 成功。
2. **提交前重读**：锁内重新读取旧条目；`base_revision` 不一致直接 409。
   写新文件后、标记旧条目前再次读取旧文件 revision；外部编辑不会被覆盖，
   新文件撤回并记录冲突。注意：Obsidian/外部编辑器不参与这把锁，服务只能做
   “提交前重读比对”，不是操作系统级 compare-and-swap；极窄的并发窗口仍需人工核对。
3. **持久化操作记录**：`.torch-os/self-archive-ops/<hash>.json` 保存
   `operation_id / kind / status / old_entry_id / old_path / old_revision /
   new_entry_id / new_path / error`，不保存第二份正文。
   `create_entry` 与 `supersede_entry` 都支持稳定 `operation_id`；同 id 重复请求
   返回同一结果，不重复写入。
4. **恢复规则**：`recover_incomplete_operations()` 只自动完成一种情况——
   新文件存在、旧文件仍 active、旧 revision 与记录一致。其他情况标记
   conflict/failed，不猜测、不覆盖外部修改。只读模式只报告，不写文件。
5. **读取视图**：未完成 supersede 且旧条目仍 active 时，新条目从“当前”列表隐藏，
   在历史列表带 `operation_pending` 标记；恢复完成前不会出现两个当前版本。
6. **触发时机**：真实个人实例启动时执行一次恢复；另提供
   `GET /api/self/archive/operations` 与
   `POST /api/self/archive/operations/recover`（`confirm=true`）供显式检查。
7. **待确认卡幂等**：`mentor_pending.accept_pending` 按卡片 id 加锁，
   已采纳卡片重试返回同一结果；重复采纳只写一个条目。

## 验证

`scripts/test_self_archive_ops.py` 10 项通过：重复操作、同 revision 竞争、
外部编辑冲突、中断恢复、恢复冲突、只读、创建幂等、重复/并发采纳。

## 迁移与回退

- 新增最小操作元数据，旧条目不批量改写；不改前端目录或业务文件夹。
- 回退前先处理未完成操作；回退执行器时保留已生成的新版、旧版和操作记录。
- 不靠删除历史回退；未完成操作不会被自动重放。
