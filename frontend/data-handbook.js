// 数据手册：显示运行中服务的实时结构摘要，并可下载 Markdown 版本（只读）。
import { $, state, api, escapeHtml, setStatus, toast } from "./core.js";

let loaded = false;
let busy = false;

const available = () => state.config?.capabilities?.schema_export === true;

function formatBytes(value) {
  const size = Number(value) || 0;
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function syncControls() {
  const enabled = available();
  ["#load-schema", "#download-schema-md"].forEach((selector) => {
    const node = $(selector);
    if (node) node.disabled = busy || !enabled;
  });
  const support = $("#schema-support");
  if (support) {
    support.hidden = enabled;
    support.textContent = state.config
      ? "当前运行的服务尚未支持数据手册导出。请更新并重启本地服务，再刷新页面。"
      : "正在检查服务能力；若未恢复，请到设置页重新检查本地服务。";
  }
}

export async function loadDataHandbook(force = false) {
  if (!available() || busy) { syncControls(); return; }
  if (loaded && !force) return;
  busy = true;
  syncControls();
  setStatus("#schema-status", "正在读取实时结构…");
  try {
    const payload = await api("/api/schema.json");
    loaded = true;
    const groups = Object.entries(payload.groups || {})
      .map(([name, count]) => `<div><span>${escapeHtml(name)}</span><strong>${escapeHtml(count)} 张</strong></div>`).join("");
    const busiest = payload.tables
      .filter((table) => Number(table.rows) > 0)
      .sort((left, right) => Number(right.rows) - Number(left.rows))
      .slice(0, 5)
      .map((table) => `<li>${escapeHtml(table.name)}：${escapeHtml(table.rows)} 行</li>`).join("");
    $("#schema-summary").innerHTML = `
      <div class="settings-list">
        <div><span>数据表</span><strong>${escapeHtml(payload.database.tables)} 张</strong></div>
        <div><span>数据库大小</span><strong>${escapeHtml(formatBytes(payload.database.bytes))}</strong></div>
        <div><span>全文检索辅助表</span><strong>${escapeHtml(payload.database.fts_helpers)} 张</strong></div>
      </div>
      <div class="settings-list">${groups}</div>
      ${busiest ? `<h4>行数最多的表</h4><ul class="extract-list">${busiest}</ul>` : ""}
      <p class="field-help">${escapeHtml(payload.note || "")}</p>
      <p class="field-help">生成时间：${escapeHtml(payload.generated_at)}；下载的 Markdown 含每张表的列定义、索引、备份/导出边界与恢复步骤。</p>`;
    setStatus("#schema-status", `已读取 ${payload.database.tables} 张表的实时结构。`, "success");
  } catch (error) {
    setStatus("#schema-status", `读取失败：${error.message}`, "error");
  } finally {
    busy = false;
    syncControls();
  }
}

function downloadSchema() {
  if (!available()) return;
  location.assign("/api/schema.md");
  toast("已开始下载数据手册（Markdown），文件在浏览器下载目录。", "success");
}

export function initDataHandbook() {
  $("#load-schema")?.addEventListener("click", () => loadDataHandbook(true));
  $("#download-schema-md")?.addEventListener("click", downloadSchema);
  document.addEventListener("click", (event) => {
    if (event.target.closest('[data-settings-nav="data"]')) loadDataHandbook();
  });
  document.addEventListener("config-changed", () => {
    syncControls();
    const section = document.querySelector('[data-settings-section="data"]');
    if (state.view === "settings" && section && section.hidden === false) loadDataHandbook();
  });
  syncControls();
}
