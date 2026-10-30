// 知识库「DeepSeek 问答」入口（2026-10-29，v1.0 P2）。
// 不内嵌 chat.deepseek.com：外链打开；贴回公开分享链接后由后端取回问答、预览、归档。
// AI 整理只创建 new_note 提案，用户点采纳后才落盘。
import { $, api, escapeHtml, toast } from "./core.js";
import { renderMarkdownDocument } from "./pillar-markdown.js";
import { proposalsMarkup } from "./proposals-model.js";
import { isObsidianHost } from "./task-open.js";

const DEEPSEEK_URL = "https://chat.deepseek.com/";

const state = {
  open: false,
  preview: null,
  chapters: null,
  busy: false,
};
let initialized = false;

function el(id) { return document.getElementById(id); }

export function initShareLink() {
  if (initialized) return;
  initialized = true;
  el("share-link-close")?.addEventListener("click", closeShareLink);
  el("share-link-preview")?.addEventListener("click", () => previewShare());
  el("share-link-external")?.addEventListener("click", openDeepSeek);
  el("share-link-dialog")?.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeShareLink();
  });
  el("share-link-result")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-share-action]");
    if (!button) return;
    if (button.dataset.shareAction === "archive") archiveShare(button);
    if (button.dataset.shareAction === "digest") digestShare(button);
  });
  document.addEventListener("torch:chapters-changed", () => {
    state.chapters = null;
  });
  document.addEventListener("torch:proposals-changed", () => {
    if (state.open && state.preview) {
      setStatus("提案已处理；采纳的笔记已写入知识库。", "success");
      document.dispatchEvent(new CustomEvent("torch:notes-changed", {
        detail: { from: "share-link" },
      }));
    }
  });
}

/** 知识库首页的大按钮：直接打开 DeepSeek 网页版，不消耗 torch 的 Key。 */
export function openDeepSeekAsk() {
  openDeepSeek();
}

/**
 * options 可传字符串或 { url, preview }：
 * 知识库首页的“粘贴分享链接”会把 URL 预填进来；preview=true 时打开后直接取回。
 */
export function openShareLinkDialog(options = {}) {
  initShareLink();
  const config = typeof options === "string" ? { url: options } : (options || {});
  state.preview = null;
  state.busy = false;
  setStatus("");
  const result = el("share-link-result");
  if (result) result.innerHTML = `<p class="field-help">贴一个 chat.deepseek.com 的公开分享链接，先取回、再决定归档或让 AI 导师整理。</p>`;
  el("share-link-url").value = String(config.url || "");
  el("share-link-rounds").value = "";
  const dialog = el("share-link-dialog");
  if (dialog && !dialog.open) {
    try { dialog.showModal(); } catch { dialog.setAttribute("open", ""); }
  }
  state.open = true;
  if (config.url && config.preview) {
    window.setTimeout(() => { previewShare().catch(() => {}); }, 80);
  } else {
    window.setTimeout(() => el("share-link-url")?.focus?.(), 30);
  }
}

function closeShareLink() {
  const dialog = el("share-link-dialog");
  if (dialog?.open) dialog.close();
  else dialog?.removeAttribute("open");
  state.open = false;
}

function setStatus(message, tone = "") {
  const node = el("share-link-status");
  if (!node) return;
  node.textContent = message;
  node.className = `status-message ${tone}`.trim();
}

function openDeepSeek() {
  if (isObsidianHost() && globalThis.parent && globalThis.parent !== globalThis) {
    try {
      globalThis.parent.postMessage({ type: "torch:open-url", url: DEEPSEEK_URL }, "*");
      return;
    } catch { /* fall through */ }
  }
  window.open(DEEPSEEK_URL, "_blank", "noopener,noreferrer");
}

async function loadChapters() {
  if (!state.chapters) {
    state.chapters = await api("/api/writing/chapters");
  }
  return state.chapters?.knowledge || [];
}

async function previewShare() {
  if (state.busy) return;
  const url = String(el("share-link-url")?.value || "").trim();
  if (!url) {
    setStatus("先粘贴分享链接。", "warning");
    return;
  }
  state.busy = true;
  const button = el("share-link-preview");
  if (button) button.disabled = true;
  setStatus("正在取回分享…");
  try {
    const result = await api("/api/knowledge/share/preview", {
      method: "POST",
      body: JSON.stringify({ url, rounds: String(el("share-link-rounds")?.value || "").trim() }),
      timeoutMs: 120000,
    });
    state.preview = result;
    await renderPreview(result);
    setStatus(result.too_long
      ? "这篇分享很长；建议只取需要的轮次后再归档。"
      : "已取回；预览确认后再归档。", result.too_long ? "warning" : "success");
  } catch (error) {
    setStatus(`取回失败：${error.message}`, "error");
  } finally {
    state.busy = false;
    if (button) button.disabled = false;
  }
}

async function renderPreview(result) {
  const chapters = await loadChapters().catch(() => []);
  const options = chapters.map((chapter) =>
    `<option value="${escapeHtml(chapter.key)}"${chapter.key === result.suggested_chapter ? " selected" : ""}>${escapeHtml(chapter.title)}</option>`
  ).join("");
  const doc = renderMarkdownDocument(String(result.markdown || ""));
  const host = el("share-link-result");
  if (!host) return;
  host.innerHTML = `
    <section class="share-preview">
      <header class="share-preview-head">
        <div>
          <p class="eyebrow">SHARE PREVIEW</p>
          <h3>${escapeHtml(result.title || "DeepSeek 分享")}</h3>
          <p class="field-help">${Number(result.turns) || 0} 轮问答 · ${Number(result.chars_markdown) || 0} 字 · ${escapeHtml(result.share_url || "")}</p>
        </div>
        <a class="text-button" href="${escapeHtml(result.share_url || DEEPSEEK_URL)}" target="_blank" rel="noopener noreferrer">打开原分享 ↗</a>
      </header>
      <div class="share-preview-meta">
        <label>归档到 <select id="share-link-chapter">${options}</select></label>
        <label>标题 <input id="share-link-title" type="text" maxlength="200" value="${escapeHtml(result.title || "")}" /></label>
      </div>
      <div class="md-body share-preview-body">${doc.html || "<p class='field-help'>没有可预览的正文。</p>"}</div>
      <div class="share-preview-actions">
        <button type="button" class="button primary" data-share-action="archive">直接归档</button>
        <button type="button" class="button secondary" data-share-action="digest">让学术导师整理</button>
        <span class="field-help">直接归档不调用 AI；AI 整理会生成一条待采纳提案。</span>
      </div>
      <div id="share-link-proposal" class="share-preview-proposal"></div>
    </section>`;
}

async function archiveShare(button) {
  if (!state.preview || state.busy) return;
  state.busy = true;
  button.disabled = true;
  setStatus("正在归档…");
  try {
    const chapter = el("share-link-chapter")?.value || state.preview.suggested_chapter;
    const title = String(el("share-link-title")?.value || "").trim() || state.preview.title;
    const result = await api("/api/knowledge/share/archive", {
      method: "POST",
      body: JSON.stringify({
        url: state.preview.share_url, rounds: String(el("share-link-rounds")?.value || "").trim(),
        chapter, title, confirm: true,
      }),
      timeoutMs: 120000,
    });
    setStatus(`已归档到 ${chapter}：${result.note?.path || ""}`, "success");
    toast("分享已归档到知识库。", "success");
    document.dispatchEvent(new CustomEvent("torch:notes-changed", {
      detail: { path: result.note?.path, chapter, pillar: "knowledge" },
    }));
  } catch (error) {
    setStatus(`归档失败：${error.message}`, "error");
  } finally {
    state.busy = false;
    button.disabled = false;
  }
}

async function digestShare(button) {
  if (!state.preview || state.busy) return;
  state.busy = true;
  button.disabled = true;
  setStatus("学术导师正在整理…");
  try {
    const chapter = el("share-link-chapter")?.value || state.preview.suggested_chapter;
    const result = await api("/api/knowledge/share/digest", {
      method: "POST",
      body: JSON.stringify({
        url: state.preview.share_url,
        rounds: String(el("share-link-rounds")?.value || "").trim(),
        chapter,
      }),
      timeoutMs: 180000,
    });
    const host = el("share-link-proposal");
    if (host) host.innerHTML = proposalsMarkup([result.proposal]);
    setStatus("整理完成；请采纳下面的提案才会写入知识库。", "success");
  } catch (error) {
    setStatus(`整理失败：${error.message}`, "error");
  } finally {
    state.busy = false;
    button.disabled = false;
  }
}
