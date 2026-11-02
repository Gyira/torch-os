// AI 角色管理页的纯数据层（2026-11-01）。
// DOM、网络与提示词写入都不在这里，方便 node --test 直接覆盖筛选、校验与脏检查。

export const MAX_SKILLS = 6;

export const FIELD_LIMITS = Object.freeze({
  name: 40,
  tagline: 120,
  identity: 4000,
  style: 2000,
  skillName: 40,
  skillWhen: 200,
  skillInstructions: 4000,
});

export const ROLE_LAYERS = Object.freeze([
  Object.freeze({ id: "identity", label: "自我", mark: "身" }),
  Object.freeze({ id: "life", label: "生活", mark: "生" }),
  Object.freeze({ id: "knowledge", label: "知识", mark: "知" }),
  Object.freeze({ id: "ops", label: "运营", mark: "营" }),
  Object.freeze({ id: "love", label: "关系", mark: "爱" }),
  Object.freeze({ id: "health", label: "健康", mark: "健" }),
  Object.freeze({ id: "finance", label: "财务", mark: "财" }),
]);

export const STYLE_PRESETS = Object.freeze([
  Object.freeze({
    id: "execution",
    name: "干脆执行",
    summary: "短、准、先做",
    text: "说话简短、坚定、直率。先给结论和下一步，再点出风险；信息够了就推进，不绕弯子。",
  }),
  Object.freeze({
    id: "analysis",
    name: "严谨分析",
    summary: "判断、依据、不确定处",
    text: "说话克制、沉稳、讲依据。先给判断，再列关键依据，最后明确哪里还不确定。",
  }),
  Object.freeze({
    id: "companion",
    name: "温和陪伴",
    summary: "先接住，再梳理",
    text: "说话温和、细腻、有分寸。先接住情绪和处境，再梳理现实约束，最后只给一个小建议。",
  }),
  Object.freeze({
    id: "creative",
    name: "发散创意",
    summary: "几个方向，再收敛",
    text: "说话轻松、鲜活、有画面。先给几个有区别的方向，再推荐一个值得试的，最后说下一步。",
  }),
]);

const LAYER_LABELS = Object.fromEntries(ROLE_LAYERS.map((item) => [item.id, item.label]));
let skillSequence = 0;

// Fixed paths come from backend role_policy; this map only translates them for
// display. Unknown paths fall back to the raw value rather than hiding scope.
export const ROOT_LABELS = Object.freeze({
  "00_人生路": "人生路",
  "01_自我档案": "自我档案",
  "02_生活运营": "生活运营",
  "03_知识库": "知识库",
  "02_生活运营/01_恋爱系统": "恋爱系统",
  "02_生活运营/02_健康系统": "健康系统",
  "02_生活运营/03_财务系统": "财务系统",
  "00_人生路/04_目标/04_习惯追踪": "习惯追踪",
  "01_自我档案/01_档案": "自我档案条目",
  "01_自我档案/02_人生导师/记忆": "导师记忆",
  "01_自我档案/02_人生导师/提问": "每日提问",
});
export const ACTION_LABELS = Object.freeze({
  new_note: "新建笔记",
  archive_note: "移动笔记",
  new_task: "新建待办",
  new_habit: "新建习惯",
  health_report: "保存健康报告",
  finance_report: "保存财务报告",
});

export function scopeLabel(value) {
  const key = String(value || "").trim();
  if (!key) return "无";
  return ROOT_LABELS[key] || key;
}

export function policyReadLabels(policy) {
  const roots = Array.isArray(policy?.read_roots) ? policy.read_roots : [];
  return roots.map(scopeLabel);
}

export function policyWriteLabels(policy) {
  const roots = Array.isArray(policy?.write_roots) ? policy.write_roots : [];
  return roots.map(scopeLabel);
}

export function policyActionLabels(policy) {
  const actions = Array.isArray(policy?.actions) ? policy.actions : [];
  return actions.map((value) => ACTION_LABELS[value] || value);
}

export function layerLabel(layer) {
  const key = String(layer || "");
  return LAYER_LABELS[key] || key || "通用";
}

export function layerMark(layer) {
  const key = String(layer || "");
  return ROLE_LAYERS.find((item) => item.id === key)?.mark || "AI";
}

export function roleInitial(name) {
  const text = String(name || "").trim();
  if (!text) return "AI";
  return Array.from(text).slice(0, 1).join("") || "AI";
}

export function skillCountLabel(count) {
  const value = Math.max(0, Number(count) || 0);
  return value ? `${value} 个 Skill` : "还没有 Skill";
}

export function totalSkillCount(items) {
  return (Array.isArray(items) ? items : []).reduce(
    (sum, item) => sum + Math.max(0, Number(item?.custom?.skills) || 0), 0);
}

export function customProfileCount(items) {
  return (Array.isArray(items) ? items : []).filter((item) => {
    const custom = item?.custom || {};
    return Boolean(custom.identity) || Boolean(custom.style) || Number(custom.skills) > 0;
  }).length;
}

export function newSkill(id = "") {
  skillSequence += 1;
  return {
    id: String(id || `draft_${Date.now().toString(36)}_${skillSequence}`),
    name: "",
    when: "",
    instructions: "",
  };
}

export function profileSkills(profile) {
  return Array.isArray(profile?.skills) ? profile.skills : [];
}

function searchableText(role) {
  const modes = (Array.isArray(role?.modes) ? role.modes : [])
    .map((mode) => `${mode?.id || ""} ${mode?.label || ""}`).join(" ");
  const skills = (Array.isArray(role?.skill_names) ? role.skill_names : []).join(" ");
  return [
    role?.id, role?.name, role?.tagline, role?.layer, layerLabel(role?.layer),
    modes, skills,
  ].join(" ").toLocaleLowerCase("zh-CN");
}

export function filterRoles(items, query = "", layer = "") {
  const list = Array.isArray(items) ? items : [];
  const layerKey = String(layer || "").trim();
  const tokens = String(query || "").trim().toLocaleLowerCase("zh-CN")
    .split(/\s+/).filter(Boolean);
  return list.filter((role) => {
    if (layerKey && String(role?.layer || "") !== layerKey) return false;
    if (!tokens.length) return true;
    const haystack = searchableText(role);
    return tokens.every((token) => haystack.includes(token));
  });
}

export function validateProfile(profile) {
  const fail = (message, field) => ({ ok: false, message, field });
  const name = String(profile?.name ?? "").trim();
  if (!name) return fail("名字不能为空。", "name");
  if (name.length > FIELD_LIMITS.name) return fail(`名字不能超过 ${FIELD_LIMITS.name} 字。`, "name");
  if (/[\r\n\t]/.test(name)) return fail("名字不能包含换行或制表符。", "name");
  if (String(profile?.tagline ?? "").trim().length > FIELD_LIMITS.tagline) {
    return fail(`一句话定位不能超过 ${FIELD_LIMITS.tagline} 字。`, "tagline");
  }
  if (String(profile?.identity ?? "").trim().length > FIELD_LIMITS.identity) {
    return fail(`身份设定不能超过 ${FIELD_LIMITS.identity} 字。`, "identity");
  }
  if (String(profile?.style ?? "").trim().length > FIELD_LIMITS.style) {
    return fail(`说话风格不能超过 ${FIELD_LIMITS.style} 字。`, "style");
  }
  const skills = profileSkills(profile);
  if (skills.length > MAX_SKILLS) return fail(`最多只能有 ${MAX_SKILLS} 个专属 Skill。`, "skills");
  const names = new Set();
  for (let index = 0; index < skills.length; index += 1) {
    const skill = skills[index] || {};
    const label = `第 ${index + 1} 个 Skill`;
    const skillName = String(skill.name ?? "").trim();
    if (!skillName) return fail(`${label}还没有名字。`, `skill:${index}:name`);
    if (skillName.length > FIELD_LIMITS.skillName) {
      return fail(`${label}的名字不能超过 ${FIELD_LIMITS.skillName} 字。`, `skill:${index}:name`);
    }
    if (/[\r\n\t]/.test(skillName)) {
      return fail(`${label}的名字不能包含换行或制表符。`, `skill:${index}:name`);
    }
    const folded = skillName.toLocaleLowerCase("zh-CN");
    if (names.has(folded)) return fail(`Skill 名称重复：${skillName}`, `skill:${index}:name`);
    names.add(folded);
    if (String(skill.when ?? "").trim().length > FIELD_LIMITS.skillWhen) {
      return fail(`${label}的使用时机不能超过 ${FIELD_LIMITS.skillWhen} 字。`, `skill:${index}:when`);
    }
    if (!String(skill.instructions ?? "").trim()) {
      return fail(`${label}还没有写怎么做。`, `skill:${index}:instructions`);
    }
    if (String(skill.instructions ?? "").trim().length > FIELD_LIMITS.skillInstructions) {
      return fail(`${label}的做法不能超过 ${FIELD_LIMITS.skillInstructions} 字。`, `skill:${index}:instructions`);
    }
  }
  return { ok: true, message: "", field: "" };
}

export function profilePayload(profile) {
  return {
    name: String(profile?.name ?? "").trim(),
    tagline: String(profile?.tagline ?? "").trim(),
    identity: String(profile?.identity ?? "").trim(),
    style: String(profile?.style ?? "").trim(),
    skills: profileSkills(profile).map((skill) => ({
      id: String(skill?.id ?? ""),
      name: String(skill?.name ?? "").trim(),
      when: String(skill?.when ?? "").trim(),
      instructions: String(skill?.instructions ?? "").trim(),
    })),
  };
}

export function profileSignature(profile) {
  const payload = profilePayload(profile);
  return JSON.stringify(payload);
}

export function isProfileDirty(profile, baseline) {
  if (!profile || !baseline) return false;
  return profileSignature(profile) !== profileSignature(baseline);
}

export function preferredPreviewMode(profile) {
  const modes = (Array.isArray(profile?.modes) ? profile.modes : [])
    .map((mode) => String(mode?.id || "")).filter(Boolean);
  for (const preferred of ["converse", "advice", "discuss", "tutor", "report"]) {
    if (modes.includes(preferred)) return preferred;
  }
  return modes[0] || "";
}

export function previewLabel(profile) {
  const mode = preferredPreviewMode(profile);
  const label = (Array.isArray(profile?.modes) ? profile.modes : [])
    .find((item) => item?.id === mode)?.label;
  return label ? `试聊（${label}）` : "试聊一句";
}
