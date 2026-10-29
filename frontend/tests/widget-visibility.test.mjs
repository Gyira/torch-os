import test from "node:test";
import assert from "node:assert/strict";

import {
  applyWidgetVisibility, clearWidgetHidden, isWidgetHidden,
  readStoredWidgetHidden, resolveWidgetHidden, storeWidgetHidden,
} from "../widget-order.js";

test("hidden keys are filtered to known widgets and deduplicated", () => {
  assert.deepEqual(resolveWidgetHidden(["weather", "unknown", "weather"]), ["weather"]);
  assert.deepEqual(resolveWidgetHidden(null), []);
  assert.equal(isWidgetHidden("weather", ["weather"]), true);
  assert.equal(isWidgetHidden("countdown", ["weather"]), false);
});

test("visibility hides the matching data-widget nodes without removing them", () => {
  const weather = { dataset: { widget: "weather" }, hidden: false };
  const countdown = { dataset: { widget: "countdown" }, hidden: false };
  const columns = {
    left: { children: [weather, countdown] },
    right: { children: [] },
  };
  const applied = applyWidgetVisibility(columns, ["weather"]);
  assert.deepEqual(applied, ["weather"]);
  assert.equal(weather.hidden, true);
  assert.equal(countdown.hidden, false);
  assert.equal(columns.left.children.length, 2, "隐藏只设置 hidden，不删除节点");
});

test("hidden preferences round-trip through localStorage", () => {
  const store = new Map();
  globalThis.window = { localStorage: {
    getItem: (key) => store.has(key) ? store.get(key) : null,
    setItem: (key, value) => store.set(key, String(value)),
    removeItem: (key) => store.delete(key),
  } };
  clearWidgetHidden();
  assert.deepEqual(readStoredWidgetHidden(), []);
  storeWidgetHidden(["weather", "weather", "deepseek"]);
  assert.deepEqual(readStoredWidgetHidden(), ["weather", "deepseek"]);
  clearWidgetHidden();
  assert.deepEqual(readStoredWidgetHidden(), []);
});
