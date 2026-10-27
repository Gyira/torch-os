// 列车追踪：关注列表纯函数、卡片摘要与“和恋爱系统无关”的静态边界。
import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  MAX_WATCHED_TRAINS, addWatchCode, cardModel, formatCountdown,
  normalizeTrainCode, normalizeWatch, removeWatchCode, selectWatchCode, statusTone,
} from "../train-tracker-model.js";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("train codes are upper-cased and validated", () => {
  assert.equal(normalizeTrainCode(" g1 "), "G1");
  assert.equal(normalizeTrainCode("D123"), "D123");
  assert.equal(normalizeTrainCode("C1"), "C1");
  assert.equal(normalizeTrainCode("1"), "");
  assert.equal(normalizeTrainCode("G"), "");
  assert.equal(normalizeTrainCode("G123456"), "");
  assert.equal(normalizeTrainCode("G1 / G2"), "");
});

test("watchlist migrates a legacy array, dedupes and keeps a valid selection", () => {
  assert.deepEqual(normalizeWatch(["g1", "G1", "D2", "bad"]),
    { codes: ["G1", "D2"], selected: "G1" });
  assert.deepEqual(normalizeWatch({ codes: ["G1", "D2"], selected: "D2" }),
    { codes: ["G1", "D2"], selected: "D2" });
  assert.deepEqual(normalizeWatch({ codes: ["G1"], selected: "G9" }),
    { codes: ["G1"], selected: "G1" });
  assert.deepEqual(normalizeWatch(null), { codes: [], selected: "" });
});

test("add, select and remove keep the watchlist consistent", () => {
  let watch = normalizeWatch([]);
  watch = addWatchCode(watch, "g1");
  assert.deepEqual(watch, { codes: ["G1"], selected: "G1", error: "" });
  watch = addWatchCode(watch, "D2");
  assert.deepEqual(watch.codes, ["G1", "D2"]);
  assert.equal(watch.selected, "D2");
  watch = selectWatchCode(watch, "G1");
  assert.equal(watch.selected, "G1");
  watch = removeWatchCode(watch, "G1");
  assert.deepEqual(watch, { codes: ["D2"], selected: "D2", error: "" });
  assert.match(addWatchCode(watch, "??").error, /格式/);
  let full = { codes: Array.from({ length: MAX_WATCHED_TRAINS }, (_, i) => `G${i + 1}`), selected: "G1" };
  assert.match(addWatchCode(full, "D99").error, /最多关注/);
});

test("card summary covers empty, missing timetable, running and arrived", () => {
  assert.equal(cardModel(null, "").code, "添加车次");
  const missing = cardModel({ found: false, code: "G9", error: "没有" }, "G9");
  assert.equal(missing.status, "缺少时刻表");
  assert.equal(missing.tone, "unknown");

  const train = {
    train_code: "G1", from_station: "北京南", to_station: "上海虹桥",
    depart_time: "10:00", arrive_time: "11:00",
  };
  const running = cardModel({
    found: true, code: "G1", train,
    realtime: {
      status: "running", status_text: "运行中", current_station: "济南西",
      next_station: "上海虹桥", next_arrive_time: "11:00", progress_pct: 63,
    },
  }, "G1");
  assert.equal(running.route, "北京南 → 上海虹桥");
  assert.match(running.meta, /济南西 → 上海虹桥/);
  assert.equal(running.progress, 63);
  assert.equal(running.tone, "running");

  const arrived = cardModel({
    found: true, code: "G1", train,
    realtime: { status: "arrived", status_text: "已到达", progress_pct: 100 },
  }, "G1");
  assert.equal(arrived.tone, "done");
  assert.match(arrived.meta, /已到达 上海虹桥/);
});

test("countdown and tone helpers are stable", () => {
  assert.equal(formatCountdown(35), "35 分钟");
  assert.equal(formatCountdown(90), "1 小时 30 分钟");
  assert.equal(formatCountdown(120), "2 小时");
  assert.equal(formatCountdown(-1), "");
  assert.equal(statusTone({ status: "before_depart" }), "waiting");
  assert.equal(statusTone({ status: "at_station" }), "station");
  assert.equal(statusTone({ status: "nope" }), "unknown");
});

test("the workbench widget has no love-system wording", async () => {
  const html = await readFile(path.join(frontend, "index.html"), "utf8");
  const start = html.indexOf('id="workbench-train-tracker"');
  const end = html.indexOf('id="workbench-deepseek"', start);
  assert.ok(start > 0 && end > start);
  const snippet = html.slice(start, end);
  assert.match(snippet, /data-widget="train-tracker"/);
  assert.match(snippet, /data-widget-label="列车追踪"/);
  assert.doesNotMatch(snippet, /恋爱|女朋友|值乘|交路|乘务/);
  const workbench = await readFile(path.join(frontend, "workbench.js"), "utf8");
  assert.match(workbench, /initTrainTrackerWidget/);
  assert.match(workbench, /loadTrainTrackerWidget/);
});
