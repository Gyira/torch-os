"""Proactive suggestion cards: local rules, dedupe, dismiss, and no side effects."""

import json
from datetime import date, timedelta

from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database
from backend.app.model_gateway import ModelGateway
from backend.app.suggestions import SuggestionService

TODAY = date(2026, 10, 4)


def build(tmp_path, monkeypatch):
    monkeypatch.setattr('backend.app.suggestions.shanghai_today', lambda: TODAY)
    monkeypatch.setattr('backend.app.learning.shanghai_today', lambda: TODAY)
    monkeypatch.setattr('backend.app.habits.shanghai_today', lambda: TODAY)
    monkeypatch.setattr('backend.app.journal.shanghai_today', lambda: TODAY)
    database = Database(tmp_path / 'torch_os.sqlite3')
    gateway = ModelGateway(provider='local', base_url='', model='', api_key='', timeout=1)
    app = create_app(database, tmp_path / 'vault', gateway, security_environ={}, env_file=tmp_path / '.env')
    client = TestClient(app)
    client.get('/')
    return database, client


def snapshot(database):
    """Everything a suggestion must never change."""
    with database.connect() as connection:
        tasks = connection.execute('SELECT COUNT(*) AS total FROM tasks').fetchone()['total']
        habits = connection.execute('SELECT COUNT(*) AS total FROM habit_entries').fetchone()['total']
        journal = connection.execute('SELECT COUNT(*) AS total FROM journal_entries').fetchone()['total']
        memories = connection.execute('SELECT status, COUNT(*) AS total FROM memories GROUP BY status').fetchall()
    return {'tasks': tasks, 'habits': habits, 'journal': journal,
            'memories': {row['status']: row['total'] for row in memories}}


def test_refresh_generates_expected_cards_with_evidence(tmp_path, monkeypatch):
    database, client = build(tmp_path, monkeypatch)
    client.put('/api/schedule', json={'morning_enabled': False, 'evening_enabled': False, 'weekly_enabled': False})
    goal = client.post('/api/goals', json={'title': '完成数据手册', 'category': 'project'}).json()
    client.post('/api/tasks', json={'title': '写表清单', 'goal_id': goal['id'], 'due_date': '2026-10-02'})
    habit = client.post('/api/habits', json={
        'title': '早睡', 'cue': '洗漱后', 'minimum_action': '关灯', 'start_date': '2026-09-01',
        'weekdays': [0, 1, 2, 3, 4, 5, 6],
    }).json()
    assert habit['id']
    subject = client.post('/api/learning/subjects', json={'title': '数学', 'weekly_target_minutes': 300}).json()
    for offset, minutes in ((13, 60), (12, 60), (3, 10)):
        day = (TODAY - timedelta(days=offset)).isoformat()
        created = client.post('/api/learning/sessions', json={
            'subject_id': subject['id'], 'study_date': day, 'minutes': minutes,
            'request_key': f'11111111-2222-4333-8444-5555555555{offset:02d}',
        })
        assert created.status_code == 200, created.text

    before = snapshot(database)
    payload = client.post('/api/suggestions/refresh').json()
    kinds = {item['kind'] for item in payload['suggestions']}
    assert {'study', 'task', 'habit', 'journal'} <= kinds
    study = next(item for item in payload['suggestions'] if item['kind'] == 'study')
    assert study['evidence']['recent_7d_minutes'] == 10
    assert study['evidence']['previous_7d_minutes'] == 120
    assert '25 分钟' in study['action_text']
    task = next(item for item in payload['suggestions'] if item['kind'] == 'task')
    assert task['evidence']['overdue'] == 1 and '写表清单' in task['body']
    habit_card = next(item for item in payload['suggestions'] if item['kind'] == 'habit')
    assert '关灯' in habit_card['action_text']
    assert payload['note'].startswith('建议只是候选')
    assert snapshot(database) == before


def test_refresh_is_idempotent_and_dismiss_hides_the_card(tmp_path, monkeypatch):
    database, client = build(tmp_path, monkeypatch)
    client.put('/api/schedule', json={'morning_enabled': False, 'evening_enabled': False, 'weekly_enabled': False})
    first = client.post('/api/suggestions/refresh').json()
    assert first['created'] >= 1
    second = client.post('/api/suggestions/refresh').json()
    assert second['created'] == 0 and second['skipped'] >= 1
    assert second['open'] == first['open']

    card = first['suggestions'][0]
    dismissed = client.post(f"/api/suggestions/{card['id']}/dismiss").json()
    assert dismissed == {'dismissed': True, 'open': first['open'] - 1}
    remaining = client.get('/api/suggestions').json()
    assert all(item['id'] != card['id'] for item in remaining['suggestions'])
    assert client.post('/api/suggestions/999999/dismiss').status_code == 404
    with database.connect() as connection:
        events = [dict(row) for row in connection.execute(
            'SELECT event_type, detail_json FROM audit_events ORDER BY id').fetchall()]
    assert any(row['event_type'] == 'suggestions_generated' for row in events)
    assert any(row['event_type'] == 'suggestion_dismissed' for row in events)
    assert all('写表清单' not in row['detail_json'] for row in events)


def test_candidate_backlog_and_quiet_day_rules(tmp_path, monkeypatch):
    database, client = build(tmp_path, monkeypatch)
    service = SuggestionService(database, tmp_path / 'vault')
    quiet = service.generate(TODAY)
    kinds = {card['kind'] for card in service.list_open()['suggestions']}
    assert 'candidate-backlog' not in {card['dedupe_key'] for card in service.list_open()['suggestions']}
    assert 'journal' in kinds and 'vault' in kinds
    assert quiet['created'] >= 2

    for index in range(21):
        client.post('/api/notes', json={'title': f'笔记{index}', 'content': f'我在记录第 {index} 条内容。', 'type': 'note'})
    refreshed = service.generate(TODAY)
    cards = service.list_open()['suggestions']
    backlog = next(item for item in cards if item['kind'] == 'memory')
    assert backlog['evidence']['candidates'] >= 20
    assert '先确认 5 条' in backlog['action_text']
    assert refreshed['created'] >= 1


def test_old_databases_without_journal_table_still_refresh(tmp_path):
    database = Database(tmp_path / 'old.sqlite3')
    service = SuggestionService(database, None)
    result = service.generate(TODAY)
    # No journal table and no data yet: the rule is skipped instead of crashing.
    assert result['generated'] == 0 and result['created'] == 0
    assert all(card['kind'] != 'journal' for card in service.list_open()['suggestions'])


def test_scheduler_run_also_generates_cards(tmp_path, monkeypatch):
    database, client = build(tmp_path, monkeypatch)
    from backend.app.scheduler import SchedulerService
    service = SchedulerService(database)
    result = service.run_due(now=__import__('datetime').datetime(2026, 10, 4, 8, 30,
                                                              tzinfo=__import__('zoneinfo').ZoneInfo('Asia/Shanghai')))
    assert 'suggestions' in result
    assert client.get('/api/suggestions').json()['count'] >= 1


def test_snooze_hides_then_returns_and_done_is_a_receipt_only(tmp_path, monkeypatch):
    database, client = build(tmp_path, monkeypatch)
    client.put('/api/schedule', json={'morning_enabled': False, 'evening_enabled': False, 'weekly_enabled': False})
    payload = client.post('/api/suggestions/refresh').json()
    card = payload['suggestions'][0]
    before = snapshot(database)

    snoozed = client.post(f"/api/suggestions/{card['id']}/snooze", json={'days': 1}).json()
    assert snoozed['snoozed'] is True and snoozed['days'] == 1
    assert client.get('/api/suggestions').json()['count'] == payload['count'] - 1

    # 同一天再刷新不会把它带回来（去重键已存在，但仍处于静音期）。
    again = client.post('/api/suggestions/refresh').json()
    assert again['created'] == 0
    assert all(item['id'] != card['id'] for item in again['suggestions'])

    # 到了静音结束的那天，它重新出现在列表里。
    import backend.app.suggestions as module
    monkeypatch.setattr(module, 'shanghai_today', lambda: TODAY + timedelta(days=2))
    assert any(item['id'] == card['id'] for item in client.get('/api/suggestions').json()['suggestions'])

    done = client.post(f"/api/suggestions/{card['id']}/done").json()
    assert done['done'] is True
    assert all(item['id'] != card['id'] for item in client.get('/api/suggestions').json()['suggestions'])

    restored = client.post(f"/api/suggestions/{card['id']}/restore").json()
    assert restored['restored'] is True
    assert any(item['id'] == card['id'] for item in client.get('/api/suggestions').json()['suggestions'])

    assert client.post(f"/api/suggestions/{card['id']}/snooze", json={'days': 0}).status_code == 422
    assert client.post('/api/suggestions/999999/snooze', json={'days': 1}).status_code == 404
    assert client.post('/api/suggestions/999999/done').status_code == 404
    assert client.post('/api/suggestions/999999/restore').status_code == 404
    assert snapshot(database) == before
    with database.connect() as connection:
        events = {row['event_type'] for row in connection.execute('SELECT event_type FROM audit_events').fetchall()}
    assert {'suggestion_snoozed', 'suggestion_done', 'suggestion_restored'} <= events


def test_archive_lists_dismissed_snoozed_and_done_cards_with_state(tmp_path, monkeypatch):
    database, client = build(tmp_path, monkeypatch)
    client.put('/api/schedule', json={'morning_enabled': False, 'evening_enabled': False, 'weekly_enabled': False})
    goal = client.post('/api/goals', json={'title': '归档测试目标', 'category': 'project'}).json()
    client.post('/api/tasks', json={'title': '归档测试任务', 'goal_id': goal['id'], 'due_date': '2026-10-01'})
    client.post('/api/suggestions/refresh')
    cards = client.get('/api/suggestions').json()['suggestions']
    assert len(cards) >= 3
    dismissed, snoozed, done = cards[0], cards[1], cards[2]
    client.post(f"/api/suggestions/{dismissed['id']}/dismiss")
    client.post(f"/api/suggestions/{snoozed['id']}/snooze", json={'days': 7})
    client.post(f"/api/suggestions/{done['id']}/done")

    archive = client.get('/api/suggestions/archive').json()
    states = {item['id']: item['state'] for item in archive['suggestions']}
    assert states[dismissed['id']] == 'dismissed'
    assert states[snoozed['id']] == 'snoozed'
    assert states[done['id']] == 'done'
    assert archive['count'] == 3
    assert '恢复后卡片回到' in archive['note']
    assert all(item['id'] not in {card['id'] for card in client.get('/api/suggestions').json()['suggestions']}
               for item in archive['suggestions'])

    restored = client.post(f"/api/suggestions/{snoozed['id']}/restore").json()
    assert restored['restored'] is True
    assert client.get('/api/suggestions/archive').json()['count'] == 2
    assert any(card['id'] == snoozed['id'] for card in client.get('/api/suggestions').json()['suggestions'])


def test_interrupted_streak_and_cold_subject_rules(tmp_path, monkeypatch):
    database, client = build(tmp_path, monkeypatch)
    client.put('/api/schedule', json={'morning_enabled': False, 'evening_enabled': False, 'weekly_enabled': False})
    # 连续三天记录（10-01…10-03），今天 10-04 还没写 → 中断 1 天。
    for day in ('2026-10-01', '2026-10-02', '2026-10-03'):
        saved = client.put(f'/api/journal/{day}', json={'revision': 0, 'content': f'{day} 的虚构记录'})
        assert saved.status_code == 200, saved.text
    subject = client.post('/api/learning/subjects', json={'title': '英语', 'weekly_target_minutes': 120}).json()
    old_day = (TODAY - timedelta(days=20)).isoformat()
    created = client.post('/api/learning/sessions', json={
        'subject_id': subject['id'], 'study_date': old_day, 'minutes': 30,
        'request_key': '22222222-3333-4444-8555-666666666666',
    })
    assert created.status_code == 200, created.text

    before = snapshot(database)
    payload = client.post('/api/suggestions/refresh').json()
    by_kind = {}
    for item in payload['suggestions']:
        by_kind.setdefault(item['kind'], []).append(item)
    interrupted = next(item for item in by_kind.get('journal', []) if item['evidence'].get('streak_days'))
    assert interrupted['evidence'] == {'streak_days': 3, 'gap_days': 1}
    assert '一句话' in interrupted['action_text']
    assert '连续 3 天' in interrupted['body']
    cold = next(item for item in by_kind.get('study', []) if item['evidence'].get('subject_id') == subject['id'])
    assert cold['evidence']['days_since'] == 20
    assert '25 分钟' in cold['action_text']
    assert snapshot(database) == before

    again = client.post('/api/suggestions/refresh').json()
    assert again['created'] == 0


def test_no_streak_card_when_the_record_is_current_or_has_no_history(tmp_path, monkeypatch):
    database, client = build(tmp_path, monkeypatch)
    client.put('/api/schedule', json={'morning_enabled': False, 'evening_enabled': False, 'weekly_enabled': False})
    service = SuggestionService(database, tmp_path / 'vault')
    # 完全没有记录：由 journal-gap 负责，不会冒出“中断”。
    assert service._journal_interrupted(TODAY) is None
    for day in ('2026-10-02', '2026-10-03', '2026-10-04'):
        client.put(f'/api/journal/{day}', json={'revision': 0, 'content': f'{day} 的记录'})
    assert service._journal_interrupted(TODAY) is None
    # 只有一个新科目、从没记录过：不该被点名。
    client.post('/api/learning/subjects', json={'title': '数学', 'weekly_target_minutes': 60})
    assert service._subject_cold(TODAY) == []


def test_vault_issue_card_links_to_the_library_page(tmp_path, monkeypatch):
    database, client = build(tmp_path, monkeypatch)
    client.put('/api/schedule', json={'morning_enabled': False, 'evening_enabled': False, 'weekly_enabled': False})
    vault = tmp_path / 'vault'
    (vault / '90-Inbox').mkdir(parents=True, exist_ok=True)
    (vault / '90-Inbox' / 'broken.md').write_text('---\ntitle: 缺字段\n---\n\n', encoding='utf-8')
    service = SuggestionService(database, vault)
    before = snapshot(database)
    payload = client.post('/api/suggestions/refresh').json()
    card = next(item for item in payload['suggestions'] if item['dedupe_key'].startswith('vault-issues-'))
    assert card['evidence']['issues'] >= 1
    assert card['evidence']['by_code']
    assert card['evidence']['examples'][0].endswith('broken.md')
    assert card['link_view'] == 'documents'
    assert card['link_label'] == '去资料页处理'
    assert '不会自动改文件' in card['action_text']
    assert snapshot(database) == before
    # 没有问题的库不会产生这张卡。
    clean = tmp_path / 'clean-vault'
    clean.mkdir()
    (clean / 'ok.md').write_text('---\nid: ok-1\ntype: note\ntitle: 正常\n---\n\n我在记录正常内容。\n', encoding='utf-8')
    assert SuggestionService(database, clean)._vault_issues(TODAY) is None


def test_explicit_click_turns_one_card_into_task_draft_without_side_effects(tmp_path, monkeypatch):
    database, client = build(tmp_path, monkeypatch)
    client.put('/api/schedule', json={'morning_enabled': False, 'evening_enabled': False, 'weekly_enabled': False})
    goal = client.post('/api/goals', json={'title': '完成数据手册', 'category': 'project'}).json()
    client.post('/api/tasks', json={'title': '写表清单', 'goal_id': goal['id'], 'due_date': '2026-10-01'})
    payload = client.post('/api/suggestions/refresh').json()
    card = next(item for item in payload['suggestions'] if item['kind'] == 'task')
    before = snapshot(database)

    created = client.post(f"/api/suggestions/{card['id']}/task-draft")
    assert created.status_code == 201, created.text
    result = created.json()
    assert result['created'] is True
    task = result['task']
    assert task['title'] == '先推进：写表清单'
    assert f"来源：建议卡片 #{card['id']}" in task['description']
    assert task['due_date'] == TODAY.isoformat()
    assert task['status'] == 'todo' and task['priority'] == 'medium'
    assert client.get('/api/suggestions').json()['count'] == payload['count'] - 1

    again = client.post(f"/api/suggestions/{card['id']}/task-draft")
    assert again.status_code == 201
    assert again.json()['created'] is False and again.json()['already'] is True
    assert again.json()['task']['id'] == task['id']
    with database.connect() as connection:
        assert connection.execute('SELECT COUNT(*) AS n FROM tasks').fetchone()['n'] == before['tasks'] + 1
        events = [dict(row) for row in connection.execute(
            "SELECT event_type, detail_json FROM audit_events WHERE event_type='suggestion_task_drafted'").fetchall()]
    assert len(events) == 1
    assert json.loads(events[0]['detail_json'])['task_id'] == task['id']
    assert client.post('/api/suggestions/999999/task-draft').status_code == 404

    # 其他数据没有被顺手改动。
    after = snapshot(database)
    assert {key: value for key, value in after.items() if key != 'tasks'} == {
        key: value for key, value in before.items() if key != 'tasks'}


def test_task_draft_titles_cover_the_rule_kinds():
    service = SuggestionService.__new__(SuggestionService)
    assert SuggestionService.task_draft_text({'kind': 'study', 'title': '学习时长比上一周少', 'evidence': {'subject_id': 3}}) == '安排 25 分钟学习（学习时长比上一周少）'
    assert SuggestionService.task_draft_text({'kind': 'study', 'title': 'x', 'evidence': {}}) == '安排 25 分钟学习'
    assert SuggestionService.task_draft_text({'kind': 'task', 'title': 't', 'evidence': {'titles': ['写表清单']}}) == '先推进：写表清单'
    assert SuggestionService.task_draft_text({'kind': 'task', 'title': 't', 'evidence': {}}) == '推进一项到期任务'
    assert SuggestionService.task_draft_text({'kind': 'habit', 'title': '习惯“早睡”最近一周没记录', 'evidence': {}}).startswith('完成习惯：')
    assert SuggestionService.task_draft_text({'kind': 'journal', 'title': 'j', 'evidence': {}}) == '写三句话的每日记录'
    assert SuggestionService.task_draft_text({'kind': 'memory', 'title': 'm', 'evidence': {}}) == '确认 5 条候选记忆'
    assert SuggestionService.task_draft_text({'kind': 'vault', 'title': 'v', 'evidence': {}}) == '处理资料体检问题'


def test_import_drift_card_fires_only_when_imported_files_changed_on_disk(tmp_path, monkeypatch):
    import hashlib

    database, client = build(tmp_path, monkeypatch)
    vault = tmp_path / 'vault'
    folder = vault / '80-Wolai'
    folder.mkdir(parents=True, exist_ok=True)
    note = folder / 'wolai_drift1.md'
    body = '# 导入的虚构笔记\n\n原始内容。\n'
    note.write_text(body, encoding='utf-8')
    original_digest = hashlib.sha256(note.read_bytes()).hexdigest()
    document_id, _ = database.upsert_document('80-Wolai/wolai_drift1.md', '导入的虚构笔记', '原始内容。',
                                              original_digest, {'id': 'wolai_drift1', 'source_kind': 'imported'})
    service = SuggestionService(database, vault)
    assert service._import_drift(TODAY) is None

    note.write_text('# 导入的虚构笔记\n\n本地改过的内容。\n', encoding='utf-8')
    edited_bytes = note.read_bytes()
    card = service._import_drift(TODAY)
    assert card is not None
    assert card['evidence']['drifted'] == 1
    assert card['evidence']['examples'] == ['80-Wolai/wolai_drift1.md']
    assert card['link_view'] == 'documents'
    assert '不会自动改动' in card['action_text']
    assert note.read_bytes() == edited_bytes  # 检查本身不改文件

    payload = client.post('/api/suggestions/refresh').json()
    assert any(item['dedupe_key'] == f'import-drift-{TODAY.isoformat()}' for item in payload['suggestions'])
    again = client.post('/api/suggestions/refresh').json()
    assert again['created'] == 0

    # 索引追上文件内容后卡片不再出现。
    database.upsert_document('80-Wolai/wolai_drift1.md', '导入的虚构笔记', '本地改过的内容。',
                             hashlib.sha256(note.read_bytes()).hexdigest(), {'id': 'wolai_drift1', 'source_kind': 'imported'})
    assert service._import_drift(TODAY) is None
    assert database.get_document(document_id)['available'] == 1


def test_top_pick_prefers_the_most_urgent_due_task(tmp_path, monkeypatch):
    database, client = build(tmp_path, monkeypatch)
    client.put('/api/schedule', json={'morning_enabled': False, 'evening_enabled': False, 'weekly_enabled': False})
    goal = client.post('/api/goals', json={'title': '完成数据手册', 'category': 'project'}).json()
    client.post('/api/tasks', json={'title': '普通任务', 'goal_id': goal['id'], 'due_date': '2026-10-02', 'priority': 'medium'})
    client.post('/api/tasks', json={'title': '紧急任务', 'goal_id': goal['id'], 'due_date': '2026-10-03', 'priority': 'urgent'})
    payload = client.post('/api/suggestions/refresh').json()
    pick = next(item for item in payload['suggestions'] if item['kind'] == 'top-pick')
    assert pick['title'] == '今天先做：紧急任务'
    assert pick['evidence']['reason'] == 'due_task'
    assert pick['evidence']['priority'] == 'urgent'
    assert pick['evidence']['due_date'] == '2026-10-03'
    assert '不会自动改任务' in pick['action_text']
    assert '今天先做' in pick['title']
    # 变任务草稿沿用通用路径：标题就是这张卡的标题。
    created = client.post(f"/api/suggestions/{pick['id']}/task-draft").json()
    assert created['created'] is True
    assert created['task']['title'] == '今天先做：紧急任务'


def test_top_pick_falls_back_to_the_first_relevant_suggestion(tmp_path, monkeypatch):
    database, client = build(tmp_path, monkeypatch)
    client.put('/api/schedule', json={'morning_enabled': False, 'evening_enabled': False, 'weekly_enabled': False})
    habit = client.post('/api/habits', json={
        'title': '早睡', 'cue': '洗漱后', 'minimum_action': '关灯', 'start_date': '2026-09-01',
        'weekdays': [0, 1, 2, 3, 4, 5, 6],
    }).json()
    assert habit['id']
    service = SuggestionService(database, tmp_path / 'vault')
    cards = service.build_cards(TODAY)
    pick = next(card for card in cards if card['kind'] == 'top-pick')
    assert pick['evidence']['reason'] == 'suggestion'
    assert pick['evidence']['from_kind'] in {'habit', 'journal', 'vault'}
    assert pick['title'].startswith('今天先看：')
    assert '不会自动执行' in pick['action_text']
    # 没有可建议的内容时不产生 top-pick。
    empty_vault = tmp_path / 'clean'
    empty_vault.mkdir()
    quiet = SuggestionService(database, empty_vault)
    assert quiet._top_pick(TODAY, []) is None
