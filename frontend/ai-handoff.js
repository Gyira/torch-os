import { api, toast } from "./core.js";

let generation = 0;
export async function openAIHandoff(payload) {
  let dialog = document.getElementById("ai-handoff-dialog");
  if (dialog?.open) { dialog.querySelector("textarea")?.focus(); return; }
  const version = ++generation;
  const trigger = document.activeElement;
  if (!dialog) {
    dialog = document.createElement("dialog");
    dialog.id = "ai-handoff-dialog";
    dialog.className = "share-link-dialog context-pack-dialog";
    dialog.setAttribute("aria-labelledby", "ai-handoff-heading");
    document.body.append(dialog);
  }
  const controller = new AbortController();
  dialog.innerHTML = `<header class="note-editor-head"><div><h2 id="ai-handoff-heading">到 ChatGPT 继续聊</h2><p>先核对背景，再复制到你习惯的对话工具。</p></div><button type="button" data-handoff-close class="button secondary">关闭</button></header>
    <div class="context-pack-body"><p class="field-help">这里只在本机准备内容，不调用模型、不自动上传、不写入记忆。图片附件未包含。</p>
    <label><input type="checkbox" data-handoff-history ${payload.thread_id ? "" : "disabled"} /> 附带本角色的近期对话节选</label>
    <p data-handoff-status role="status">正在准备背景…</p>
    <label for="ai-handoff-text">本次准备复制的内容</label><textarea id="ai-handoff-text" class="ai-handoff-text" rows="15" readonly></textarea>
    <p class="field-help">聊完后，可把分享链接或有价值的正文带回 Torch 知识库。AI 建议与个人事实仍需区分。</p></div>
    <footer class="context-pack-actions"><button type="button" data-handoff-retry class="button secondary">重新准备</button><a href="https://chatgpt.com/" target="_blank" rel="noopener noreferrer" class="button secondary">打开 ChatGPT ↗</a><button type="button" data-handoff-copy class="button primary" disabled>复制背景与问题</button></footer>`;
  const input = dialog.querySelector("textarea"), status = dialog.querySelector("[data-handoff-status]");
  const copy = dialog.querySelector("[data-handoff-copy]");
  let request = 0;
  const load = async () => {
    const token = ++request;
    copy.disabled = true; input.value = ""; status.textContent = "正在准备背景…";
    try {
      const data = await api("/api/ai/handoff", { method: "POST", signal: controller.signal,
        body: JSON.stringify({ ...payload, include_history: dialog.querySelector("[data-handoff-history]").checked }) });
      if (version !== generation || token !== request || !dialog.open) return;
      input.value = data.text; copy.disabled = false;
      status.textContent = `${data.sources.length} 项资料 · ${data.chars} 字${data.truncated ? " · 部分资料已节选" : ""}${data.history_included ? " · 含近期对话" : " · 未附带历史"}。请检查后再复制。`;
    } catch (error) { if (version === generation && token === request && dialog.open && error.name !== "AbortError") status.textContent = error.message; }
  };
  dialog.querySelector("[data-handoff-close]").onclick = () => dialog.close();
  dialog.querySelector("[data-handoff-history]").onchange = load;
  dialog.querySelector("[data-handoff-retry]").onclick = load;
  copy.onclick = async () => {
    try { await navigator.clipboard.writeText(input.value); toast("已复制，粘贴到 ChatGPT 即可继续聊"); }
    catch { input.focus(); input.select(); status.textContent = "剪贴板不可用，已选中内容，请手动复制。"; }
  };
  dialog.onclose = () => { if (dialog.open) return; controller.abort(); if (version === generation) generation += 1; if (trigger?.isConnected) trigger.focus({ preventScroll: true }); };
  dialog.showModal();
  await load();
}
