// AI 角色管理页（2026-11-01）。
// 一页管理所有角色：身份、说话风格、专属 Skill；写入由后端做校验与冲突保护。
// 本文件只碰 DOM 与接口，纯筛选/校验逻辑在 role-admin-model.js。
import { $, api, confirmAction, escapeHtml, setStatus, toast } from "./core.js";
import { openRoleDrawer } from "./roles.js";
import {
  MAX_SKILLS, FIELD_LIMITS, ROLE_LAYERS, STYLE_PRESETS,
  customProfileCount, filterRoles, isProfileDirty, layerLabel,
  newSkill, preferredPreviewMode, profilePayload, profileSkills, roleInitial,
  skillCountLabel, totalSkillCount, validateProfile,
} from "./role-admin-model.js";

const PROPOSAL_LABELS = {
  identity_patch: "身份补丁",
  review_card: "回看卡",
  new_note: "新笔记",
  archive_note: "归档笔记",
  new_task: "新任务",
  new_habit: "新习惯",
  health_report: "健康报告",
  finance_report: "财务报告",
  life_advice: "生活建议",
  hub_event: "导师事件",
};

const state = {
  items: [],
  registryErrors: [],
  aiStatus: null,
  selected: "",
  profile: null,
  baseline: null,
  query: "",
  layer: "",
  loading: false,
  saving: false,
  loadToken: 0,
  initialized: false,
};

function clone(value) {
  if (typeof structuredClone === "function") return structuredClone(value);
  return JSON.parse(JSON.stringify(value));
}

function el(id) {
  return document.getElementById(id);
}

function dirty() {
  return isProfileDirty(state.profile, state.baseline);
}

function modeLabels(profile) {
  return (Array.isArray(profile?.modes) ? profile.modes : [])
    .map((mode) => String(mode.label || mode.id || "")).filter(Boolean);
}

function skillNameList(profile) {
  return profileSkills(profile).map((skill) => String(skill.name || "未命名 Skill"));
}

function proposalLabel(value) {
  return PROPOSAL_LABELS[value] || value;
}

export function initRoleAdmin() {
  if (state.initialized) return;
  state.initialized = true;
  const search = el("role-admin-search");
  search?.addEventListener("input", () => {
    state.query = search.value || "";
    renderList();
  });
  el("role-admin-filters")?.addEventListener("click", (event) => {
    const button = event.target.closest?.("[data-role-layer]");
    if (!button) return;
    state.layer = button.dataset.roleLayer || "";
    renderFilters();
    renderList();
  });
  el("role-admin-list")?.addEventListener("click", (event) => {
    const clear = event.target.closest?.("[data-role-clear-filters]");
    if (clear) {
      state.query = "";
      state.layer = "";
      if (search) search.value = "";
      renderFilters();
      renderList();
      search?.focus();
      return;
    }
    const button = event.target.closest?.("[data-role-select]");
    if (!button) return;
    void selectRole(button.dataset.roleSelect || "");
  });
  el("role-admin-editor")?.addEventListener("input", onEditorInput);
  el("role-admin-editor")?.addEventListener("click", onEditorClick);
  el("role-admin-editor")?.addEventListener("submit", onEditorSubmit);
  window.addEventListener("beforeunload", (event) => {
    if (!dirty()) return;
    event.preventDefault();
    event.returnValue = "";
  });
}

export async function loadRoleAdmin() {
  if (!el("roles-view")) return;
  initRoleAdmin();
  if (state.loading) return;
  // 用户正在编辑时不刷新，避免把未保存的内容悄悄换掉。
  if (state.profile && dirty()) return;
  state.loading = true;
  setStatus("#role-admin-list-status", "正在读取角色…");
  try {
    const [manifest, aiStatus] = await Promise.all([
      api("/api/ai/roles"),
      api("/api/ai/status").catch(() => null),
    ]);
    state.items = Array.isArray(manifest?.items) ? manifest.items : [];
    state.registryErrors = Array.isArray(manifest?.errors) ? manifest.errors : [];
    state.aiStatus = aiStatus;
    state.loading = false;
    renderReadiness();
    renderFilters();
    renderList();
    renderRegistryErrors();
    if (!state.items.some((item) => item.id === state.selected)) {
      state.selected = state.items[0]?.id || "";
      state.profile = null;
      state.baseline = null;
    }
    if (!state.selected) {
      renderNoRoles();
      setStatus("#role-admin-list-status", state.registryErrors.length
        ? "没有可用角色。先修复下面列出的角色文件。" : "角色目录里还没有角色卡。");
      return;
    }
    setStatus("#role-admin-list-status", "");
    if (!state.profile || state.profile.id !== state.selected) {
      await loadProfile(state.selected);
    } else {
      renderEditor();
      renderList();
    }
  } catch (error) {
    state.loading = false;
    setStatus("#role-admin-list-status", `读取角色失败：${error.message}`, "error");
    renderEditorError("读取角色列表失败", error.message, "roles");
  }
}

async function selectRole(roleId) {
  const next = String(roleId || "").trim();
  if (!next || next === state.selected) return;
  if (!(await confirmDiscardChanges())) return;
  state.selected = next;
  state.profile = null;
  state.baseline = null;
  renderList();
  await loadProfile(next, { force: true });
}

async function loadProfile(roleId, { force = false } = {}) {
  if (!roleId) return;
  if (!force && state.profile?.id === roleId && !dirty()) {
    renderEditor();
    return;
  }
  const token = ++state.loadToken;
  renderEditorLoading();
  try {
    const profile = await api(`/api/ai/roles/${encodeURIComponent(roleId)}/profile`);
    if (token !== state.loadToken) return;
    state.selected = roleId;
    state.profile = clone(profile);
    state.profile.skills = profileSkills(profile);
    state.baseline = clone(state.profile);
    renderEditor();
    renderList();
  } catch (error) {
    if (token !== state.loadToken) return;
    renderEditorError("这个角色暂时打不开", error.message, "profile");
  }
}

function renderReadiness() {
  const node = el("role-admin-readiness");
  if (!node) return;
  const status = state.aiStatus;
  const available = status?.available === true;
  node.dataset.tone = available ? "ok" : "warn";
  const reason = String(status?.reason || "还没有读取到 AI 状态。").trim();
  node.innerHTML = `<span class="role-admin-readiness-dot" aria-hidden="true"></span>
    <div><strong>${available ? "AI 已就绪" : "AI 未就绪，角色卡仍可编辑"}</strong>
    <p>${available
      ? "保存后可以点“试聊”验证它，不会自动发送任何消息。"
      : escapeHtml(reason)}</p></div>`;
}

function renderFilters() {
  const host = el("role-admin-filters");
  if (!host) return;
  const counts = new Map();
  for (const item of state.items) {
    const key = String(item.layer || "");
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  const layers = ROLE_LAYERS.filter((layer) => counts.has(layer.id));
  if (state.layer && !counts.has(state.layer)) state.layer = "";
  host.innerHTML = [
    `<button type="button" class="role-admin-filter${state.layer ? "" : " is-active"}" data-role-layer="" aria-pressed="${state.layer ? "false" : "true"}">全部<span>${state.items.length}</span></button>`,
    ...layers.map((layer) => {
      const active = state.layer === layer.id;
      return `<button type="button" class="role-admin-filter${active ? " is-active" : ""}" data-role-layer="${escapeHtml(layer.id)}" aria-pressed="${active ? "true" : "false"}">${escapeHtml(layer.label)}<span>${counts.get(layer.id) || 0}</span></button>`;
    }),
  ].join("");
}

function renderList() {
  const host = el("role-admin-list");
  if (!host) return;
  const filtered = filterRoles(state.items, state.query, state.layer);
  const count = el("role-admin-count");
  if (count) {
    count.textContent = state.query || state.layer
      ? `筛选出 ${filtered.length} / ${state.items.length} 位角色`
      : `${state.items.length} 位角色 · ${totalSkillCount(state.items)} 个专属 Skill`;
  }
  const stat = el("role-admin-hero-stats");
  if (stat) {
    stat.textContent = `${state.items.length} 位角色 · ${customProfileCount(state.items)} 位已自定义 · ${totalSkillCount(state.items)} 个 Skill`;
  }
  if (!filtered.length) {
    host.innerHTML = `<div class="role-admin-list-empty">
      <p>没有匹配的角色。</p>
      <button type="button" class="text-button" data-role-clear-filters>清除筛选</button>
    </div>`;
    return;
  }
  host.innerHTML = filtered.map((item) => {
    const selected = item.id === state.selected;
    const skillCount = Math.max(0, Number(item.custom?.skills) || 0);
    const modes = Array.isArray(item.modes) ? item.modes.length : 0;
    const meta = [modes ? `${modes} 种模式` : "", skillCount ? skillCountLabel(skillCount) : ""]
      .filter(Boolean).join(" · ");
    const tagline = String(item.tagline || "").trim();
    return `<button type="button" class="role-admin-card${selected ? " is-active" : ""}" data-role-select="${escapeHtml(item.id)}" aria-pressed="${selected ? "true" : "false"}">
      <span class="role-admin-avatar" data-layer="${escapeHtml(item.layer || "")}" aria-hidden="true">${escapeHtml(roleInitial(item.name))}</span>
      <span class="role-admin-card-copy">
        <span class="role-admin-card-top"><strong>${escapeHtml(item.name || item.id)}</strong><span class="role-admin-layer">${escapeHtml(layerLabel(item.layer))}</span></span>
        <span class="role-admin-card-tagline">${tagline ? escapeHtml(tagline) : "还没有一句话定位"}</span>
        <span class="role-admin-card-meta">${escapeHtml(meta || "尚未自定义")}${item.custom?.identity ? " · 已定身份" : ""}${item.custom?.style ? " · 已定风格" : ""}</span>
      </span>
      <span class="role-admin-card-arrow" aria-hidden="true">›</span>
    </button>`;
  }).join("");
}

function renderRegistryErrors() {
  const node = el("role-admin-warning");
  if (!node) return;
  if (!state.registryErrors.length) {
    node.hidden = true;
    node.innerHTML = "";
    return;
  }
  node.hidden = false;
  node.innerHTML = `<strong>有 ${state.registryErrors.length} 个角色文件没有加载</strong>
    <ul>${state.registryErrors.map((item) => `<li><code>${escapeHtml(item.file)}</code>：${escapeHtml(item.error)}</li>`).join("")}</ul>
    <p>修好文件后重新进入本页，或者重启本地服务。坏文件不会被角色使用。</p>`;
}

function renderNoRoles() {
  const editor = el("role-admin-editor");
  if (!editor) return;
  editor.innerHTML = `<div class="role-admin-empty">
    <span class="eyebrow">NO ROLE</span>
    <h2>没有可管理的角色</h2>
    <p>检查 <code>dsh/roles</code> 目录。每个角色是一张 Markdown 角色卡，文件名不能以 <code>_</code> 开头。</p>
  </div>`;
}

function renderEditorLoading() {
  const editor = el("role-admin-editor");
  if (!editor) return;
  editor.innerHTML = `<div class="role-admin-skeleton" aria-label="正在读取角色卡">
    <div class="role-admin-skeleton-head"></div>
    <div class="role-admin-skeleton-block"></div>
    <div class="role-admin-skeleton-block is-short"></div>
    <div class="role-admin-skeleton-block"></div>
  </div>`;
}

function renderEditorError(title, message, retry) {
  const editor = el("role-admin-editor");
  if (!editor) return;
  editor.innerHTML = `<div class="role-admin-empty is-error" role="alert">
    <span class="eyebrow">ROLE ERROR</span>
    <h2>${escapeHtml(title)}</h2>
    <p>${escapeHtml(message || "没有拿到角色卡内容。")}</p>
    <button type="button" class="button secondary" data-role-retry="${escapeHtml(retry)}">重新读取</button>
  </div>`;
}

function renderEditor() {
  const editor = el("role-admin-editor");
  if (!editor || !state.profile) return;
  const profile = state.profile;
  const modes = modeLabels(profile);
  const entries = Array.isArray(profile.entry) ? profile.entry : [];
  const proposals = Array.isArray(profile.proposals) ? profile.proposals : [];
  const isDirty = dirty();
  const stylePresetActive = STYLE_PRESETS.find((preset) => preset.text === String(profile.style || "").trim());
  const preview = preferredPreviewMode(profile);
  const skills = profileSkills(profile);
  const custom = {
    identity: Boolean(profile.identity),
    style: Boolean(profile.style),
    skills: skills.length,
  };
  editor.innerHTML = `<form id="role-admin-form" class="role-admin-form" novalidate>
    <header class="role-admin-editor-head">
      <div class="role-admin-editor-identity">
        <span id="role-admin-editor-avatar" class="role-admin-avatar role-admin-avatar-lg" data-layer="${escapeHtml(profile.layer || "")}" aria-hidden="true">${escapeHtml(roleInitial(profile.name))}</span>
        <div class="role-admin-editor-heading">
          <p class="eyebrow">ROLE PROFILE · ${escapeHtml(layerLabel(profile.layer))}</p>
          <h2 id="role-admin-editor-title">${escapeHtml(profile.name || "未命名角色")}</h2>
          <p class="role-admin-editor-subtitle"><code>${escapeHtml(profile.id || "")}</code>${profile.file ? ` · ${escapeHtml(profile.file)}` : ""} · ${modes.length} 种模式</p>
        </div>
      </div>
      <div class="role-admin-head-actions">
        <span id="role-admin-dirty" class="role-admin-dirty"${isDirty ? "" : " hidden"}>未保存</span>
        <button type="submit" class="button primary" data-role-save="1" data-save-label="保存"${isDirty ? "" : " disabled"}>保存</button>
        <button type="button" class="button secondary role-admin-preview" data-role-preview="1"${isDirty || !preview ? " disabled" : ""} title="${isDirty ? "先保存修改，再用最新设定试聊" : "打开这个角色的对话抽屉"}">${escapeHtml(preview ? `试聊（${(profile.modes || []).find((item) => item.id === preview)?.label || "对话"}）` : "试聊一句")}</button>
      </div>
    </header>

    <div class="role-admin-editor-body">
      <section class="role-admin-section" aria-labelledby="role-admin-identity-heading">
        <div class="role-admin-section-head">
          <div><span class="eyebrow">IDENTITY</span><h3 id="role-admin-identity-heading">身份</h3><p>它是谁、站在什么位置、在意什么。会在模式提示之后追加；输出格式和只读边界不变。</p></div>
          <span class="role-admin-counter" data-counter="identity">0 / ${FIELD_LIMITS.identity}</span>
        </div>
        <div class="role-admin-two-col">
          <div class="field">
            <label for="role-admin-name">名字</label>
            <input id="role-admin-name" data-profile-field="name" value="${escapeHtml(profile.name || "")}" maxlength="${FIELD_LIMITS.name}" autocomplete="off" />
          </div>
          <div class="field">
            <label for="role-admin-tagline">一句话定位</label>
            <input id="role-admin-tagline" data-profile-field="tagline" value="${escapeHtml(profile.tagline || "")}" maxlength="${FIELD_LIMITS.tagline}" autocomplete="off" placeholder="例如：陪你把想法写清楚" />
          </div>
        </div>
        <div class="field">
          <label for="role-admin-identity">身份设定</label>
          <textarea id="role-admin-identity" data-profile-field="identity" rows="5" maxlength="${FIELD_LIMITS.identity}" placeholder="例如：你是我的长期写作伙伴。你熟悉我的表达习惯，但不替我下结论；先问清我想说什么，再给具体建议。">${escapeHtml(profile.identity || "")}</textarea>
          <p class="field-help">留空就沿用角色卡原有的身份。这里不需要重复语气和格式规则。</p>
        </div>
      </section>

      <section class="role-admin-section" aria-labelledby="role-admin-style-heading">
        <div class="role-admin-section-head">
          <div><span class="eyebrow">VOICE</span><h3 id="role-admin-style-heading">说话风格</h3><p>可以套用预设，也可以写自己的。保存后只影响这个角色。</p></div>
          <span class="role-admin-counter" data-counter="style">0 / ${FIELD_LIMITS.style}</span>
        </div>
        <div class="role-admin-style-presets" role="group" aria-label="说话风格预设">
          ${STYLE_PRESETS.map((preset) => `<button type="button" class="role-admin-style-preset${stylePresetActive?.id === preset.id ? " is-active" : ""}" data-style-preset="${escapeHtml(preset.id)}" aria-pressed="${stylePresetActive?.id === preset.id ? "true" : "false"}"><strong>${escapeHtml(preset.name)}</strong><span>${escapeHtml(preset.summary)}</span></button>`).join("")}
        </div>
        <div class="field">
          <label for="role-admin-style">风格说明</label>
          <textarea id="role-admin-style" data-profile-field="style" rows="4" maxlength="${FIELD_LIMITS.style}" placeholder="例如：短句，先说结论；不夸张，不堆术语；不同意时直接说理由。">${escapeHtml(profile.style || "")}</textarea>
          <p class="field-help">留空就沿用角色卡原有的语气。点预设会替换当前风格说明。</p>
        </div>
      </section>

      <section class="role-admin-section" aria-labelledby="role-admin-skills-heading">
        <div class="role-admin-section-head">
          <div><span class="eyebrow">SKILLS</span><h3 id="role-admin-skills-heading">专属 Skill</h3><p>教它一件只属于这个角色的事：什么时候用、按什么步骤做、不要做什么。</p></div>
          <button type="button" class="text-button" data-skill-add${skills.length >= MAX_SKILLS ? " disabled" : ""}>${skills.length >= MAX_SKILLS ? `最多 ${MAX_SKILLS} 个` : "添加 Skill"}</button>
        </div>
        <div class="role-admin-skills">${skills.length ? skills.map((skill, index) => skillMarkup(skill, index)).join("") : `<div class="role-admin-skill-empty"><p>还没有专属 Skill。</p><p>例如「周日复盘」：在周日晚上，用三个问题带我回看这一周；先列事实，再给一个下周动作。</p><button type="button" class="text-button" data-skill-add>添加第一个 Skill</button></div>`}</div>
        <p class="field-help">专属 Skill 只在本角色里生效；它不会获得文件或工具权限，仍然只读资料、只提议。</p>
      </section>

      <details class="role-admin-source">
        <summary>角色卡原有设定</summary>
        <div class="role-admin-source-grid">
          <div><span class="field-label">模式</span><div class="role-admin-chips">${modes.length ? modes.map((mode) => `<span class="role-admin-chip">${escapeHtml(mode)}</span>`).join("") : "<span class=\"meta\">未声明</span>"}</div></div>
          <div><span class="field-label">可用入口</span><div class="role-admin-chips">${entries.length ? entries.map((entry) => `<code class="role-admin-chip">${escapeHtml(entry)}</code>`).join("") : "<span class=\"meta\">无固定入口</span>"}</div></div>
          <div><span class="field-label">可提议</span><div class="role-admin-chips">${proposals.length ? proposals.map((item) => `<span class="role-admin-chip">${escapeHtml(proposalLabel(item))}</span>`).join("") : "<span class=\"meta\">只回答，不提议</span>"}</div></div>
          <div><span class="field-label">角色卡</span><p class="meta"><code>${escapeHtml(profile.file || `${profile.id}.md`)}</code> · 身份/风格 ${custom.identity || custom.style ? "已自定义" : "沿用原文"} · ${skillCountLabel(skills.length)}</p></div>
        </div>
        <p class="field-help">模式规则与提示词正文仍直接写在角色卡文件里；这里管理的是身份、风格和专属 Skill。改完保存后，下一轮对话生效。</p>
      </details>
    </div>

    <footer class="role-admin-footer">
      <p id="role-admin-feedback" class="status-message" role="status" aria-live="polite"></p>
      <div class="role-admin-footer-actions">
        <button type="button" class="text-button" data-role-reload="1">重新载入</button>
        <button type="button" class="button secondary" data-role-undo="1"${isDirty ? "" : " disabled"}>撤销修改</button>
        <button type="submit" class="button primary" data-role-save="1" data-save-label="保存角色"${isDirty && !state.saving ? "" : " disabled"}>${state.saving ? "正在保存…" : "保存角色"}</button>
      </div>
    </footer>
  </form>`;
  updateEditorChrome();
}

function skillMarkup(skill, index) {
  const safeId = String(skill.id || `skill_${index}`);
  return `<article class="role-admin-skill" data-skill-id="${escapeHtml(safeId)}" data-skill-index="${index}">
    <div class="role-admin-skill-head">
      <span class="role-admin-skill-index" aria-hidden="true">${String(index + 1).padStart(2, "0")}</span>
      <div class="field role-admin-skill-name">
        <label for="role-admin-skill-${index}-name">Skill 名称</label>
        <input id="role-admin-skill-${index}-name" data-skill-field="name" data-skill-index="${index}" value="${escapeHtml(skill.name || "")}" maxlength="${FIELD_LIMITS.skillName}" autocomplete="off" placeholder="例如：周回看" />
      </div>
      <button type="button" class="text-button role-admin-skill-remove" data-skill-remove="${index}">移除</button>
    </div>
    <div class="field">
      <label for="role-admin-skill-${index}-when">什么时候用</label>
      <input id="role-admin-skill-${index}-when" data-skill-field="when" data-skill-index="${index}" value="${escapeHtml(skill.when || "")}" maxlength="${FIELD_LIMITS.skillWhen}" autocomplete="off" placeholder="例如：周日晚上复盘，或我说“回看这一周”时" />
    </div>
    <div class="field">
      <label for="role-admin-skill-${index}-instructions">教它怎么做</label>
      <textarea id="role-admin-skill-${index}-instructions" data-skill-field="instructions" data-skill-index="${index}" rows="4" maxlength="${FIELD_LIMITS.skillInstructions}" placeholder="写步骤、判断标准和边界。例如：先列三件事实，再指出一个反复出现的模式，最后只给一个下周动作；不要替我评价好坏。">${escapeHtml(skill.instructions || "")}</textarea>
      <span class="role-admin-counter" data-counter="skill:${index}:instructions">0 / ${FIELD_LIMITS.skillInstructions}</span>
    </div>
  </article>`;
}

function updateEditorChrome() {
  if (!state.profile) return;
  const profile = state.profile;
  const isDirty = dirty();
  const title = el("role-admin-editor-title");
  if (title) title.textContent = String(profile.name || "").trim() || "未命名角色";
  const avatar = el("role-admin-editor-avatar");
  if (avatar) avatar.textContent = roleInitial(profile.name);
  const activeCard = document.querySelector(".role-admin-card.is-active");
  if (activeCard) {
    const cardName = activeCard.querySelector(".role-admin-card-top strong");
    if (cardName) cardName.textContent = String(profile.name || "").trim() || profile.id || "未命名角色";
    const cardTagline = activeCard.querySelector(".role-admin-card-tagline");
    if (cardTagline) {
      const tagline = String(profile.tagline || "").trim();
      cardTagline.textContent = tagline || "还没有一句话定位";
    }
    const cardAvatar = activeCard.querySelector(".role-admin-avatar");
    if (cardAvatar) cardAvatar.textContent = roleInitial(profile.name);
  }
  for (const button of document.querySelectorAll("[data-style-preset]")) {
    const preset = STYLE_PRESETS.find((item) => item.id === button.dataset.stylePreset);
    const active = Boolean(preset && String(profile.style || "").trim() === preset.text);
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
  }
  const badge = el("role-admin-dirty");
  if (badge) badge.hidden = !isDirty;
  const undo = document.querySelector("[data-role-undo]");
  if (undo) undo.disabled = !isDirty || state.saving;
  for (const save of document.querySelectorAll("[data-role-save]")) {
    save.disabled = state.saving || !isDirty;
    save.textContent = state.saving
      ? "正在保存…" : (save.dataset.saveLabel || "保存角色");
  }
  const preview = document.querySelector("[data-role-preview]");
  if (preview) {
    preview.disabled = isDirty || !preferredPreviewMode(profile) || state.saving;
    preview.title = isDirty ? "先保存修改，再用最新设定试聊" : "打开这个角色的对话抽屉";
  }
  const values = {
    identity: String(profile.identity || ""),
    style: String(profile.style || ""),
  };
  for (const node of document.querySelectorAll("[data-counter]")) {
    const key = node.dataset.counter || "";
    let value = "";
    if (key === "identity" || key === "style") value = values[key] || "";
    if (key.startsWith("skill:")) {
      const [, index, field] = key.split(":");
      value = String(profileSkills(profile)[Number(index)]?.[field] || "");
    }
    const limit = key === "style" ? FIELD_LIMITS.style
      : key === "identity" ? FIELD_LIMITS.identity
        : FIELD_LIMITS.skillInstructions;
    node.textContent = `${value.length} / ${limit}`;
    node.classList.toggle("is-near", value.length > limit * 0.9);
  }
  const form = el("role-admin-form");
  if (form) form.setAttribute("aria-busy", state.saving ? "true" : "false");
}

function onEditorInput(event) {
  if (!state.profile) return;
  if (event.target.matches?.("[aria-invalid='true']")) {
    event.target.removeAttribute("aria-invalid");
  }
  const field = event.target.closest?.("[data-profile-field]");
  if (field) {
    state.profile[field.dataset.profileField] = field.value;
    updateEditorChrome();
    return;
  }
  const skillField = event.target.closest?.("[data-skill-field]");
  if (!skillField) return;
  const index = Number(skillField.dataset.skillIndex);
  const skills = profileSkills(state.profile);
  if (!Number.isInteger(index) || index < 0 || index >= skills.length) return;
  skills[index][skillField.dataset.skillField] = skillField.value;
  updateEditorChrome();
}

function onEditorClick(event) {
  const target = event.target;
  if (target.closest?.("[data-role-retry]")) {
    const retry = target.closest("[data-role-retry]").dataset.roleRetry;
    if (retry === "profile" && state.selected) void loadProfile(state.selected, { force: true });
    else if (retry === "roles") void loadRoleAdminForce();
    return;
  }
  const style = target.closest?.("[data-style-preset]");
  if (style) {
    const preset = STYLE_PRESETS.find((item) => item.id === style.dataset.stylePreset);
    if (!preset || !state.profile) return;
    state.profile.style = preset.text;
    const field = el("role-admin-style");
    if (field) field.value = preset.text;
    document.querySelectorAll("[data-style-preset]").forEach((button) => {
      const active = button.dataset.stylePreset === preset.id;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    });
    updateEditorChrome();
    return;
  }
  if (target.closest?.("[data-skill-add]")) {
    if (!state.profile || profileSkills(state.profile).length >= MAX_SKILLS) return;
    const skill = newSkill();
    state.profile.skills.push(skill);
    renderEditor();
    window.requestAnimationFrame(() => {
      const input = document.querySelector(`[data-skill-id="${skill.id}"] [data-skill-field="name"]`);
      input?.focus();
    });
    return;
  }
  const remove = target.closest?.("[data-skill-remove]");
  if (remove) {
    if (!state.profile) return;
    const index = Number(remove.dataset.skillRemove);
    if (!Number.isInteger(index)) return;
    state.profile.skills.splice(index, 1);
    renderEditor();
    window.requestAnimationFrame(() => {
      document.querySelector("[data-skill-add]")?.focus();
    });
    return;
  }
  if (target.closest?.("[data-role-preview]")) {
    const profile = state.profile;
    if (!profile || dirty()) return;
    const mode = preferredPreviewMode(profile);
    if (!mode) {
      toast("这个角色没有可以试聊的模式。", "error");
      return;
    }
    void openRoleDrawer({
      roleId: profile.id,
      mode,
      title: profile.name,
      hint: "试聊会用刚保存的角色卡。它只提议，采纳后才写入。",
    });
    return;
  }
  if (target.closest?.("[data-role-undo]")) {
    void undoChanges();
    return;
  }
  if (target.closest?.("[data-role-reload]")) {
    void reloadProfile();
  }
}

function onEditorSubmit(event) {
  event.preventDefault();
  void saveProfile();
}

async function saveProfile() {
  if (!state.profile || state.saving || !state.selected) return;
  const validation = validateProfile(state.profile);
  if (!validation.ok) {
    setStatus("#role-admin-feedback", validation.message, "error");
    focusValidationField(validation.field);
    return;
  }
  state.saving = true;
  updateEditorChrome();
  setStatus("#role-admin-feedback", "正在保存角色卡…");
  try {
    const payload = {
      ...profilePayload(state.profile),
      expected_revision: state.profile.revision || "",
    };
    const result = await api(`/api/ai/roles/${encodeURIComponent(state.selected)}/profile`, {
      method: "PUT",
      body: JSON.stringify(payload),
      timeoutMs: 30000,
    });
    const profile = result?.profile;
    if (!profile?.id) throw new Error("服务没有返回保存后的角色卡。");
    state.profile = clone(profile);
    state.profile.skills = profileSkills(profile);
    state.baseline = clone(state.profile);
    state.saving = false;
    updateManifestAfterSave(profile);
    renderFilters();
    renderList();
    renderEditor();
    setStatus("#role-admin-feedback", "已保存。下一轮对话会用新设定。", "success");
    toast("角色卡已保存");
    document.dispatchEvent(new CustomEvent("torch:roles-changed"));
  } catch (error) {
    state.saving = false;
    updateEditorChrome();
    const prefix = error.status === 409
      ? "角色卡已在别处修改，你写的内容还在。点“重新载入”会覆盖它；想保留就先把内容复制出去。"
      : "保存失败：";
    setStatus("#role-admin-feedback", `${prefix}${error.message}`, "error");
  }
}

function updateManifestAfterSave(profile) {
  const item = state.items.find((entry) => entry.id === profile.id);
  if (!item) return;
  item.name = profile.name;
  item.tagline = profile.tagline;
  item.custom = {
    identity: Boolean(profile.identity),
    style: Boolean(profile.style),
    skills: profileSkills(profile).length,
  };
  item.skill_names = skillNameList(profile);
}

function focusValidationField(field) {
  document.querySelectorAll("[aria-invalid='true']").forEach((node) => {
    node.removeAttribute("aria-invalid");
  });
  let selector = "";
  if (field.startsWith("skill:")) {
    const [, index, name] = field.split(":");
    selector = `[data-skill-index="${index}"] [data-skill-field="${name}"]`;
  } else if (field) {
    selector = `[data-profile-field="${field}"]`;
  }
  const node = selector ? document.querySelector(selector) : null;
  if (node) {
    node.setAttribute("aria-invalid", "true");
    node.focus();
    node.scrollIntoView({ block: "center", behavior: "smooth" });
  }
  const feedback = el("role-admin-feedback");
  feedback?.scrollIntoView({ block: "nearest" });
}

async function undoChanges() {
  if (!state.profile || !dirty()) return;
  const ok = await confirmAction({
    title: "撤销未保存的修改？",
    message: "会回到上次读取到的角色卡内容；已经保存的修改不受影响。",
    confirmLabel: "撤销修改",
    cancelLabel: "继续编辑",
    danger: true,
  });
  if (!ok) return;
  state.profile = clone(state.baseline);
  state.profile.skills = profileSkills(state.profile);
  renderEditor();
  setStatus("#role-admin-feedback", "已撤销未保存的修改。", "success");
}

async function reloadProfile() {
  if (!state.selected) return;
  const ok = await confirmAction({
    title: "重新载入角色卡？",
    message: dirty()
      ? "未保存的身份、风格和 Skill 会丢失，回到磁盘上的最新版本。"
      : "会重新读取磁盘上的角色卡；不修改任何内容。",
    confirmLabel: "重新载入",
    cancelLabel: "取消",
    danger: dirty(),
  });
  if (!ok) return;
  await loadProfile(state.selected, { force: true });
}

async function confirmDiscardChanges() {
  if (!dirty()) return true;
  return confirmAction({
    title: "放弃未保存的角色修改？",
    message: "切换角色后，这次写的身份、风格和 Skill 不会保存。",
    confirmLabel: "放弃修改",
    cancelLabel: "继续编辑",
    danger: true,
  });
}

export async function confirmRoleAdminLeave() {
  return confirmDiscardChanges();
}

async function loadRoleAdminForce() {
  if (state.profile && dirty() && !(await confirmDiscardChanges())) return;
  state.profile = null;
  state.baseline = null;
  await loadRoleAdmin();
}
