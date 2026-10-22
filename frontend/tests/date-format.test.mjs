import test from "node:test";
import assert from "node:assert/strict";
import { formatRelativeDate } from "../date-format.js";

test("date-only values use a short Chinese date", () => {
  assert.equal(formatRelativeDate("2026-10-22"), "10月22日");
});

test("timestamps use today/yesterday and Shanghai time", () => {
  const now = new Date();
  assert.match(formatRelativeDate(now.toISOString()), /^今天 \d{2}:\d{2}$/);
  const yesterday = new Date(now.getTime() - 86400000);
  assert.match(formatRelativeDate(yesterday.toISOString()), /^昨天 \d{2}:\d{2}$/);
});

test("older years are explicit and invalid text is left alone", () => {
  assert.match(formatRelativeDate("2020-01-02T03:04:05Z"), /^2020年1月2日 \d{2}:\d{2}$/);
  assert.equal(formatRelativeDate("not-a-date"), "not-a-date");
  assert.equal(formatRelativeDate(""), "");
});
