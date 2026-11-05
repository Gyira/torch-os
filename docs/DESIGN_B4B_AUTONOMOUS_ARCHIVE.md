# B4b：人生导师自主更新档案

日期：2026-11-03。分类：B 类业务行为；对应 D1、D2、D3。依赖 B4a 已验收。

## 行为

- 导师对话可返回 `archive_updates`（创建或替代条目）；全量整理的 `archive_patches`
  走同一条链。服务端在 `backend/app/mentor_archive.py` 统一校验：
  - 本子必须是 11 个固定本子之一；`section` 接受本子 key 与常见别名（`motivation`、
    `身份` 等）。`identity` 文件不是本子：学历、职业、家庭等稳定事实落到 `self_facts`；
    仍无法识别时错误里列出全部可用 key，而不是只说“未知”；
  - 替代必须带现有 `entry_id`，且本子与原条目一致；
  - `fact` 必须有能通过角色范围校验的可信来源（本轮 S 编号 / context 来源 / 库内已存在
    Markdown），仅有外部 URL 或虚构路径不算；否则该项失败，不能把模型猜测写成事实；
  - 来源通过 `RolePolicy.reference` 绑定当前上下文；浏览器不能提交“已授权”标记；
  - 新建/替代使用 B4a 操作幂等键，重复请求只应用一次；部分失败逐项返回，不重发模型。
- 成功项写入现有 Markdown 条目（沿用 `entry_id / revision / supersedes`），
  不是第二套记忆库；旧条目保留为 `superseded` 成长历史。
- `memory_notes` 继续写导师记忆时间线，按日期/类型/文本去重。
- 对话响应返回逐项 `archive_updates`：动作、类型、本子、标题、正文、依据、来源、
  操作 ID、当前条目路径、旧版路径、成功/失败原因。
- 前端在回复下方非阻塞显示“本轮档案更新”；不强制切到“待确认”标签、不抢焦点。
  失败项显示原因，成功项可打开当前条目和旧版。
- 旧 `archive_verdict` 仍被接受：自主模式转换为更新，关闭自主模式时生成待确认卡。
- 自主写范围只限自我档案与导师记忆；恋爱、健康、待办等跨域写入仍走各自角色/受控执行器。
- 同批更新角色提示：`dsh/roles/mentor.md` 要求输出 `archive_updates` 并说明事实来源；
  `dsh/roles/AGENTS.md` 把“只有用户采纳后写入”限定为普通提议，明确只有人生导师的
  `archive_updates` / `memory_notes` 是服务端校验后提交的例外。其他角色和 `dsh/AGENTS.md`
  的运行时规则没有改变。

## 开关与回退

`TORCH_MENTOR_AUTONOMOUS_ARCHIVE` 默认开启（`1/true/yes/on`）。
设为 `0` 时：新输出转成旧待确认卡，不自动采纳任何历史卡；已自动写入的条目和历史保留。
代码回退只切回待确认执行模式，不删除记忆、条目或版本链。
`POLICY_VERSION` 已从 `B3-2-v1` 升为 `B4b-v1`，旧模型会话不复用作新能力上下文。

## 验证

`scripts/test_mentor_autonomous.py` 18 项通过：事实/推断标记、无来源/仅 URL/虚构路径
事实拒绝、本地已有来源接受、替代历史链、部分失败、重复请求、开关回退、只读、
全量整理自主/回退、`identity` 归入基础事实、未知本子的报错列出可用 key。
`scripts/test_ui_polish.mjs` 覆盖更新卡片的类型、操作 ID、
历史链接与失败文案。`scripts/check_all.ps1` 全部通过。

本地个人库后端已在原端口 `127.0.0.1:9168` 重启（原 PID 9944 → 最终 PID 7788）：
`/api/config/status` 仍指向个人数据库；`/api/ai/status` 显示 7 个活跃角色、
`mentor_autonomous_archive=true`；`/api/self/archive/operations` 与
`/api/mentor/overview` 返回 200 且未完成操作为 0。未调用真实模型、未创建个人测试记录。
