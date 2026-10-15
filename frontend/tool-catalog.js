// 只读工具接口清单：给外部智能体（例如 DeepSeek Harness）看的“能读什么”。
import { $, state, api, escapeHtml, setStatus } from "./core.js";

let loaded = false;
let busy = false;
let selftesting = false;

const available = () => state.config?.capabilities?.tools === true;
const bridgeAvailable = () => available() && state.config?.capabilities?.mcp_bridge === true;

function syncControls() {
  const enabled = available();
  const button = $("#load-tools");
  if (button) button.disabled = busy || !enabled;
  const support = $("#tools-support");
  if (support) {
    support.hidden = enabled;
    support.textContent = state.config
      ? "当前运行的服务尚未支持只读工具接口。请更新并重启本地服务，再刷新页面。"
      : "正在检查服务能力；若未恢复，请到设置页重新检查本地服务。";
  }
  const bridgeButton = $("#run-bridge-selftest");
  if (bridgeButton) bridgeButton.disabled = busy || selftesting || !bridgeAvailable();
}

async function runBridgeSelftest() {
  if (!bridgeAvailable() || selftesting) return;
  selftesting = true;
  syncControls();
  setStatus("#bridge-selftest-status", "正在自检工具桥（会用与 MCP 客户端相同的方式启动本机桥）…");
  try {
    const result = await api("/api/bridge/selftest", { method: "POST" });
    if (result.ok) {
      setStatus("#bridge-selftest-status",
        `桥自检通过：协议 ${result.protocol_version} · ${result.tool_count} 个只读工具 · ${result.resources ?? "?"} 个资源 · 示例调用 ${result.sample_tool} 成功（${result.elapsed_ms} 毫秒）。`,
        "success");
    } else {
      setStatus("#bridge-selftest-status", `桥自检未通过：${result.error || "未知原因"}`, "error");
    }
  } catch (error) {
    setStatus("#bridge-selftest-status", `桥自检未通过：${error.message}`, "error");
  } finally {
    selftesting = false;
    syncControls();
  }
}

export async function loadToolCatalog(force = false) {
  if (!available() || busy) { syncControls(); return; }
  if (loaded && !force) return;
  busy = true;
  syncControls();
  setStatus("#tools-status", "正在读取工具清单…");
  try {
    const payload = await api("/api/tools");
    loaded = true;
    const rows = payload.tools.map((tool) => `
      <div><span>${escapeHtml(tool.name)}</span><strong>${escapeHtml(tool.method)} ${escapeHtml(tool.path)}</strong></div>`).join("");
    const details = payload.tools.map((tool) => `
      <li><strong>${escapeHtml(tool.name)}</strong>：${escapeHtml(tool.summary)}${(tool.parameters || []).length ? `（参数：${escapeHtml(tool.parameters.join("、"))}）` : ""}</li>`).join("");
    $("#tools-summary").innerHTML = `
      <div class="settings-list">${rows}</div>
      <ul class="extract-list">${details}</ul>
      <p class="field-help">${escapeHtml(payload.note || "")}</p>`;
    setStatus("#tools-status", `共 ${payload.count} 个只读工具；写操作从不通过这些接口开放。`, "success");
  } catch (error) {
    setStatus("#tools-status", `读取失败：${error.message}`, "error");
  } finally {
    busy = false;
    syncControls();
  }
}

export function initToolCatalog() {
  $("#load-tools")?.addEventListener("click", () => loadToolCatalog(true));
  $("#run-bridge-selftest")?.addEventListener("click", runBridgeSelftest);
  document.addEventListener("click", (event) => {
    if (event.target.closest('[data-settings-nav="data"]')) loadToolCatalog();
  });
  document.addEventListener("config-changed", () => {
    syncControls();
    const section = document.querySelector('[data-settings-section="data"]');
    if (state.view === "settings" && section && section.hidden === false) loadToolCatalog();
  });
  syncControls();
}
