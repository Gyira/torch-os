// 工作台 · 人生导师常驻条（2026-11-01）：今日三问 / 待确认 / 画像时间。
// 只读计数，不调用模型；点击进入自我档案 → 人生导师。
import { api, escapeHtml } from "./core.js";

const state = { pending: 0, portraitUpdated: "", loaded: false };

function strip() { return document.getElementById("workbench-mentor"); }

function render() {
  const host = strip();
  if (!host) return;
  const pending = Number(state.pending) || 0;
  const updated = String(state.portraitUpdated || "").slice(0, 10);
  const hint = pending ? `${pending} 条档案修改等你确认。` : "查看当前画像，记录或修正对自己的认识。";
  host.innerHTML = `<div class="mentor-strip-inner">
    <span class="mentor-strip-mark" aria-hidden="true">我</span>
    <div class="mentor-strip-copy">
      <p class="eyebrow">自我档案</p>
      <p>${escapeHtml(hint)}</p>
    </div>
    <div class="mentor-strip-meta">
      ${pending ? `<span class="mentor-strip-chip is-warn">待确认 ${pending}</span>` : ""}
      ${updated ? `<span class="meta">画像 ${escapeHtml(updated)}</span>` : ""}
    </div>
    <span class="text-button mentor-strip-open" aria-hidden="true">去看看 ›</span>
  </div>`;
  host.hidden = false;
  host.setAttribute("aria-busy", "false");
  host.dataset.mentorStripOpen = "";
  host.setAttribute("role", "button");
  host.setAttribute("tabindex", "0");
  // Use the visible copy as the accessible name, including the daily counts.
  host.removeAttribute("aria-label");
}

function openMentor() {
  document.dispatchEvent(new CustomEvent("torch:open-self-page", {
    detail: {
      page: state.pending ? "mentor" : "archive",
      sub: state.pending ? "pending" : "overview",
    },
  }));
}

export async function loadMentorStrip() {
  try {
    const overview = await api("/api/self/overview", { timeoutMs: 30000 });
    state.pending = Number(overview.pending_count) || 0;
    state.portraitUpdated = overview.portrait?.updated_at || "";
    render();
  } catch {
    const host = strip();
    if (host) host.hidden = true;
  }
}

export function initMentorStrip() {
  if (state.loaded) return;
  state.loaded = true;
  document.addEventListener("click", (event) => {
    if (!event.target.closest?.("[data-mentor-strip-open]")) return;
    openMentor();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    if (!event.target.closest?.("[data-mentor-strip-open]")) return;
    event.preventDefault();
    openMentor();
  });
  document.addEventListener("torch:self-badges", (event) => {
    const detail = event.detail || {};
    state.pending = Number(detail.pending) || 0;
    state.portraitUpdated = detail.portraitUpdated || state.portraitUpdated;
    render();
  });
  document.addEventListener("torch:mentor-pending-changed", () => {
    loadMentorStrip().catch(() => {});
  });
  loadMentorStrip().catch(() => {});
}
