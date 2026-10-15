"""A small local, read-only agent loop for chat: plan → gather → answer digest.

The loop only ever calls the read-only tools from 0.51.0. It runs entirely on
this machine, and the gathered digest is deliberately *not* sent to a cloud
model: the cloud contract stays "question + confirmed memories only".
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

MAX_STEPS = 3
PLAN_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ('today', ('今天', '今日', '现在', '最近', '这周', '本周', '状态', '安排')),
    ('goals', ('目标', '任务', '计划', '待办', '要做', '截止', '进度')),
    ('habits', ('习惯', '坚持', '打卡', '连续')),
    ('learning', ('学习', '复习', '科目', '分钟', '考研', '英语', '数学')),
    ('body', ('身体', '睡眠', '运动', '体重', '作息')),
    ('documents', ('资料', '笔记', '文档', '索引')),
    ('imports', ('导入', 'wolai', '附件', '历史')),
    ('vault_audit', ('体检', '检查', '问题', '格式')),
    ('vault', ('vault', '库', '只读')),
)
TOOL_LABELS = {
    'memories': '已确认记忆',
    'today': '今天的记录',
    'goals': '目标与任务',
    'habits': '习惯',
    'learning': '学习',
    'vault': 'Vault 状态',
    'body': '身体记录',
    'documents': '资料索引',
    'imports': '资料导入',
    'vault_audit': 'Vault 体检',
}


def plan_steps(question: str) -> list[dict[str, Any]]:
    """Memories first, then at most two intent-matched tools (never a write)."""
    text = str(question or '')
    steps: list[dict[str, Any]] = [{'tool': 'memories', 'params': {'q': text, 'limit': 3}}]
    for tool, keywords in PLAN_RULES:
        if len(steps) >= MAX_STEPS:
            break
        if any(keyword in text for keyword in keywords):
            steps.append({'tool': tool, 'params': {}})
    return steps


def _summarize(tool: str, payload: dict[str, Any]) -> str:
    if tool == 'memories':
        return f"找到 {payload.get('count', 0)} 条已确认记忆"
    if tool == 'today':
        journal = '已记录' if payload.get('journal', {}).get('recorded') else '还没有记录'
        tasks = payload.get('tasks', {})
        study = payload.get('study', {})
        return (f"日记{journal}；未完成任务 {tasks.get('open', 0)} 项、今天到期 {tasks.get('due_today', 0)} 项、逾期 {tasks.get('overdue', 0)} 项；"
                f"学习 {study.get('minutes', 0)} 分钟；身体记录{'有' if payload.get('body', {}).get('recorded') else '无'}")
    if tool == 'goals':
        return f"进行中目标 {len(payload.get('goals', []))} 个，未完成任务 {payload.get('open_task_count', 0)} 项"
    if tool == 'habits':
        habits = payload.get('habits', [])
        done = sum(int(item.get('done') or 0) for item in habits)
        return f"{len(habits)} 个习惯，最近 {payload.get('from_date')} 起完成合计 {done} 次"
    if tool == 'learning':
        return f"最近 {payload.get('from_date')} 起学习 {payload.get('total_minutes', 0)} 分钟，涉及 {len(payload.get('by_subject', []))} 个科目"
    if tool == 'vault':
        mode = '只读模式' if payload.get('read_only') else '可写'
        return f"Vault 有 {payload.get('markdown_files', 0)} 篇 Markdown（{mode}）"
    if tool == 'body':
        return (f"最近 {payload.get('from_date')} 起有身体记录 {payload.get('recorded_days', 0)} 天，"
                f"睡眠 {payload.get('sleep_minutes_total', 0)} 分钟、运动 {payload.get('exercise_minutes_total', 0)} 分钟（不含体重）")
    if tool == 'documents':
        return f"已索引 {payload.get('documents', 0)} 篇资料，最近的一篇是「{(payload.get('recent') or [{}])[0].get('title') or '未命名'}」"
    if tool == 'imports':
        return (f"导入包 {payload.get('packages', 0)} 个、已导入笔记 {payload.get('imported_notes', 0)} 篇、"
                f"历史版本 {payload.get('history_versions', 0)} 个、索引待刷新 {payload.get('notes_index_stale', 0)} 篇")
    if tool == 'vault_audit':
        return f"体检 {payload.get('checked', 0)} 篇，发现 {payload.get('issues', 0)} 个问题"
    return '已读取'


def _count(tool: str, payload: dict[str, Any]) -> int:
    if tool == 'memories':
        return int(payload.get('count') or 0)
    if tool == 'goals':
        return len(payload.get('goals') or [])
    if tool == 'habits':
        return len(payload.get('habits') or [])
    if tool == 'learning':
        return len(payload.get('by_subject') or [])
    if tool == 'today':
        return int(payload.get('tasks', {}).get('open') or 0)
    if tool == 'body':
        return int(payload.get('recorded_days') or 0)
    if tool == 'documents':
        return int(payload.get('documents') or 0)
    if tool == 'imports':
        return int(payload.get('imported_notes') or 0)
    if tool == 'vault_audit':
        return int(payload.get('issues') or 0)
    return 0


def run(service, question: str, cloud_available: bool = False) -> dict[str, Any]:
    """Execute the plan against read-only tools and return a local-only digest."""
    steps = plan_steps(question)
    traces: list[dict[str, Any]] = []
    for step in steps:
        tool = step['tool']
        try:
            if tool == 'memories':
                payload = service.memories(step['params']['q'], step['params'].get('limit', 3))
            elif tool == 'today':
                payload = service.today()
            elif tool == 'goals':
                payload = service.goals(3)
            elif tool == 'habits':
                payload = service.habits(30)
            elif tool == 'learning':
                payload = service.learning(7)
            elif tool == 'vault':
                payload = service.vault()
            elif tool == 'body':
                payload = service.body_summary(7)
            elif tool == 'documents':
                payload = service.documents_summary(3)
            elif tool == 'imports':
                payload = service.imports_summary()
            elif tool == 'vault_audit':
                payload = service.vault_audit()
            else:
                continue
        except HTTPException as error:
            traces.append({'tool': tool, 'label': TOOL_LABELS.get(tool, tool),
                           'summary': f'暂时不可用：{error.detail}', 'items': 0})
            continue
        traces.append({'tool': tool, 'label': TOOL_LABELS.get(tool, tool),
                       'summary': _summarize(tool, payload), 'items': _count(tool, payload)})
    tools = [trace['tool'] for trace in traces]
    useful = any(trace['tool'] != 'memories' or trace['items'] for trace in traces)
    return {'steps': traces, 'tools': tools, 'digest': digest(traces, cloud_available) if useful else ''}


def digest(traces: list[dict[str, Any]], cloud_available: bool = False) -> str:
    if not traces:
        return ''
    lines = ['', '我做的本地步骤（只在这台机器上执行，不会上传）：']
    for trace in traces:
        lines.append(f"- {trace['label']}：{trace['summary']}")
    lines.append('云端模式只发送你的问题与相关已确认记忆；上面这些本地步骤不会发送。' if cloud_available
                 else '上面这些本地步骤只在这台机器上执行，不会发送给任何外部服务。')
    return '\n'.join(lines)
