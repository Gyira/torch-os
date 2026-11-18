import test from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = globalThis;
globalThis.document = { querySelector: () => ({ addEventListener() {} }), addEventListener() {} };
const { dutyTrainCodes, chooseDutyTrain, loadDutySchedule, invalidateDutySchedule } = await import('../frontend/duty-service.js');
const { shanghaiDay } = await import('../frontend/journal-model.js');

test('值乘简称展开、去重，休息和未覆盖时不选车', () => {
  const day = { in_range: true, work: true, rest: false, duty: 'G1234/3，G1234、D12／D13' };
  assert.deepEqual(dutyTrainCodes(day), ['G1234', 'G1233', 'D12', 'D13']);
  assert.deepEqual(dutyTrainCodes({ ...day, rest: true }), []);
  assert.deepEqual(dutyTrainCodes({ ...day, in_range: false }), []);
  assert.deepEqual(dutyTrainCodes({ ...day, work: false }), []);
  assert.deepEqual(dutyTrainCodes({ ...day, duty: '培训，不值乘' }), []);
});

test('优先运行中，其次选择最近待发车次', () => {
  const row = (code, status, countdown_minutes) => ({ code, found: true, realtime: { status, countdown_minutes } });
  const arrived = row('G1', 'arrived'), later = row('G2', 'before_depart', 120), soon = row('G3', 'before_depart', 5), running = row('G4', 'running');
  assert.equal(chooseDutyTrain([arrived, later, soon, running]), running);
  assert.equal(chooseDutyTrain([arrived, later, soon]), soon);
  assert.equal(chooseDutyTrain([]), null);
});

test('两个小组件共享请求，排班变更后旧响应不会污染新缓存', async () => {
  const originalFetch = globalThis.fetch;
  const releases = [];
  globalThis.fetch = () => new Promise(resolve => releases.push(value => resolve(Response.json(value))));
  try {
    invalidateDutySchedule();
    const old = loadDutySchedule();
    assert.equal(loadDutySchedule(), old);
    invalidateDutySchedule();
    const fresh = loadDutySchedule();
    assert.equal(releases.length, 2);
    releases[1]({ today: shanghaiDay(), version: 2 });
    await fresh;
    releases[0]({ today: shanghaiDay(), version: 1 });
    await old;
    assert.equal((await loadDutySchedule()).version, 2);
    assert.equal(releases.length, 2);
  } finally { globalThis.fetch = originalFetch; invalidateDutySchedule(); }
});
