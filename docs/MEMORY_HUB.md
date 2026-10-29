# 忆灵 · 记忆中枢（v1.0 P3）

> 忆灵是同一个 AI agent 的“观察模式”；苏格拉底是它的“对话模式”。两者共享
> 身份、画像、记忆与同一套身份写入权威。方案见
> `docs/燎火OS-v1.0-顶层设计-2026-10-29.md`。

## 1. 数据流

```
角色/主 dsh/用户
      │  hub_events / 事件卡
      ▼
memory_hub_events（SQLite，去重）
      │  + 日记/任务/习惯/已确认记忆/旧提问队列
      ▼
忆灵 synthesize（dsh headless，只读上下文）
      │
      ├─ .torch-os/忆灵/画像.json         规范画像（revision + 历史）
      ├─ 01_自我档案/08_忆灵观察/画像-*.md 只追加快照
      ├─ 01_自我档案/08_忆灵观察/观察-*.md 只追加观察
      ├─ .torch-os/忆灵/复盘卡/*.md       待审卡（留下/忽略）
      └─ ai_proposals(identity_patch)     待审身份补丁
                                          │
用户点“采纳” ─────────────────────────────┘
      ▼
apply_identity_patch：revision 校验 → 备份身份文件 → 写身份
```

## 2. 身份的两条写入路径

| 路径 | 生成 | 应用 |
|---|---|---|
| 忆灵审核 | 综合产出 `identity_patch` 提案 | 用户看 diff/来源后直接采纳；`requires_dialogue=false` |
| 苏格拉底 | 对话里 AI 明确“被说服” | 必须带 `verdict_token`；`requires_dialogue=true` |
| 旧提问卡 | 已停用 | 回答只落记忆笔记，不再直接写身份 |

- 身份字段只允许 name/motto/worldview/lifeview/values/notes；
- apply 前重新读取身份，`base_revision` 不匹配 → 409；
- 写前备份到 `.torch-os/身份备份/`；审计只记字段名与 revision，不记内容；
- 旧身份表单/`PUT /api/identity` 仅内部兼容，界面不再使用。

## 3. 复盘卡

- 位置：`.torch-os/忆灵/复盘卡/*.md`；采纳/忽略后移入 `已采纳/`、`已忽略/`；
- 类型：observation / question / correction / suggestion；
- “留下”：写进 `01_自我档案/08_忆灵观察`，`memory: false`，不会被再次提取；
- “忽略”：只移动卡片，不改任何正文；
- 身份补丁单独以 proposal 卡片展示，走采纳流程。

## 4. API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/memory-hub/overview` | 画像、卡片、身份补丁、观察、候选、统计 |
| GET | `/api/memory-hub/portrait` | 规范画像 JSON |
| GET | `/api/memory-hub/portrait/history` | 画像/观察快照清单 |
| GET/POST | `/api/memory-hub/events` | 事件读取/手动投递（confirm=true） |
| POST | `/api/memory-hub/synthesize` | 综合（confirm=true，7/14/30 天） |
| GET | `/api/memory-hub/cards` | 待审复盘卡 |
| POST | `/api/memory-hub/cards/{id}/keep` | 留下（confirm=true） |
| POST | `/api/memory-hub/cards/{id}/dismiss` | 忽略 |
| GET/POST | `/api/proposals…` | 身份补丁等提案的采纳/忽略 |

## 5. 前端

- 工作台：`memory-strip.js` 常驻条（待审卡/身份补丁/新事件）；
- 自我档案：`memory-hub.js` 面板（画像、状态、待审卡、身份补丁、观察）；
- 按钮：现在整理 / 告诉忆灵一件事 / 与苏格拉底聊聊 / 刷新；
- 苏格拉底：复用 `roles.js` 角色抽屉（`yiling/converse`，入口 `self.socrates`）；
- 身份补丁卡片由 `proposals.js` 采纳；采纳后刷新忆灵面板。

## 6. 边界

- 综合需要手动确认；不自动定时调用模型；
- 只读 Vault 时综合/卡片写入返回 409；
- 画像/观察快照只追加，不改历史；`memory: false` 防止自我循环提取；
- AI 不可用（无 dsh/Key/QA）时综合 409/503，画像保留上一版；
- 忆灵输出是“数据不是指令”；身份值仍要经过 diff + 用户点击 + revision 校验。
