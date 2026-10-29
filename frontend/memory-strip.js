// 工作台忆灵条（2026-10-29，v1.0 P3）：替代旧的每日提问卡，显示待审卡/上次整理。
import { $, escapeHtml } from "./core.js";

let initialized = false;

export function initMemoryStrip() {
  if (initialized) return;
  initialized = true;
  $("#workbench-memory")?.addEventListener("click", () => {
    location.hash = "#self";
    window.setTimeout(() => {
      document.getElementById("self-hub")?.scrollIntoView?.({ block: "start", behavior: "smooth" });
    }, 120);
  });
}

export async function loadMemoryStrip() {
  const host = $("#workbench-memory");
  if (!host) return;
  try {
    const overview = await fetch("/api/memory-hub/overview", {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    }).then((response) => response.ok ? response.json() : null);
    if (!overview) {
      host.hidden = true;
      return;
    }
    const cards = Array.isArray(overview.cards) ? overview.cards.length : 0;
    const patches = Array.isArray(overview.identity_patches) ? overview.identity_patches.length : 0;
    const events = Number(overview.stats?.pending_events) || 0;
    const portrait = overview.portrait?.portrait;
    host.innerHTML = `<button type="button" class="memory-strip-inner" data-memory-open>
      <span class="eyebrow">忆灵</span>
      <strong>${cards + patches} 张待审</strong>
      <span class="memory-strip-detail">${portrait ? "画像已更新" : "还没有画像"} · ${events} 条新事件</span>
      <span class="memory-strip-go">去自我档案 ›</span>
    </button>`;
    host.hidden = false;
  } catch {
    host.hidden = true;
  }
}
