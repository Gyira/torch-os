import { api, escapeHtml, openDialog, confirmAction, toast, state } from "./core.js";
import { openPopover } from "./tasks-picker.js";
import { canDeleteNotebook, buildMoveTargetGroups } from "./notebook-model.js";

export function libraryTools(kind, id, title, view, chapter = "") {
  if (!["life", "knowledge"].includes(view)) return "";
  return `<button type="button" class="library-more" data-library-kind="${kind}" data-library-id="${escapeHtml(id)}" data-library-title="${escapeHtml(title)}" data-library-view="${view}" data-library-chapter="${escapeHtml(chapter)}" aria-label="管理${escapeHtml(title)}" aria-haspopup="dialog">•••</button>`;
}

export function initLibraryFiles({ books, refresh }) {
  let selection = null, busy = false;
  const picked = new Map();
  const rowFor = (node) => node?.closest(".library-book-wrap, .note-card-wrap");
  const entry = (node) => {
    const data = node.dataset;
    return { kind: data.libraryKind, id: data.libraryId, title: data.libraryTitle,
      view: data.libraryView, chapter: data.libraryChapter };
  };
  const visible = () => [...document.querySelectorAll(`#${selection?.view}-view [data-library-kind="${selection?.kind}"]`)];
  function paint() {
    const focusedAction = document.activeElement?.dataset?.selection;
    document.querySelector(".library-selection-bar")?.remove();
    document.querySelectorAll(".library-selected").forEach((row) => row.classList.remove("library-selected"));
    document.querySelectorAll("[data-library-selectable]").forEach((button) => { button.removeAttribute("aria-pressed"); delete button.dataset.librarySelectable; });
    if (!selection) return;
    for (const node of visible()) {
      const selected = picked.has(node.dataset.libraryId);
      rowFor(node)?.classList.toggle("library-selected", selected);
      const button = rowFor(node)?.querySelector(".note-card, .notebook-card");
      if (button) { button.setAttribute("aria-pressed", String(selected)); button.dataset.librarySelectable = "true"; }
    }
    const bar = document.createElement("div");
    bar.className = "library-selection-bar";
    bar.setAttribute("role", "region");
    bar.setAttribute("aria-label", "文件多选操作");
    bar.innerHTML = `<strong role="status">已选 ${picked.size} ${selection.kind === "book" ? "本" : "篇"}</strong>
      <button type="button" data-selection="all">全选当前页面</button>
      <button type="button" data-selection="title" ${picked.size !== 1 ? "disabled" : ""}>更改标题</button>
      ${selection.kind === "note" ? `<button type="button" data-selection="move" ${!picked.size ? "disabled" : ""}>移动</button>` : ""}
      <button type="button" data-selection="delete" ${!picked.size ? "disabled" : ""}>删除</button>
      <button type="button" data-selection="done">完成</button>`;
    if (busy) bar.querySelectorAll("button").forEach((button) => { button.disabled = true; });
    bar.addEventListener("click", (event) => {
      const action = event.target.dataset.selection;
      if (!action || busy) return;
      if (action === "done") { selection = null; picked.clear(); paint(); }
      else if (action === "all") { visible().forEach((node) => picked.set(node.dataset.libraryId, entry(node))); paint(); }
      else run(action, [...picked.values()]);
    });
    document.querySelector(`#${selection.view}-view`)?.prepend(bar);
    if (focusedAction) bar.querySelector(`[data-selection="${focusedAction}"]:not(:disabled)`)?.focus({ preventScroll: true });
  }
  function select(item) {
    if (!selection || selection.kind !== item.kind || selection.view !== item.view) {
      picked.clear(); selection = { kind: item.kind, view: item.view };
    }
    if (picked.has(item.id)) picked.delete(item.id); else picked.set(item.id, item);
    paint();
  }
  function menu(anchor, item) {
    const group = picked.has(item.id) && selection?.kind === item.kind && selection?.view === item.view
      ? [...picked.values()] : [item];
    openPopover(anchor, (layer, close) => {
      for (const [action, label] of [["select", "多选"], ["title", "更改标题"], ...(item.kind === "note" ? [["move", "移动到其他本子"]] : []), ["delete", "移入回收中心"]]) {
        const button = document.createElement("button");
        button.type = "button"; button.className = "time-popover-row"; button.textContent = label;
        button.dataset.libraryAction = action;
        if (action === "title" && group.length > 1) { button.disabled = true; button.textContent = "请选择一项更改标题"; }
        if (action === "delete" && item.kind === "book" && !canDeleteNotebook(item.view, books(item.view).find((book) => book.key === item.id))) {
          button.disabled = true; button.textContent = "默认本子不能删除";
        }
        button.addEventListener("click", () => { close(); if (action === "select") select(item); else run(action, group); });
        layer.append(button);
      }
    });
  }
  function ask(title, body) {
    return new Promise((resolve) => {
      const dialog = document.querySelector("#detail-dialog");
      openDialog(title, `<form id="library-action-form">${body}<p class="field-help" id="library-form-status"></p><div class="library-form-actions"><button type="button" data-library-cancel>取消</button><button type="submit" class="button primary">确定</button></div></form>`);
      const form = dialog.querySelector("form");
      let settled = false;
      const done = (value) => { if (settled) return; settled = true; dialog.removeEventListener("close", cancel); resolve(value); };
      const cancel = () => done(null);
      dialog.addEventListener("close", cancel, { once: true });
      form.querySelector("[data-library-cancel]").onclick = () => { done(null); dialog.close(); };
      form.onsubmit = (event) => { event.preventDefault(); const value = String(new FormData(form).get("value") || "").trim(); if (!value) { form.querySelector("#library-form-status").textContent = "请填写或选择内容。"; return; } done(value); dialog.close(); };
      form.querySelector("input, select")?.focus();
    });
  }
  async function run(action, items) {
    if (busy || !items.length) return;
    busy = true; paint();
    let completed = 0;
    const failures = [];
    try {
      // Capture revisions before confirmation: edits in Obsidian during the dialog must conflict.
      const snapshots = new Map();
      for (const item of items) if (item.kind === "note") {
        try { snapshots.set(item.id, await api(`/api/notes?path=${encodeURIComponent(item.id)}`)); }
        catch (error) { snapshots.set(item.id, error); }
      }
      let value = "";
      if (action === "title") {
        value = await ask("更改标题", `<label>标题<input name="value" required maxlength="${items[0].kind === "book" ? 80 : 200}" value="${escapeHtml(items[0].title)}"></label><p class="field-help">更改显示标题，保留文件路径和已有链接。</p>`);
        if (value === null) return;
      }
      if (action === "move") {
        const payload = await api("/api/writing/chapters");
        const chapters = payload[items[0].view] || [];
        const groups = buildMoveTargetGroups(items[0].view, chapters);
        value = await ask(`移动 ${items.length} 篇文档`, `<label>目标本子 / 章节<select name="value" required><option value="">请选择</option>${groups.map((group) => `<optgroup label="${escapeHtml(books(items[0].view).find((book) => book.key === group.key)?.title || group.title)}">${group.chapters.map((chapter) => `<option value="${escapeHtml(chapter.key)}">${escapeHtml(chapter.title)}</option>`).join("")}</optgroup>`).join("")}</select></label><p class="field-help">文件会实际移入目标文件夹。移动日记后，它将离开原日期的日记位置。</p>`);
        if (value === null) return;
      }
      if (action === "delete" && !(await confirmAction({ title: `删除 ${items.length} ${items[0].kind === "book" ? "个本子" : "篇文档"}？`, message: "所选内容会移入回收中心。本子中的文档和附件会一起保留，可以恢复。", confirmLabel: "移入回收中心", danger: true }))) return;
      for (const item of items) {
        try {
          let url, body;
          if (item.kind === "book") {
            url = action === "title" ? "/api/notebooks/title" : "/api/notebooks/delete";
            body = { pillar: item.view, key: item.id, ...(action === "title" ? { title: value } : { confirm: true }) };
          } else {
            const snapshot = snapshots.get(item.id);
            if (snapshot instanceof Error) throw snapshot;
            if (action === "move" && snapshot.chapter === value) { picked.delete(item.id); completed++; continue; }
            url = "/api/library/notes/action";
            body = { action, path: item.id, expected_revision: snapshot.revision, ...(action === "title" ? { title: value } : action === "move" ? { target_chapter: value } : {}) };
          }
          await api(url, { method: "POST", body: JSON.stringify(body) });
          picked.delete(item.id); completed++;
        } catch (error) { failures.push(`${item.title}：${error.message}`); }
      }
      if (completed) {
        document.dispatchEvent(new CustomEvent("data-changed"));
        await refresh(items[0].view);
      }
      if (failures.length) {
        openDialog("部分项目未完成", `<p>已完成 ${completed} 项，未完成 ${failures.length} 项。</p><ul>${failures.map((message) => `<li>${escapeHtml(message)}</li>`).join("")}</ul>`);
      } else toast(`已${{ title: "更新标题", move: "移动", delete: "移入回收中心" }[action]} · ${completed} 项`, "success");
    } catch (error) { toast(error.message, "error"); }
    finally { busy = false; paint(); }
  }
  document.addEventListener("contextmenu", (event) => {
    const button = rowFor(event.target)?.querySelector("[data-library-kind]");
    if (!button) return;
    event.preventDefault(); if (!busy) menu(button, entry(button));
  });
  document.addEventListener("click", (event) => {
    const button = rowFor(event.target)?.querySelector("[data-library-kind]");
    if (!button) return;
    const item = entry(button);
    const explicit = event.target.closest("[data-library-kind]");
    const selecting = selection?.view === item.view && selection?.kind === item.kind;
    if (explicit || selecting || event.ctrlKey || event.metaKey) {
      event.preventDefault(); event.stopImmediatePropagation();
      if (busy) return;
      if (explicit) menu(button, item); else select(item);
    }
  }, true);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && selection && !busy && !document.querySelector("dialog[open]")) { selection = null; picked.clear(); paint(); }
  });
  document.addEventListener("torch:library-rendered", () => {
    if (selection && (state.view !== selection.view || !visible().some((node) => picked.has(node.dataset.libraryId)))) {
      selection = null; picked.clear();
    }
    paint();
  });
}
