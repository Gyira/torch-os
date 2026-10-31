// 回收中心（2026-10-31）：四库删除的笔记/日记与待办事项统一在这里恢复或彻底删除。
// 物理文件仍留在各域回收站；待办事项模块的回收站与这里查看的是同一批任务。
import {
  $, api, changed, confirmAction, escapeHtml, formatDate, jsonBody, setStatus, state, toast,
} from "./core.js";

const KIND_LABELS = {
  note: "笔记", journal: "日记", task: "任务", task_folder: "清单", notebook: "本子",
};

let items = new Map();
let initialized = false;
let busy = false;

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

function failureReason(result) {
  return result.skipped?.[0]?.reason || result.errors?.[0]?.reason || "未知原因";
}

function touchesTasks(paths) {
  return paths.some((path) => items.get(path)?.source === "tasks");
}

/** 与待办事项模块的回收站保持一致：本地恢复成功后，把滴答侧也删过的任务恢复回来。 */
async function restoreDida(paths) {
  const ids = new Set();
  for (const path of paths) {
    const item = items.get(path);
    if (!item || item.source !== "tasks") continue;
    if (item.dida_id) ids.add(item.dida_id);
    for (const id of item.dida_ids || []) ids.add(id);
  }
  if (!ids.size || state.config?.capabilities?.dida_sync !== true) {
    return { restored: 0, error: "" };
  }
  try {
    const result = await api("/api/sync/dida/restore", jsonBody("POST", {
      external_ids: [...ids],
    }));
    const failure = result.errors?.[0];
    return {
      restored: Number(result.restored) || 0,
      error: failure ? (failure.message || failure.code || "unknown") : "",
    };
  } catch (error) {
    return { restored: 0, error: error.message };
  }
}

function itemMarkup(item) {
  const kind = KIND_LABELS[item.kind] || "文档";
  const meta = [
    item.original_path ? `原位置 ${item.original_path}` : "原位置未知",
    item.deleted_at ? `删除于 ${whenText(item.deleted_at)}` : "",
    sizeText(item.bytes),
    item.kind === "task_folder" && Number(item.count) ? `${item.count} 个任务` : "",
    item.done ? "已完成" : "",
  ].filter(Boolean).join(" · ");
  return `<article class="recycle-item">
    <div class="recycle-item-main">
      <span class="recycle-kind">${escapeHtml(kind)}</span>
      <strong class="recycle-title">${escapeHtml(item.title || "未命名文档")}</strong>
      ${item.excerpt ? `<p class="recycle-excerpt">${escapeHtml(item.excerpt)}</p>` : ""}
      <p class="recycle-meta">${escapeHtml(meta)}</p>
    </div>
    <div class="recycle-actions">
      <button type="button" class="button secondary" data-recycle-restore="${escapeHtml(item.trash_path)}"${item.restorable ? "" : " disabled"}>恢复</button>
      <button type="button" class="small-button reject" data-recycle-purge="${escapeHtml(item.trash_path)}">彻底删除</button>
    </div>
  </article>`;
}

function render(payload) {
  const host = $("#recycle-groups");
  if (!host) return;
  items = new Map();
  for (const group of payload.groups || []) {
    for (const item of group.items || []) items.set(item.trash_path, item);
  }
  if (!(Number(payload.total) > 0)) {
    host.innerHTML = `<div class="recycle-empty">
      <p class="recycle-empty-title">回收中心是空的</p>
      <p class="field-help">四库删除的笔记、日记和任务会先出现在这里。恢复会回到原位置，彻底删除才会真正移除文件。</p>
    </div>`;
    return;
  }
  host.innerHTML = (payload.groups || []).map((group) => `
    <section class="recycle-group" data-pillar="${escapeHtml(group.key)}" aria-labelledby="recycle-group-${escapeHtml(group.key)}">
      <div class="recycle-group-head">
        <div><p class="eyebrow">${escapeHtml(group.title)}</p><h3 id="recycle-group-${escapeHtml(group.key)}">${escapeHtml(group.title)}</h3></div>
        <span class="meta">${Number(group.count) || 0} 项</span>
      </div>
      ${(group.items || []).length
        ? `<div class="recycle-list">${group.items.map(itemMarkup).join("")}</div>`
        : `<p class="recycle-group-empty">这一库没有删除的文档。</p>`}
    </section>`).join("");
}

export async function loadRecycleCenter() {
  const host = $("#recycle-groups");
  if (!host) return;
  setStatus("#recycle-status", "正在读取回收中心…");
  try {
    const payload = await api("/api/recycle");
    render(payload);
    setStatus("#recycle-status", "");
  } catch (error) {
    items = new Map();
    host.innerHTML = `<p class="empty-state">回收中心读取失败：${escapeHtml(error.message)}</p>`;
    setStatus("#recycle-status", "", "error");
  }
}

async function restore(paths) {
  if (!paths.length || busy) return;
  busy = true;
  setStatus("#recycle-status", "正在恢复…");
  try {
    const result = await api("/api/recycle/restore", jsonBody("POST", { paths }));
    const restored = Number(result.restored) || 0;
    const failed = (result.skipped?.length || 0) + (result.errors?.length || 0);
    if (!restored) {
      setStatus("#recycle-status", `未能恢复：${failureReason(result)}`, "error");
      return;
    }
    const lists = Number(result.lists_restored) > 0 ? `，并恢复 ${result.lists_restored} 个清单定义` : "";
    const remote = touchesTasks(paths) ? await restoreDida(paths) : { restored: 0, error: "" };
    const remoteNote = remote.restored > 0 ? "，已同步恢复滴答侧任务" : "";
    toast(`已恢复 ${restored} 项${lists}${remoteNote}。`, remote.error ? "warning" : "success");
    if (touchesTasks(paths)) document.dispatchEvent(new CustomEvent("torch:tasks-changed"));
    changed();
    await loadRecycleCenter();
    if (failed) {
      setStatus("#recycle-status", `已恢复 ${restored} 项，${failed} 项未恢复：${failureReason(result)}`, "warning");
    } else if (remote.error) {
      setStatus("#recycle-status", `滴答侧恢复失败：${remote.error}`, "warning");
    }
  } catch (error) {
    setStatus("#recycle-status", error.message, "error");
  } finally {
    busy = false;
  }
}

async function purge(paths) {
  if (!paths.length || busy) return;
  busy = true;
  try {
    const target = paths.length > 1
      ? `选中的 ${paths.length} 个文档`
      : `《${items.get(paths[0])?.title || "未命名文档"}》`;
    const accepted = await confirmAction({
      title: paths.length > 1 ? "彻底删除选中文档" : "彻底删除这份文档？",
      message: `${target}会从回收站真正删除，无法恢复。`,
      confirmLabel: "彻底删除", danger: true,
    });
    if (!accepted) return;
    setStatus("#recycle-status", "正在彻底删除…");
    const result = await api("/api/recycle/purge", jsonBody("POST", { paths }));
    const deleted = Number(result.deleted) || 0;
    const failed = (result.skipped?.length || 0) + (result.errors?.length || 0);
    if (!deleted) {
      setStatus("#recycle-status", `未能删除：${failureReason(result)}`, "error");
      return;
    }
    toast(`已彻底删除 ${deleted} 项。`, "success");
    if (touchesTasks(paths)) document.dispatchEvent(new CustomEvent("torch:tasks-changed"));
    changed();
    await loadRecycleCenter();
    if (failed) {
      setStatus("#recycle-status", `已删除 ${deleted} 项，${failed} 项未删除：${failureReason(result)}`, "warning");
    }
  } catch (error) {
    setStatus("#recycle-status", error.message, "error");
  } finally {
    busy = false;
  }
}

export function initRecycleCenter() {
  if (initialized) return;
  initialized = true;
  $("#recycle-refresh")?.addEventListener("click", loadRecycleCenter);
  document.addEventListener("click", (event) => {
    const restoreButton = event.target.closest?.("[data-recycle-restore]");
    if (restoreButton) {
      restore([restoreButton.dataset.recycleRestore]);
      return;
    }
    const purgeButton = event.target.closest?.("[data-recycle-purge]");
    if (purgeButton) purge([purgeButton.dataset.recyclePurge]);
  });
}
