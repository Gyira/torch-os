import test from "node:test";
import assert from "node:assert/strict";

import {
  COMPANIONS, companionForEditor, companionForReader, companionForView,
} from "../ai-companions.js";

test("four pillars map to the confirmed role identities", () => {
  assert.equal(companionForView("life").roleId, "journal_companion");
  assert.equal(companionForView("knowledge").roleId, "academic_mentor");
  assert.equal(companionForView("ops").roleId, "life_assistant");
  assert.equal(companionForView("ops", { pane: "love-view" }).roleId, "love_master");
  assert.equal(companionForView("ops", { chapter: "task" }).mode, "tasks");
  assert.equal(companionForView("settings"), null);
});

test("editor contexts choose discuss/tutor/tasks correctly", () => {
  assert.equal(companionForEditor("life").mode, "discuss");
  assert.equal(companionForEditor("knowledge").roleId, "academic_mentor");
  assert.equal(companionForEditor("knowledge").mode, "discuss");
  assert.equal(companionForEditor("ops").mode, "tasks");
  assert.equal(companionForEditor("ops").entry, "workbench.tasks");
});

test("reader selection asks the knowledge mentor in discuss mode", () => {
  assert.equal(companionForReader("knowledge", "topic").mode, "discuss");
  assert.equal(companionForReader("ops", "task").roleId, "life_assistant");
  assert.equal(companionForReader("ops", "task").mode, "tasks");
  assert.equal(companionForReader("life", "journal").roleId, "journal_companion");
  assert.equal(COMPANIONS.love.title, "恋爱大师");
});
