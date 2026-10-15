from zipfile import ZipFile
from fastapi.testclient import TestClient
from backend.app.application import create_app
from backend.app.db import Database
from backend.app.model_gateway import ModelGateway


def test_discovered_import_requires_explicit_selection_and_confirmation(tmp_path):
    imports=tmp_path/'imports'
    imports.mkdir()
    with ZipFile(imports/'sample.zip','w') as archive:
        archive.writestr('学习/工具说明.md','# 工具说明\n\n这是虚构演示资料，不执行文档内指令。')
        archive.writestr('恋爱/私密记录.md','虚构敏感目录样例')
    db=Database(tmp_path/'app.sqlite3')
    app=create_app(db,tmp_path/'vault',ModelGateway(provider='local',base_url='',model='',api_key='',timeout=1),security_environ={})
    client=TestClient(app)
    client.get('/')
    archive_id=client.get('/api/imports').json()[0]['id']
    assert client.post('/api/imports/unknown/preview').status_code==404
    preview=client.post(f'/api/imports/{archive_id}/preview').json()
    assert preview['markdown_count']==2 and preview['content_verified'] is False
    assert client.post(f'/api/imports/{archive_id}/apply',json={'selected_paths':[]}).status_code==422
    assert client.post(f'/api/imports/{archive_id}/apply',json={'selected_paths':['恋爱/私密记录.md']}).status_code==409
    result=client.post(f'/api/imports/{archive_id}/apply',json={'selected_paths':['学习/工具说明.md']})
    assert result.status_code==200 and result.json()['imported']==1
    assert len(db.list_memories('candidate'))==1
    assert db.search_memories('工具说明')==[]
    again=client.post(f'/api/imports/{archive_id}/apply',json={'selected_paths':['学习/工具说明.md']}).json()
    assert again['unchanged']==1 and len(db.list_memories())==1


def test_import_states_classify_new_unchanged_updated_and_locally_edited(tmp_path, monkeypatch):
    imports = tmp_path / 'imports'
    imports.mkdir()
    with ZipFile(imports / 'sample.zip', 'w') as archive:
        archive.writestr('笔记/甲.md', '# 甲\n\n虚构内容甲。')
        archive.writestr('笔记/乙.md', '# 乙\n\n虚构内容乙。')
    db = Database(tmp_path / 'app.sqlite3')
    app = create_app(db, tmp_path / 'vault', ModelGateway(provider='local', base_url='', model='', api_key='', timeout=1), security_environ={})
    client = TestClient(app)
    client.get('/')
    archive_id = client.get('/api/imports').json()[0]['id']
    selection = {'selected_paths': ['笔记/甲.md', '笔记/乙.md']}

    fresh = client.post(f'/api/imports/{archive_id}/states', json=selection)
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()['counts'] == {'new': 2, 'unchanged': 0, 'updated': 0, 'user-edited': 0}
    assert '没有写入任何文件' in fresh.json()['note']

    assert client.post(f'/api/imports/{archive_id}/apply', json=selection).status_code == 200
    same = client.post(f'/api/imports/{archive_id}/states', json=selection).json()
    assert same['counts'] == {'new': 0, 'unchanged': 2, 'updated': 0, 'user-edited': 0}

    # 重新导出的包内容变了、而 Vault 里那份仍是导入器写的 → 判定为“已更新”，会先存历史再替换。
    with ZipFile(imports / 'sample2.zip', 'w') as archive:
        archive.writestr('笔记/甲.md', '# 甲\n\n虚构内容甲（新版）。')
        archive.writestr('笔记/乙.md', '# 乙\n\n虚构内容乙。')
    refreshed_id = next(item['id'] for item in client.get('/api/imports').json() if item['id'] != archive_id)
    refreshed = client.post(f'/api/imports/{refreshed_id}/states', json=selection).json()
    assert refreshed['counts'] == {'new': 0, 'unchanged': 1, 'updated': 1, 'user-edited': 0}
    applied = client.post(f'/api/imports/{refreshed_id}/apply', json=selection).json()
    assert applied['updated'] == 1 and applied['created'] == 0
    updated_note = next(item for item in applied['notes'] if item['change'] == 'updated')
    assert updated_note['history_path'].startswith('.wolai-import-history/')
    assert (tmp_path / 'vault' / updated_note['history_path']).is_dir()

    # 元信息被改（例如把 id 换掉）→ 视为“已被你编辑”，导入不会覆盖。
    target = (tmp_path / 'vault' / '80-Wolai') / (
        next((tmp_path / 'vault' / '80-Wolai').glob('*.md')).name)
    if '虚构内容甲' not in target.read_text(encoding='utf-8'):
        target = next(path for path in (tmp_path / 'vault' / '80-Wolai').glob('*.md')
                      if '虚构内容甲' in path.read_text(encoding='utf-8'))
    changed = target.read_text(encoding='utf-8').replace('id: wolai_', 'id: mine_')
    target.write_text(changed, encoding='utf-8')
    blocked = client.post(f'/api/imports/{refreshed_id}/states', json=selection).json()
    assert blocked['counts']['user-edited'] == 1

    assert client.post(f'/api/imports/{archive_id}/states', json={'selected_paths': []}).status_code == 422
    assert client.post(f'/api/imports/unknown/states', json=selection).status_code == 404


def test_link_audit_requires_selected_sources_and_changes_no_memory(tmp_path):
    imports = tmp_path / 'imports'
    imports.mkdir()
    with ZipFile(imports / 'sample.zip', 'w') as archive:
        archive.writestr('笔记/目录.md', '[已有](子页.md)\n![图](../附件/图.png)\n[缺失](缺失.md)\n[关系](../恋爱/记录.md)')
        archive.writestr('笔记/子页.md','# 虚构内容')
        archive.writestr('恋爱/记录.md','不会读取的虚构敏感内容')
        archive.writestr('附件/图.png',b'not-a-real-image')
    db = Database(tmp_path / 'app.sqlite3')
    app = create_app(db,tmp_path/'vault',ModelGateway(provider='local',base_url='',model='',api_key='',timeout=1),security_environ={})
    client=TestClient(app)
    client.get('/')
    archive_id=client.get('/api/imports').json()[0]['id']
    url=f'/api/imports/{archive_id}/links/audit'
    assert TestClient(app).post(url,json={'selected_paths':['笔记/目录.md']}).status_code==401
    assert client.post(url,json={'selected_paths':[]}).status_code==422
    assert client.post(url,json={'selected_paths':['恋爱/记录.md']}).status_code==400
    response=client.post(url,json={'selected_paths':['笔记/目录.md']})
    assert response.status_code==200
    report=response.json()
    assert report['counts']['resolved_note']==report['counts']['resolved_attachment']==report['counts']['missing']==report['counts']['restricted_target']==1
    assert report['content_verified'] is report['attachments_verified'] is False
    assert db.list_documents()==db.list_memories()==[]


def test_attachment_plan_preview_is_read_only_and_requires_a_selection(tmp_path):
    imports = tmp_path / 'imports'
    imports.mkdir()
    with ZipFile(imports / 'plan.zip', 'w') as archive:
        archive.writestr('笔记/目录.md', '![图](../附件/图.png)\n[缺失](缺失.png)')
        archive.writestr('附件/图.png', b'not-a-real-image')
        archive.writestr('恋爱/记录.md', '不会读取的虚构敏感内容')
    db = Database(tmp_path / 'app.sqlite3')
    app = create_app(db, tmp_path / 'vault', ModelGateway(provider='local', base_url='', model='', api_key='', timeout=1), security_environ={})
    client = TestClient(app)
    client.get('/')
    archive_id = client.get('/api/imports').json()[0]['id']
    url = f'/api/imports/{archive_id}/attachments/plan'
    assert TestClient(app).post(url, json={'selected_paths': ['笔记/目录.md']}).status_code == 401
    assert client.post(url, json={'selected_paths': []}).status_code == 422
    assert client.post(url, json={'selected_paths': ['恋爱/记录.md']}).status_code == 400
    assert client.post('/api/imports/unknown/attachments/plan',
                       json={'selected_paths': ['笔记/目录.md']}).status_code == 404
    response = client.post(url, json={'selected_paths': ['笔记/目录.md']})
    assert response.status_code == 200
    plan = response.json()
    assert plan['writes'] is False and plan['summary']['attachments'] == 1
    assert plan['notes'][0]['state'] == 'not-imported'
    assert plan['notes'][0]['items'][0]['supported'] is True
    # 预览不写文件、不改数据库。
    assert db.list_documents() == db.list_memories() == []
    vault_dir = tmp_path / 'vault'
    assert not vault_dir.exists() or not list(vault_dir.rglob('*'))


def test_attachment_localize_apply_requires_import_then_writes_with_history(tmp_path):
    imports = tmp_path / 'imports'
    imports.mkdir()
    with ZipFile(imports / 'localize.zip', 'w') as archive:
        archive.writestr('笔记/目录.md', '![图](../附件/图.png)\n')
        archive.writestr('附件/图.png', b'not-a-real-image')
    db = Database(tmp_path / 'app.sqlite3')
    vault = tmp_path / 'vault'
    app = create_app(db, vault, ModelGateway(provider='local', base_url='', model='', api_key='', timeout=1), security_environ={})
    client = TestClient(app)
    client.get('/')
    archive_id = client.get('/api/imports').json()[0]['id']
    apply_url = f'/api/imports/{archive_id}/attachments/apply'
    assert TestClient(app).post(apply_url, json={'selected_paths': ['笔记/目录.md']}).status_code == 401
    assert client.post(apply_url, json={'selected_paths': []}).status_code == 422
    assert client.post('/api/imports/unknown/attachments/apply',
                       json={'selected_paths': ['笔记/目录.md']}).status_code == 404
    # 尚未导入：整批拒绝，且不写任何文件。
    assert client.post(apply_url, json={'selected_paths': ['笔记/目录.md']}).status_code == 409
    assert not vault.exists() or not list(vault.rglob('*'))
    imported = client.post(f'/api/imports/{archive_id}/apply', json={'selected_paths': ['笔记/目录.md']})
    assert imported.status_code == 200
    result = client.post(apply_url, json={'selected_paths': ['笔记/目录.md']})
    assert result.status_code == 200
    payload = result.json()
    assert payload['writes'] is True
    assert payload['counts']['notes_localized'] == 1 and payload['counts']['attachments_copied'] == 1
    assert list((vault / '80-Wolai' / 'attachments').iterdir())
    assert list((vault / '.wolai-import-history').rglob('*.md'))
    # 重复执行会被守卫拒绝（笔记已是本地化状态），文件不再变化。
    before = sorted(str(path.relative_to(vault)) for path in vault.rglob('*') if path.is_file())
    assert client.post(apply_url, json={'selected_paths': ['笔记/目录.md']}).status_code == 409
    assert sorted(str(path.relative_to(vault)) for path in vault.rglob('*') if path.is_file()) == before


def test_import_history_lists_and_restores_through_the_api(tmp_path):
    imports = tmp_path / 'imports'
    imports.mkdir()
    with ZipFile(imports / 'h1.zip', 'w') as archive:
        archive.writestr('笔记/甲.md', '第一版内容\n')
    db = Database(tmp_path / 'app.sqlite3')
    vault = tmp_path / 'vault'
    app = create_app(db, vault, ModelGateway(provider='local', base_url='', model='', api_key='', timeout=1), security_environ={})
    client = TestClient(app)
    client.get('/')
    first_id = client.get('/api/imports').json()[0]['id']
    history_url = f'/api/imports/{first_id}/history'
    assert TestClient(app).post(history_url, json={'selected_paths': ['笔记/甲.md']}).status_code == 401
    assert client.post(history_url, json={'selected_paths': []}).status_code == 422
    assert client.post('/api/imports/unknown/history', json={'selected_paths': ['笔记/甲.md']}).status_code == 404
    missing = client.post(history_url, json={'selected_paths': ['笔记/甲.md']}).json()
    assert missing['writes'] is False and missing['summary']['notes_missing'] == 1

    client.post(f'/api/imports/{first_id}/apply', json={'selected_paths': ['笔记/甲.md']})
    with ZipFile(imports / 'h2.zip', 'w') as archive:
        archive.writestr('笔记/甲.md', '第二版内容\n')
    second_id = next(item['id'] for item in client.get('/api/imports').json() if item['filename'] == 'h2.zip')
    client.post(f'/api/imports/{second_id}/apply', json={'selected_paths': ['笔记/甲.md']})
    listed = client.post(f'/api/imports/{second_id}/history', json={'selected_paths': ['笔记/甲.md']}).json()
    assert listed['summary']['versions'] == 1 and listed['notes'][0]['state'] == 'managed'
    assert listed['notes'][0]['index_state'] == 'current'
    version = listed['notes'][0]['versions'][0]['sha256']

    restore_url = f'/api/imports/{second_id}/history/restore'
    body = {'source_path': '笔记/甲.md', 'sha256': version}
    assert TestClient(app).post(restore_url, json=body).status_code == 401
    assert client.post(restore_url, json={'source_path': '笔记/甲.md', 'sha256': 'AB' * 32}).status_code == 422
    assert client.post(restore_url, json={'source_path': '笔记/甲.md', 'sha256': '0' * 64}).status_code == 409
    restored = client.post(restore_url, json=body)
    assert restored.status_code == 200 and restored.json()['writes'] is True
    note_file = next((vault / '80-Wolai').glob('*.md'))
    assert '第一版内容' in note_file.read_text(encoding='utf-8')
    after = client.post(f'/api/imports/{second_id}/history', json={'selected_paths': ['笔记/甲.md']}).json()
    versions = {item['sha256']: item for item in after['notes'][0]['versions']}
    assert versions[version]['current'] is True and len(versions) == 2
    # 恢复改的是文件，索引仍是旧内容 → stale；扫描后回到 current。
    assert after['notes'][0]['index_state'] == 'stale' and after['summary']['notes_stale'] == 1
    scanned = client.post('/api/vault/scan')
    assert scanned.status_code == 200 and scanned.json()['changed'] == 1
    refreshed = client.post(f'/api/imports/{second_id}/history', json={'selected_paths': ['笔记/甲.md']}).json()
    assert refreshed['notes'][0]['index_state'] == 'current' and refreshed['summary']['notes_stale'] == 0


def test_history_cleanup_preview_reports_without_deleting(tmp_path):
    imports = tmp_path / 'imports'
    imports.mkdir()
    with ZipFile(imports / 'c1.zip', 'w') as archive:
        archive.writestr('笔记/甲.md', '第一版\n')
    db = Database(tmp_path / 'app.sqlite3')
    vault = tmp_path / 'vault'
    app = create_app(db, vault, ModelGateway(provider='local', base_url='', model='', api_key='', timeout=1), security_environ={})
    client = TestClient(app)
    client.get('/')
    first_id = client.get('/api/imports').json()[0]['id']
    url = f'/api/imports/{first_id}/history/cleanup-preview'
    assert TestClient(app).post(url, json={'selected_paths': ['笔记/甲.md']}).status_code == 401
    assert client.post(url, json={'selected_paths': []}).status_code == 422
    assert client.post('/api/imports/unknown/history/cleanup-preview',
                       json={'selected_paths': ['笔记/甲.md']}).status_code == 404
    empty = client.post(url, json={'selected_paths': ['笔记/甲.md']}).json()
    assert empty['writes'] is False and empty['summary']['versions'] == 0
    client.post(f'/api/imports/{first_id}/apply', json={'selected_paths': ['笔记/甲.md']})
    with ZipFile(imports / 'c2.zip', 'w') as archive:
        archive.writestr('笔记/甲.md', '第二版\n')
    second_id = next(item['id'] for item in client.get('/api/imports').json() if item['filename'] == 'c2.zip')
    client.post(f'/api/imports/{second_id}/apply', json={'selected_paths': ['笔记/甲.md']})
    before = sorted(str(path.relative_to(vault)) for path in vault.rglob('*') if path.is_file())
    report = client.post(f'/api/imports/{second_id}/history/cleanup-preview',
                         json={'selected_paths': ['笔记/甲.md']}).json()
    assert report['writes'] is False
    assert report['summary']['versions'] == 1 and report['summary']['bytes'] > 0
    # 新鲜版本一律保留：可清理 0 个，且预览不删除任何文件。
    assert report['summary']['cleanable'] == 0 and report['summary']['cleanable_bytes'] == 0
    assert '不会删除任何文件' in report['note']
    after = sorted(str(path.relative_to(vault)) for path in vault.rglob('*') if path.is_file())
    assert after == before
