// 任务回收站：列出 02_生活运营/00_待办事项/回收站 里的任务文件与清单文件夹；
// 可恢复到原位置，也可彻底删除。
import { $, api, escapeHtml, formatDate, jsonBody, setStatus, toast, confirmAction, changed, state } from "./core.js";
import { trashItemSummary, trashDidaIds } from "./tasks-model.js";

let items = [];
let selected = new Set();
let initialized = false;

function sizeText(bytes) {
  const value = Math.max(0, Number(bytes) || 0);
  if (value < 1024) return `${value} B`;
  if (value < 1048576) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1048576).toFixed(1)} MB`;
}

function whenText(value) {
  if (!value) return "时间未知";
  try { return formatDate(value); } catch { return value; }
}

function pruneSelection() {
  const available = new Set(items.map((item) => item.trash_path));
  selected = new Set([...selected].filter((path) => available.has(path)));
}

function render() {
  const panel = $("#time-trash-list");
  const count = $("#time-trash-count");
  if (count) count.textContent = items.length ? `${items.length} 个已删除条目` : "回收站是空的";
  if (!panel) return;
  if (!items.length) {
    selected.clear();
    panel.innerHTML = '<p class="empty-state">回收站是空的。删除的任务或清单文件夹会先出现在这里，可以恢复，也可以彻底删除。</p>';
    return;
  }
  pruneSelection();
  const allSelected = selected.size === items.length;
  panel.innerHTML = `
    <div class="growth-actions trash-toolbar">
      <label class="check-label"><input type="checkbox" data-trash-select-all ${allSelected ? "checked" : ""} />全选</label>
      <button type="button" class="text-button" data-trash-restore-selected ${selected.size ? "" : "disabled"}>恢复选中</button>
      <button type="button" class="text-button" data-trash-delete-selected ${selected.size ? "" : "disabled"}>彻底删除选中</button>
      <button type="button" id="time-trash-empty" class="text-button">清空回收站</button>
      <span class="field-help">恢复会放回原清单或“已完成”；删除清单文件夹会连同它的已完成任务一起恢复。</span>
    </div>
    <ul class="history-list">${items.map((item) => {
      const summary = trashItemSummary(item);
      return `
      <li>
        <div class="trash-item-main">
          <input type="checkbox" data-trash-select="${escapeHtml(item.trash_path)}" ${selected.has(item.trash_path) ? "checked" : ""} aria-label="选择 ${escapeHtml(summary.label)}" />
          <div>
            <strong>${escapeHtml(summary.label)}</strong>
            <span>${escapeHtml(summary.meta)} · ${escapeHtml(whenText(item.deleted_at))} · ${escapeHtml(sizeText(item.bytes))}${item.done ? " · 已完成" : ""}${summary.kind === "folder" ? " · 文件夹" : ""}</span>
            <span class="meta">${escapeHtml(item.original_path || item.trash_path)}</span>
          </div>
        </div>
        <div class="growth-actions"><button type="button" class="text-button" data-trash-restore="${escapeHtml(item.trash_path)}">恢复</button><button type="button" class="text-button" data-trash-delete="${escapeHtml(item.trash_path)}">彻底删除</button></div>
      </li>`;
    }).join("")}</ul>`;
}

export async function loadTrash() {
  const panel = $("#time-trash-list");
  if (!panel) return;
  setStatus("#time-trash-status", "正在读取回收站…");
  try {
    const result = await api("/api/time/trash");
    items = Array.isArray(result.items) ? result.items : [];
    pruneSelection();
    render();
    setStatus("#time-trash-status", "");
  } catch (error) {
    items = [];
    selected.clear();
    panel.innerHTML = `<p class="empty-state">回收站读取失败：${escapeHtml(error.message)}</p>`;
    setStatus("#time-trash-status", "", "error");
  }
}

/** 滴答侧如果也删除了这些任务，恢复时一并把滴答回收站里的任务恢复回来。 */
async function restoreDida(targets) {
  const ids = trashDidaIds(targets);
  if (!ids.length || state.config?.capabilities?.dida_sync !== true) return "";
  try {
    const result = await api("/api/sync/dida/restore", jsonBody("POST", {
      external_ids: ids,
    }));
    if (result.errors?.length) {
      const detail = result.errors[0]?.message || result.errors[0]?.code || "unknown";
      return `滴答侧未能恢复：${detail}`;
    }
    return Number(result.restored) > 0 ? "已同步恢复滴答侧任务。" : "";
  } catch (error) {
    return `滴答侧恢复失败：${error.message}`;
  }
}

async function restore(paths) {
  if (!paths.length) return;
  const targets = items.filter((item) => paths.includes(item.trash_path));
  setStatus("#time-trash-status", "正在恢复…");
  try {
    const result = await api("/api/time/trash/restore", jsonBody("POST", { paths }));
    if (!result.restored) {
      const reason = result.skipped?.[0]?.reason || result.errors?.[0]?.reason || "未知原因";
      setStatus("#time-trash-status", `未能恢复：${reason}`, "error");
      return;
    }
    const remoteRequest = restoreDida(targets);
    selected.clear();
    await loadTrash();
    changed();
    document.dispatchEvent(new CustomEvent("torch:tasks-changed"));
    const local = `已恢复 ${Number(result.restored) || 0} 个条目。`;
    const lists = Number(result.lists_restored) > 0 ? `并恢复 ${result.lists_restored} 个清单定义。` : "";
    const remote = await remoteRequest;
    toast(`${local}${lists}${remote}`, remote.includes("未能") || remote.includes("失败") ? "warning" : "success");
    setStatus("#time-trash-status", "");
  } catch (error) {
    setStatus("#time-trash-status", error.message, "error");
  }
}

async function purge(paths) {
  const targets = items.filter((item) => paths.includes(item.trash_path));
  const label = paths.length > 1
    ? `选中的 ${paths.length} 个条目`
    : targets[0]?.title ? `“${targets[0].title}”` : "选中的条目";
  const accepted = await confirmAction({
    title: paths.length ? "彻底删除回收站条目" : "清空回收站",
    message: paths.length
      ? `会真正删除 ${label} 的文件/文件夹，无法从回收站找回。`
      : "会真正删除回收站里所有任务文件和文件夹，无法恢复。",
    confirmLabel: paths.length ? "彻底删除" : "清空回收站",
    danger: true,
  });
  if (!accepted) return;
  setStatus("#time-trash-status", "正在删除…");
  try {
    const body = paths.length ? { paths } : { all: true };
    const result = await api("/api/time/trash/delete", jsonBody("POST", body));
    selected.clear();
    toast(`已彻底删除 ${Number(result.deleted) || 0} 个条目。`, "success");
    await loadTrash();
  } catch (error) {
    setStatus("#time-trash-status", error.message, "error");
  }
}

export function initTrashPage() {
  if (initialized) return;
  initialized = true;
  $("#time-trash-refresh")?.addEventListener("click", loadTrash);
  document.addEventListener("change", (event) => {
    const selectAll = event.target.closest("[data-trash-select-all]");
    if (selectAll) {
      selected = selectAll.checked ? new Set(items.map((item) => item.trash_path)) : new Set();
      render();
      return;
    }
    const box = event.target.closest("[data-trash-select]");
    if (box) {
      if (box.checked) selected.add(box.dataset.trashSelect);
      else selected.delete(box.dataset.trashSelect);
      render();
    }
  });
  document.addEventListener("click", (event) => {
    if (event.target.closest("[data-trash-restore-selected]")) {
      restore([...selected]);
      return;
    }
    if (event.target.closest("[data-trash-delete-selected]")) {
      purge([...selected]);
      return;
    }
    const restoreButton = event.target.closest("[data-trash-restore]");
    if (restoreButton) {
      restore([restoreButton.dataset.trashRestore]);
      return;
    }
    const button = event.target.closest("[data-trash-delete]");
    if (button) {
      purge([button.dataset.trashDelete]);
      return;
    }
    if (event.target.closest("#time-trash-empty")) {
      purge([]);
    }
  });
  document.addEventListener("torch:tasks-changed", () => {
    // 回收中心和任务回收站共用同一批文件：即使回收站页暂时被「回收中心」盖住，
    // 只要它仍被选中就重读，切回待办事项时不会显示旧条目。
    const selected = $("#time-tab-trash")?.getAttribute("aria-selected") === "true";
    if (selected) loadTrash();
  });
}
