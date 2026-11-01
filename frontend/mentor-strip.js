// 工作台 · 人生导师常驻条（2026-11-01）：今日三问 / 待确认 / 画像时间。
// 只读计数，不调用模型；点击进入自我档案 → 人生导师。
import { api, escapeHtml } from "./core.js";

const state = { pending: 0, questions: 0, portraitUpdated: "", loaded: false };

function strip() { return document.getElementById("workbench-mentor"); }

function render() {
  const host = strip();
  if (!host) return;
  const pending = Number(state.pending) || 0;
  const questions = Number(state.questions) || 0;
  const updated = String(state.portraitUpdated || "").slice(0, 10);
  const hint = questions
    ? `今天还有 ${questions} 个问题，愿意答就答，不愿意可以跳过。`
    : pending
      ? `${pending} 条档案修改等你确认。`
      : "今天没有问题；可以随时找导师聊聊。";
  host.innerHTML = `<div class="mentor-strip-inner">
    <span class="mentor-strip-mark" aria-hidden="true">导</span>
    <div class="mentor-strip-copy">
      <p class="eyebrow">LIFE MENTOR · 人生导师</p>
      <p>${escapeHtml(hint)}</p>
    </div>
    <div class="mentor-strip-meta">
      ${questions ? `<span class="mentor-strip-chip">今日三问 ${questions}</span>` : ""}
      ${pending ? `<span class="mentor-strip-chip is-warn">待确认 ${pending}</span>` : ""}
      ${updated ? `<span class="meta">画像 ${escapeHtml(updated)}</span>` : ""}
    </div>
    <button type="button" class="text-button" data-mentor-strip-open>去看看 ›</button>
  </div>`;
  host.hidden = false;
}

export async function loadMentorStrip() {
  try {
    const overview = await api("/api/self/overview", { timeoutMs: 30000 });
    state.pending = Number(overview.pending_count) || 0;
    state.questions = Number(overview.questions?.remaining) || 0;
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
    const open = event.target.closest?.("[data-mentor-strip-open]");
    if (!open) return;
    document.dispatchEvent(new CustomEvent("torch:open-self-page", {
      detail: {
        page: "mentor",
        sub: state.questions ? "questions" : state.pending ? "pending" : "chat",
      },
    }));
  });
  document.addEventListener("torch:self-badges", (event) => {
    const detail = event.detail || {};
    state.pending = Number(detail.pending) || 0;
    state.questions = Number(detail.questions) || 0;
    state.portraitUpdated = detail.portraitUpdated || state.portraitUpdated;
    render();
  });
  document.addEventListener("torch:mentor-pending-changed", () => {
    loadMentorStrip().catch(() => {});
  });
  loadMentorStrip().catch(() => {});
}
