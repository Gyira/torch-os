import test from "node:test";
import assert from "node:assert/strict";

import {
  MAX_SKILLS, STYLE_PRESETS, customProfileCount, filterRoles, isProfileDirty,
  layerLabel, newSkill, preferredPreviewMode, profilePayload, roleInitial,
  skillCountLabel, totalSkillCount, validateProfile,
} from "../role-admin-model.js";

const ROLES = [
  {
    id: "yiling", name: "忆灵", tagline: "维护画像", layer: "identity",
    modes: [{ id: "synthesize", label: "整理" }, { id: "converse", label: "对话" }],
    custom: { identity: true, style: false, skills: 1 },
    skill_names: ["周回看"],
  },
  {
    id: "health_doctor", name: "私人医生", tagline: "读健康记录", layer: "health",
    modes: [{ id: "report", label: "报告" }],
    custom: { identity: false, style: false, skills: 0 },
    skill_names: [],
  },
];

const PROFILE = {
  name: "忆灵",
  tagline: "维护画像",
  identity: "长期观察者",
  style: "温和",
  skills: [{ id: "skill_week", name: "周回看", when: "周日", instructions: "先列事实。" }],
  modes: [{ id: "synthesize" }, { id: "converse" }],
};

test("role layers and initials are presentation-safe", () => {
  assert.equal(layerLabel("identity"), "自我");
  assert.equal(layerLabel("unknown"), "unknown");
  assert.equal(roleInitial("忆灵"), "忆");
  assert.equal(roleInitial(""), "AI");
  assert.equal(skillCountLabel(0), "还没有 Skill");
  assert.equal(skillCountLabel(2), "2 个 Skill");
});

test("search matches names, taglines, modes and skill names, then filters by layer", () => {
  assert.deepEqual(filterRoles(ROLES, "画像", "").map((item) => item.id), ["yiling"]);
  assert.deepEqual(filterRoles(ROLES, "报告", "").map((item) => item.id), ["health_doctor"]);
  assert.deepEqual(filterRoles(ROLES, "周回看", "").map((item) => item.id), ["yiling"]);
  assert.deepEqual(filterRoles(ROLES, "", "health").map((item) => item.id), ["health_doctor"]);
  assert.deepEqual(filterRoles(ROLES, "忆灵", "health"), []);
  assert.equal(filterRoles(ROLES, "", "").length, 2);
});

test("counts summarise custom skills without trusting text fields", () => {
  assert.equal(totalSkillCount(ROLES), 1);
  assert.equal(customProfileCount(ROLES), 1);
  assert.equal(totalSkillCount(null), 0);
  assert.equal(customProfileCount(null), 0);
});

test("profile validation accepts a complete profile and gives field-level errors", () => {
  assert.equal(validateProfile(PROFILE).ok, true);
  assert.equal(validateProfile({ ...PROFILE, name: "  " }).field, "name");
  assert.equal(validateProfile({ ...PROFILE, identity: "字".repeat(4001) }).field, "identity");
  const badSkill = { ...PROFILE, skills: [{ id: "x", name: "", when: "", instructions: "" }] };
  assert.equal(validateProfile(badSkill).field, "skill:0:name");
  const duplicate = {
    ...PROFILE,
    skills: [
      { id: "a", name: "周回看", when: "", instructions: "先列事实。" },
      { id: "b", name: "周回看", when: "", instructions: "先列事实。" },
    ],
  };
  assert.equal(validateProfile(duplicate).field, "skill:1:name");
  const tooMany = {
    ...PROFILE,
    skills: Array.from({ length: MAX_SKILLS + 1 }, (_, index) => ({
      id: `s${index}`, name: `技能${index}`, when: "", instructions: "做。",
    })),
  };
  assert.equal(validateProfile(tooMany).field, "skills");
});

test("payload trims fields and keeps only editable skill data", () => {
  const payload = profilePayload({
    ...PROFILE,
    name: " 忆灵 ",
    skills: [{ ...PROFILE.skills[0], extra: "drop" }],
  });
  assert.equal(payload.name, "忆灵");
  assert.deepEqual(Object.keys(payload.skills[0]).sort(), ["id", "instructions", "name", "when"]);
  assert.equal(payload.expected_revision, undefined);
});

test("dirty checking notices every editable field and recovers on undo", () => {
  const baseline = structuredClone(PROFILE);
  assert.equal(isProfileDirty(PROFILE, baseline), false);
  const edited = structuredClone(PROFILE);
  edited.style = "更短";
  assert.equal(isProfileDirty(edited, baseline), true);
  edited.style = PROFILE.style;
  assert.equal(isProfileDirty(edited, baseline), false);
});

test("style presets are unique and preview mode prefers conversation", () => {
  assert.equal(new Set(STYLE_PRESETS.map((item) => item.id)).size, STYLE_PRESETS.length);
  assert.ok(STYLE_PRESETS.every((item) => item.name && item.text));
  assert.equal(preferredPreviewMode(PROFILE), "converse");
  assert.equal(preferredPreviewMode({ modes: [{ id: "advice" }, { id: "report" }] }), "advice");
  assert.equal(preferredPreviewMode({ modes: [{ id: "custom" }] }), "custom");
  assert.equal(preferredPreviewMode({}), "");
});

test("new skill drafts are unique and independently editable", () => {
  const first = newSkill();
  const second = newSkill();
  assert.notEqual(first.id, second.id);
  assert.deepEqual(
    { name: first.name, when: first.when, instructions: first.instructions },
    { name: "", when: "", instructions: "" },
  );
  first.name = "只改这一份";
  assert.equal(second.name, "");
});
