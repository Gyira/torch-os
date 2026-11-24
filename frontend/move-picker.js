import { api, escapeHtml } from "./core.js";
import { buildMoveFolders } from "./move-folders.js";

// Return a destination only after explicit confirmation. The caller owns the file operation.
export async function chooseMoveFolder(items, { confirmLabel = "移动到这里", heading = "移动文档" } = {}) {
  const catalog = await api("/api/writing/chapters?for_move=true");
  const roots = buildMoveFolders(catalog);
  const el = (id) => document.getElementById(`note-move-${id}`);
  const dialog = el("dialog");
  if (dialog.open) return null;
  const trail = [];
  let leaf = null;
  const sourceFolders = new Set(items.filter((item) => item.id)
    .map((item) => item.id.split("/").slice(0, -1).join("/")));
  const same = (node) => sourceFolders.size === 1 && sourceFolders.has(node?.path);
  const source = el("source");
  el("title").textContent = heading;
  source.textContent = items.length === 1 ? items[0].title : `已选 ${items.length} 篇文档`;
  if (sourceFolders.size === 1) source.textContent += ` · ${[...sourceFolders][0].replaceAll("/", " › ")}`;
  el("submit").textContent = confirmLabel;

  function render() {
    const parent = trail.at(-1);
    const selected = leaf || parent;
    const crumbs = leaf ? [...trail, leaf] : trail;
    el("trail").innerHTML = ["位置", ...crumbs.map((node) => node.title)].map((name, depth) =>
      `<button type="button" data-depth="${depth}" ${depth === crumbs.length ? 'aria-current="location"' : ""}>${escapeHtml(name)}</button>`
    ).join('<span aria-hidden="true">›</span>');
    el("level").textContent = !parent ? "选择区域" : trail.length === 1 ? "选择本子" : "子本子";
    el("grid").innerHTML = (parent?.children || roots).map((node, index) =>
      `<button type="button" class="move-folder${node === leaf ? " is-selected" : ""}" data-folder="${index}" ${node.path && !node.children.length ? `aria-pressed="${node === leaf}"` : ""}>
        <span class="move-folder-icon" aria-hidden="true"></span><span class="move-folder-name">${escapeHtml(node.title)}</span>
        ${same(node) ? '<small>原位置</small>' : node.children.length ? '<span class="move-forward" aria-hidden="true">›</span>' : ""}
      </button>`
    ).join("");
    el("submit").disabled = !selected?.path || same(selected);
    el("status").textContent = same(selected) ? "文档已在这里。" :
      !parent ? "" : !parent.children.length ? "这里还没有可选的本子。" :
      items.some((item) => item.chapter === "journal") ? "移走日记后，原日期将不再显示这篇日记。" : "";
  }

  return new Promise((resolve) => {
    const listeners = new AbortController();
    const on = (id, event, fn) => el(id).addEventListener(event, fn, { signal: listeners.signal });
    function finish(destination = null) {
      listeners.abort();
      if (dialog.open) dialog.close();
      resolve(destination);
    }
    on("grid", "click", (event) => {
      const button = event.target.closest("[data-folder]");
      if (!button) return;
      const node = (trail.at(-1)?.children || roots)[Number(button.dataset.folder)];
      leaf = null;
      if (node.children.length || !node.path) trail.push(node);
      else leaf = node;
      render();
      if (leaf) el("grid").querySelector(".is-selected")?.focus();
      else el("level").focus();
    });
    on("trail", "click", (event) => {
      const button = event.target.closest("[data-depth]");
      if (!button || button.hasAttribute("aria-current")) return;
      trail.splice(Number(button.dataset.depth));
      leaf = null;
      render();
      el("level").focus();
    });
    on("form", "submit", (event) => {
      event.preventDefault();
      if (el("submit").disabled) return;
      finish(leaf || trail.at(-1));
    });
    for (const id of ["close", "cancel"]) on(id, "click", () => finish());
    on("dialog", "cancel", (event) => { event.preventDefault(); finish(); });
    on("dialog", "close", () => { if (!dialog.open) finish(); });
    render();
    dialog.showModal();
    el("grid").querySelector("button")?.focus();
  });
}
