// 滴答同步的纯展示/校验函数。
import test from "node:test";
import assert from "node:assert/strict";
import {
  conflictReasonText, syncBadge, syncCountsText, syncErrorText, validateSyncForm,
} from "../dida-sync-model.js";

test("sync errors map to readable Chinese guidance", () => {
  assert.match(syncErrorText("bad_cookie"), /重新登录/);
  assert.match(syncErrorText("rate_limited"), /限流/);
  assert.match(syncErrorText("weird_code"), /weird_code/);
});

test("sync counts omit zero noise and keep important anomalies", () => {
  const text = syncCountsText({
    created: 2, updated: 1, unchanged: 3, conflicts: 1, errors: 0, remote_deleted: 4,
  });
  assert.match(text, /新建 2/);
  assert.match(text, /冲突 1/);
  assert.match(text, /远端删除 4/);
  assert.doesNotMatch(text, /错误 0/);
});

test("conflict reasons are human readable", () => {
  assert.match(conflictReasonText("local_and_remote_changed"), /都改过/);
  assert.equal(conflictReasonText("unknown_reason"), "unknown_reason");
});

test("form validation blocks bad intervals and enabling without credentials", () => {
  const base = {
    enabled: false, interval_minutes: 15, completed_days: 30,
    device_version: 8000, timezone: "Asia/Shanghai", cookie: "", cookie_set: false,
  };
  assert.equal(validateSyncForm(base).ok, true);
  assert.equal(validateSyncForm({ ...base, interval_minutes: 1 }).ok, false);
  assert.equal(validateSyncForm({ ...base, enabled: true }).ok, false);
  assert.equal(validateSyncForm({ ...base, enabled: true, cookie_set: true }).ok, true);
  assert.equal(validateSyncForm({ ...base, device_version: 0 }).ok, false);
});

test("badge reflects support, configuration and error state", () => {
  assert.equal(syncBadge({ supported: false }).text, "不可用");
  assert.equal(syncBadge({ supported: true, read_only: true }).text, "只读库");
  assert.equal(syncBadge({ supported: true, configured: false }).text, "未配置");
  assert.equal(syncBadge({ supported: true, configured: true, enabled: true }).text, "已启用");
  assert.equal(syncBadge({ supported: true, configured: true, last_error_code: "bad_cookie" }).text, "需处理");
});
