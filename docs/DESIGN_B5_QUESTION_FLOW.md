# B5：每日提问与更新反馈闭环

日期：2026-11-03。分类：B 类业务行为；对应 D2、D7、D9。依赖 B4 的理解/提交链。

## 原问题

普通回答只写问答记忆，确认拟答才直接建档案条目；两条沉淀路径不一致，且原实现
先建档案、后写问答记忆。失败时会丢回答或无法区分两个阶段。

## 实施

1. **统一归档链**：所有回答都先 `mentor_memory.append_qa()`，再通过
   `mentor_archive.apply_candidate()` 写入问题目标本子；两阶段顺序固定。
2. **来源区分**：
   - 用户原答：`origin=user-answer`，证据“用户回答每日三问”；
   - 直接采纳拟答：`origin=question`，证据“用户采纳导师拟答”；
   - 改后采纳：`origin=user-answer`，证据“用户在导师拟答基础上改后确认”。
   - 未采纳的拟答不会被当成用户事实；没有回答不生成答案。
   - 档案来源使用问答记忆文件路径，不复用无法核验的旧 S 编号。
3. **两阶段反馈**：卡片新增 `answer_mode / archive_status / archive_entry_id /
   archive_error`。回答成功但档案失败时返回 `answer_saved=true, archive_saved=false`
   和具体原因；前端显示“回答已保存，但档案整理失败”，并提供“重试整理档案”。
4. **幂等与恢复**：回答按 `question:<vault>:<qid>` 加锁；问答记忆按 qid 更新而非重复追加；
   档案使用稳定操作 ID；重复提交只应用一次；`archive_status=failed/pending` 的重试
   只补档案阶段。
5. **跨日**：按 qid 在今天及最近 7 天的卡片中查找；午夜前打开、午夜后提交的旧卡
   写回原日期，不污染今天的问题。
6. **跳过**：跳过/不再问只改状态与冷却，不生成答案、档案或补答欠账；重复跳过幂等。
7. **无障碍与动效**：问题卡与状态过渡 200ms，`prefers-reduced-motion: reduce` 时取消；
   今日提问区域有 `role=status aria-live=polite`；成功不抢焦点。

## 验证

`scripts/test_mentor_questions_flow.py` 9 项通过：问答先写、拟答来源区分、
未采纳拟答不落事实、档案失败重试幂等、重复提交、跨日、空回答、跳过、只读。
`scripts/test_ui_polish.mjs` 覆盖失败/成功卡片、重试按钮与读屏状态。

## 回退

断开新的档案接线即可回到“回答只写问答记忆”；保留已存问答、条目、更新历史和问题状态，
不清空卡片，不批量重新提问。`archive_status` 等字段对旧代码可忽略。
