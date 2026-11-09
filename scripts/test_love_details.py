"""Detail extraction regression: temporary vault, real role policy, fake dsh output."""
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from backend.app import love_details as ld, love_master as lm
from backend.app.ai_gateway import HeadlessResult
from backend.app.ai_roles import RoleRegistry, RoleService
from backend.app.db import Database
from backend.app.love_layout import love_paths

REPO = Path(__file__).resolve().parents[1]


class DetailTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.vault = Path(self.temp.name) / 'vault'
        self.vault.mkdir()
        self.paths = love_paths(self.vault)
        self.paths.chat.mkdir(parents=True)
        with closing(sqlite3.connect(self.paths.chat_db)) as con, con:
            con.executescript("CREATE TABLE contact(id,display_name,partner_sender,me_sender);"
                              "INSERT INTO contact VALUES(1,'小胡','小胡','小王');"
                              "CREATE TABLE messages(id INTEGER PRIMARY KEY,day,ts,is_send,content);")
            con.executemany("INSERT INTO messages VALUES(?,?,?,?,?)", [
                (10,'2026-05-20',1,0,'小胡不吃香菜，电话13812345678'),
                (20,'2026-11-09',2,0,'[语音消息]')])
        self.db = Database(Path(self.temp.name) / 'state.sqlite')
        self.db.initialize()
        self.service = RoleService(RoleRegistry(REPO / 'dsh/roles'), database=self.db,
                                   vault_dir=self.vault, love_settings=self.paths)
        self.row = {'topic':'饮食-香菜','category':'忌口','detail':'不吃香菜。',
                    'followup':'','action':'点单备注。','evidence_ids':[10]}
        self.service._execute = Mock(return_value=HeadlessResult(
            json.dumps({'items':[self.row], 'reflection':''}, ensure_ascii=False), '', .01, 0, ''))

    def generate(self, base=None):
        return ld.generate(self.paths, self.service, base or {}, lambda _: None, lambda _: None)

    def test_first_scan_and_noop_increment(self):
        data = self.generate()
        self.assertEqual(data['items'][0]['date'], '2026-05-20')
        self.assertEqual(data['watermark'], 20)
        prompt = self.service._execute.call_args.args[0]
        self.assertNotIn('13812345678', prompt)
        self.assertNotIn('小胡', prompt)
        self.assertIn('返回完整已有条目', prompt)
        self.assertIn('省略我、你、她', prompt)
        self.assertEqual(self.generate(data)['batches'], 0)
        self.assertEqual(self.service._execute.call_count, 1)

    def test_old_date_backfilled_later_is_not_skipped(self):
        base = self.generate()
        with closing(sqlite3.connect(self.paths.chat_db)) as con, con:
            con.execute("INSERT INTO messages VALUES(30,'2026-05-19',0,0,'不吃香菜')")
        row = {**self.row, 'evidence_ids':[10,30]}
        self.service._execute.return_value = HeadlessResult(json.dumps({'items':[row]}),'',.01,0,'')
        data = self.generate(base)
        self.assertEqual(len(data['items']), 1)
        self.assertEqual(data['items'][0]['date'], '2026-05-19')
        self.assertEqual(data['watermark'], 30)

    def test_omitted_or_fabricated_evidence_fails_closed(self):
        sources = {10:{'day':'2026-05-20'}}
        previous = ld.normalize([self.row], [], sources)
        for raw in ([], [{**self.row,'evidence_ids':[999]}], [self.row,self.row]):
            with self.assertRaises(ValueError):
                ld.normalize(raw, previous, sources)

    def test_manual_edits_revision_and_model_cannot_overwrite(self):
        data = self.generate()
        raw = json.dumps({'structured':data})
        edit = ld.DetailsEdit(revision=ld.revision(raw), items=[ld.DetailEdit(
            **{k:self.row[k] for k in ('topic',*ld.FIELDS)}
        )], reflection='少说多做。')
        edit.items[0].detail = '香菜一点都不要。'
        result = ld.edit_result(raw, edit)['structured']
        self.assertTrue(result['items'][0]['manual'])
        merged = ld.normalize([self.row], result['items'], {10:{'day':'2026-05-20'}})
        self.assertEqual(merged[0]['detail'], '香菜一点都不要。')
        self.assertTrue(result['reflection_manual'])
        edit.revision = '0'*64
        with self.assertRaises(HTTPException) as error:
            ld.edit_result(raw, edit)
        self.assertEqual(error.exception.status_code, 409)

    def test_excel_preserves_dates_and_does_not_execute_text(self):
        data = self.generate()
        data['items'][0]['detail'] = '=1+1'
        data['reflection'] = '=HYPERLINK("x")'
        data['sources'][10]['content'] = '=1+1'
        wb = load_workbook(io.BytesIO(ld.export_xlsx(data).body))
        self.assertEqual(wb['小细节记录']['C2'].data_type, 's')
        self.assertEqual(wb['反思草稿']['A1'].data_type, 's')
        self.assertEqual(wb['原聊天依据']['D2'].data_type, 's')
        self.assertEqual(wb['小细节记录']['A2'].value.strftime('%Y-%m-%d'), '2026-05-20')

    def test_chunking_has_no_message_count_cutoff(self):
        messages = [{'id':i, 'day':'2026-05-20','who':'对象','content':'不吃香菜'} for i in range(4500)]
        chunks = list(ld.batches(messages))
        self.assertGreater(len(chunks), 1)
        self.assertEqual(sum(map(len,chunks)),4500)
        self.assertEqual([r['id'] for batch in chunks for r in batch],list(range(4500)))

    def test_writable_routes_save_and_reject_stale_revision(self):
        app=FastAPI()
        app.include_router(lm.create_master_router(self.paths,self.db,role_service=self.service))
        data=self.generate()
        raw=json.dumps({'structured':data})
        with lm._connect(self.paths.master_db) as con:
            run_id=con.execute("INSERT INTO runs(skill,status,result_json,created_at) "
                               "VALUES('details','completed',?,'now')",(raw,)).lastrowid
        client=TestClient(app)
        payload={'revision':ld.revision(raw),'items':[
            {k:self.row[k] for k in ('topic',*ld.FIELDS)}],'reflection':''}
        payload['items'][0]['action']='点餐备注，不要香菜。'
        url=f'/api/love/master/runs/{run_id}/details'
        self.assertEqual(client.put(url,json=payload).status_code,200)
        self.assertEqual(client.put(url,json=payload).status_code,409)
        with lm._connect(self.paths.master_db) as con:
            con.execute("INSERT INTO runs(skill,status,created_at) VALUES('details','running','now')")
        self.assertEqual(client.post('/api/love/master/run',json={'skill':'details'}).status_code,409)

    def test_full_background_run_and_read_only_route(self):
        lm.ensure_master_schema(self.paths)
        with lm._connect(self.paths.master_db) as con:
            run_id = con.execute("INSERT INTO runs(skill,status,created_at) VALUES('details','queued','now')").lastrowid
        lm._execute_run(self.paths,self.db,self.service,'',run_id,'details',{},False)
        app=FastAPI()
        app.include_router(lm.create_master_router(self.paths,self.db,role_service=self.service,read_only=True))
        client=TestClient(app)
        run=client.get(f'/api/love/master/runs/{run_id}').json()
        self.assertEqual(run['status'],'completed')
        self.assertEqual(len(run['revision']),64)
        self.assertEqual(client.get(f'/api/love/master/runs/{run_id}/details.xlsx').status_code,200)
        response=client.put(f'/api/love/master/runs/{run_id}/details',json={
            'revision':run['revision'],'items':[],'reflection':''})
        self.assertEqual(response.status_code,409)


if __name__=='__main__':
    unittest.main()
