// 任务回收站：列出 .torch-os/task-trash 里的任务笔记，只有“彻底删除”才删除 md 文件。
import { $, api, escapeHtml, formatDate, jsonBody, setStatus, toast, confirmAction } from "./core.js";

let items = [];
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

function render() {
  const panel = $("#time-trash-list");
  const count = $("#time-trash-count");
  if (count) count.textContent = items.length ? `${items.length} 个已删除任务` : "没有已删除任务";
  if (!panel) return;
  if (!items.length) {
    panel.innerHTML = '<p class="empty-state">回收站是空的。删除的任务会先出现在这里，不会立刻删掉文件。</p>';
    return;
  }
  panel.innerHTML = `${items.length ? '<div class="growth-actions"><button type="button" id="time-trash-empty" class="text-button">清空回收站</button><span class="field-help">清空会真正删除下面所有任务笔记。</span></div>' : ""}<ul class="history-list">${items.map((item) => `
    <li>
      <div>
        <strong>${escapeHtml(item.title || "未命名任务")}</strong>
        <span>${escapeHtml(item.list_name || "收集箱")} · ${escapeHtml(whenText(item.deleted_at))} · ${escapeHtml(sizeText(item.bytes))}${item.done ? " · 已完成" : ""}</span>
        <span class="meta">${escapeHtml(item.original_path || item.trash_path)}</span>
      </div>
      <button type="button" class="text-button" data-trash-delete="${escapeHtml(item.trash_path)}">彻底删除</button>
    </li>`).join("")}</ul>`;
}

export async function loadTrash() {
  const panel = $("#time-trash-list");
  if (!panel) return;
  setStatus("#time-trash-status", "正在读取回收站…");
  try {
    const result = await api("/api/time/trash");
    items = Array.isArray(result.items) ? result.items : [];
    render();
    setStatus("#time-trash-status", "");
  } catch (error) {
    items = [];
    panel.innerHTML = `<p class="empty-state">回收站读取失败：${escapeHtml(error.message)}</p>`;
    setStatus("#time-trash-status", "", "error");
  }
}

async function purge(paths) {
  const target = items.find((item) => item.trash_path === paths[0]);
  const label = target?.title ? `“${target.title}”` : "选中的任务";
  const accepted = await confirmAction({
    title: paths.length ? "彻底删除任务" : "清空回收站",
    message: paths.length
      ? `会真正删除 ${label} 的 md 文件，无法从回收站找回。`
      : "会真正删除回收站里所有任务笔记，无法恢复。",
    confirmLabel: paths.length ? "彻底删除" : "清空回收站",
  });
  if (!accepted) return;
  setStatus("#time-trash-status", "正在删除…");
  try {
    const body = paths.length ? { paths } : { all: true };
    const result = await api("/api/time/trash/delete", jsonBody("POST", body));
    toast(`已彻底删除 ${Number(result.deleted) || 0} 个任务文件。`, "success");
    await loadTrash();
  } catch (error) {
    setStatus("#time-trash-status", error.message, "error");
  }
}

export function initTrashPage() {
  if (initialized) return;
  initialized = true;
  $("#time-trash-refresh")?.addEventListener("click", loadTrash);
  document.addEventListener("click", (event) => {
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
    if (!$("#time-trash-page")?.hidden) loadTrash();
  });
}
