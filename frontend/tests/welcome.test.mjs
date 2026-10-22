import test from "node:test";
import assert from "node:assert/strict";
import { shouldShowWelcome } from "../welcome.js";

test("welcome appears only for a genuinely empty day", () => {
  assert.equal(shouldShowWelcome(null), false);
  assert.equal(shouldShowWelcome({}), true);
  assert.equal(shouldShowWelcome({ journal: { recorded: true } }), true);
  assert.equal(shouldShowWelcome({ todos: [{}] }), false);
  assert.equal(shouldShowWelcome({ daily: [{}] }), false);
  assert.equal(shouldShowWelcome({ habits: [{}] }), false);
  assert.equal(shouldShowWelcome({}, true), false);
});
