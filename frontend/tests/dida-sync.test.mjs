import test from "node:test";
import assert from "node:assert/strict";

import {
  conflictReasonText,
  syncBadge,
  syncCountsText,
  syncWarningText,
} from "../dida-sync-model.js";

test("syncCountsText includes habit and checkin counts", () => {
  const text = syncCountsText({
    habits_created: 2, habits_updated_pushed: 1, habits_archived: 1,
    checkins_updated: 5, checkins_created_pushed: 3,
    checkins_deleted_pushed: 2, habit_conflicts: 1,
  });
  assert.match(text, /新建习惯 2/);
  assert.match(text, /回写习惯 1/);
  assert.match(text, /归档习惯 1/);
  assert.match(text, /拉取打卡 5/);
  assert.match(text, /回写打卡 3/);
  assert.match(text, /回写取消打卡 2/);
  assert.match(text, /习惯冲突 1/);
});

test("syncWarningText maps habit warnings", () => {
  assert.match(syncWarningText("habit_title_ambiguous"), /同名/);
  assert.match(syncWarningText("checkin_mass_delete_guard"), /误删/);
  assert.match(syncWarningText("habit_fetch_error_http_404"), /习惯列表拉取失败/);
  assert.match(syncWarningText("checkin_query_error_checkins_read_forbidden"), /打卡查询失败/);
});

test("conflictReasonText maps habit reasons", () => {
  assert.equal(conflictReasonText("first_link_differs"), "首次关联时两边设置不同");
  assert.equal(conflictReasonText("checkin_both_changed"), "本地和滴答的打卡都改过");
  assert.equal(conflictReasonText("remote_deleted_local_changed"), "滴答已删除，但本地也改过");
});

test("syncBadge treats habits_enabled as enabled", () => {
  assert.equal(syncBadge({
    supported: true, configured: true, enabled: false, habits_enabled: true,
  }).text, "已启用");
});
