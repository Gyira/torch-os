// 滴答同步的纯展示/校验函数。
import test from "node:test";
import assert from "node:assert/strict";
import {
  conflictReasonText, syncBadge, syncCountsText, syncErrorText, syncWarningText,
  validateSyncForm,
} from "../dida-sync-model.js";

test("sync errors map to readable Chinese guidance", () => {
  assert.match(syncErrorText("bad_cookie"), /重新登录/);
  assert.match(syncErrorText("rate_limited"), /限流/);
  assert.match(syncErrorText("weird_code"), /weird_code/);
});

test("sync counts omit zero noise and keep important anomalies", () => {
  const text = syncCountsText({
    created: 2, updated: 1, unchanged: 3, conflicts: 1, errors: 0, remote_deleted: 4,
    projects_created: 0, projects_deleted: 2, created_pushed: 2, updated_pushed: 3,
    moved_pushed: 1, projects_created_pushed: 1, projects_renamed_pushed: 2,
    projects_deleted_pushed: 3,
  });
  assert.match(text, /新建 2/);
  assert.match(text, /冲突 1/);
  assert.match(text, /远端删除 4/);
  assert.match(text, /删除清单 2/);
  assert.match(text, /回写新清单 1/);
  assert.match(text, /回写清单改名 2/);
  assert.match(text, /回写删除清单 3/);
  assert.match(text, /回写新增 2/);
  assert.match(text, /回写更新 3/);
  assert.match(text, /回写移动 1/);
  assert.doesNotMatch(text, /错误 0/);
});

test("sync warnings explain what to do instead of leaking codes", () => {
  assert.match(syncWarningText("push_delete_error"), /_csrf_token/);
  assert.match(syncWarningText("push_changes_csrf_required"), /_csrf_token/);
  assert.match(syncWarningText("project_delete_error_HTTPException"), /非任务文件/);
  assert.match(syncWarningText("push_error_ETAG"), /回写/);
  assert.match(syncWarningText("project_push_error_EXCEED_QUOTA"), /清单未能写回/);
  assert.match(syncWarningText("project_create_unconfirmed"), /确认/);
  assert.match(syncWarningText("project_rename_unconfirmed"), /确认/);
  assert.match(syncWarningText("project_create_list_gone"), /改名或删除/);
  assert.match(syncWarningText("project_create_cleanup_failed"), /空清单/);
  assert.match(syncWarningText("project_link_missing"), /同步链接/);
  assert.match(syncWarningText("local_push_unmapped_list"), /尚未映射/);
  assert.match(syncWarningText("project_seen_pending"), /完整拉取/);
  assert.match(syncErrorText("EXCEED_QUOTA"), /上限/);
  assert.equal(syncWarningText("some_new_code"), "some_new_code");
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
