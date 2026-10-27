import test from "node:test";
import assert from "node:assert/strict";
import {
  COUNTDOWN_STORAGE_KEY, countdownState, defaultCountdown,
  loadCountdown, normalizeCountdown, saveCountdown,
} from "../countdown-model.js";

test("默认倒计时指向下一个元旦，并沿用参考项目的标签", () => {
  const config = defaultCountdown(new Date("2026-10-26T12:00:00"));
  assert.equal(config.label, "New Year Countdown");
  assert.equal(config.targetDate, "2027-01-01T00:00");
  assert.equal(config.displayMode, "days");
  assert.equal(config.theme, "sunset");
  assert.equal(config.image, "");
});

test("天/小时/分钟三种显示模式都按向上取整", () => {
  const target = "2026-10-27T12:00";
  const now = new Date("2026-10-26T12:00:00");
  const base = { label: "测试", targetDate: target, reminderDays: 0, theme: "sunset", image: "" };
  assert.equal(countdownState({ ...base, displayMode: "days" }, now).value, 1);
  assert.equal(countdownState({ ...base, displayMode: "hours" }, now).value, 24);
  assert.equal(countdownState({ ...base, displayMode: "minutes" }, now).value, 1440);
  assert.equal(countdownState({ ...base, displayMode: "minutes" }, now).unit, "分钟");
});

test("到期与提醒期状态", () => {
  const config = { label: "纪念日", targetDate: "2026-10-28T12:00", displayMode: "days", reminderDays: 3, theme: "dusk", image: "" };
  const active = countdownState(config, new Date("2026-10-26T12:00:00"));
  assert.equal(active.days, 2);
  assert.equal(active.reminderActive, true);
  assert.equal(active.expired, false);
  const expired = countdownState(config, new Date("2026-10-28T12:00:01"));
  assert.equal(expired.expired, true);
  assert.equal(expired.value, 0);
  assert.equal(expired.reminderActive, false);
});

test("配置归一化会夹住提醒天数并拒绝非法背景", () => {
  const config = normalizeCountdown({
    label: "  生日  ", targetDate: "2026-12-01T00:00", displayMode: "weeks",
    reminderDays: 999, theme: "nope", image: "javascript:alert(1)",
  }, new Date("2026-10-26T12:00:00"));
  assert.equal(config.label, "生日");
  assert.equal(config.displayMode, "days");
  assert.equal(config.reminderDays, 365);
  assert.equal(config.theme, "sunset");
  assert.equal(config.image, "");
});

test("存入本机存储并读回", () => {
  const store = new Map();
  const storage = { getItem: (key) => store.get(key) ?? null, setItem: (key, value) => store.set(key, value) };
  const saved = saveCountdown(storage, {
    label: "旅行", targetDate: "2026-12-20T08:30", displayMode: "hours",
    reminderDays: 2, theme: "aurora", image: "",
  });
  assert.equal(saved.label, "旅行");
  assert.equal(store.has(COUNTDOWN_STORAGE_KEY), true);
  const loaded = loadCountdown(storage);
  assert.equal(loaded.targetDate, "2026-12-20T08:30");
  assert.equal(loaded.displayMode, "hours");
  assert.equal(loaded.theme, "aurora");
});

test("损坏的本地配置回退到默认值", () => {
  const storage = { getItem: () => "{not json", setItem() {} };
  const config = loadCountdown(storage);
  assert.equal(config.label, "New Year Countdown");
});
