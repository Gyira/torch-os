// Local four-pillars reference page: birth data never leaves this machine and
// the chart is shown as a traditional-culture mirror for reflection, not advice.
import { $, state, api, escapeHtml, jsonBody, renderState, setStatus, toast, confirmAction } from "./core.js";

let loadId = 0;
let busy = false;
let loading = false;

const available = () => state.config?.capabilities?.bazi === true;

function syncControls() {
  const enabled = available();
  const disabled = busy || loading || !enabled;
  ["#bazi-birth-date", "#bazi-birth-time", "#bazi-longitude", "#bazi-true-solar"].forEach((selector) => {
    const node = $(selector);
    if (node) node.disabled = disabled;
  });
  const save = $("#bazi-save");
  if (save) save.disabled = disabled;
  const clear = $("#bazi-clear");
  if (clear) clear.disabled = disabled || clear.dataset.hasProfile !== "true";
  const longitude = $("#bazi-longitude");
  if (longitude) longitude.required = Boolean($("#bazi-true-solar")?.checked);
  const support = $("#bazi-support");
  if (support) {
    support.hidden = enabled;
    support.textContent = state.config
      ? "当前运行的服务尚未支持八字参考页。请更新并重启本地服务，再刷新页面。"
      : "正在检查服务能力；若未恢复，请到设置页重新检查本地服务。";
  }
  const form = $("#bazi-form");
  if (form) form.dataset.disabled = enabled ? "false" : "true";
}

function elementRows(counts) {
  const order = ["金", "木", "水", "火", "土"];
  const total = order.reduce((sum, element) => sum + (counts?.[element] || 0), 0) || 1;
  return order.map((element) => {
    const value = counts?.[element] || 0;
    const width = Math.round((value / total) * 100);
    return `<div class="bazi-element"><span class="bazi-element-name">${element}</span><span class="bazi-element-bar" aria-hidden="true"><i style="width:${width}%"></i></span><span class="bazi-element-value">${value}</span></div>`;
  }).join("");
}

function pillarTable(pillars) {
  const rows = ["ganzhi", "wu_xing", "na_yin", "shi_shen_gan"];
  const labels = { ganzhi: "干支", wu_xing: "五行", na_yin: "纳音", shi_shen_gan: "十神（天干）" };
  const head = pillars.map((pillar) => `<th scope="col">${escapeHtml(pillar.name)}</th>`).join("");
  const body = rows.map((key) => `<tr><th scope="row">${labels[key]}</th>${pillars
    .map((pillar) => `<td>${escapeHtml(pillar[key] ?? "—")}</td>`).join("")}</tr>`).join("");
  const stems = pillars.map((pillar) => {
    const zhi = Array.isArray(pillar.shi_shen_zhi) ? pillar.shi_shen_zhi.filter(Boolean).join("、") : "";
    return `<tr><th scope="row">十神（${escapeHtml(pillar.name)}地支）</th><td colspan="4">${escapeHtml(zhi || "—")}</td></tr>`;
  }).join("");
  return `<table class="bazi-table"><caption>四柱（年 / 月 / 日 / 时）</caption><thead><tr><th scope="col">项目</th>${head}</tr></thead><tbody>${body}${stems}</tbody></table>`;
}

function renderChart(chart) {
  const lunar = chart.lunar || {};
  const solar = chart.true_solar || {};
  const solarLine = solar.applied
    ? `真太阳时：${escapeHtml(solar.corrected_time || "")}（经度时差 ${solar.longitude_minutes} 分 + 均时差 ${solar.equation_of_time_minutes} 分）`
    : escapeHtml(solar.note || "");
  return `
    <div class="bazi-summary">
      <div><span class="eyebrow">日主</span><strong>${escapeHtml(chart.day_master)}</strong></div>
      <div><span class="eyebrow">农历</span><strong>${escapeHtml(lunar.text || "—")}</strong></div>
      <div><span class="eyebrow">生肖</span><strong>${escapeHtml(lunar.zodiac || "—")}</strong></div>
      <div><span class="eyebrow">排盘时间</span><strong>${escapeHtml(chart.chart_time || "")}</strong></div>
    </div>
    <p class="meta">${solarLine}</p>
    ${pillarTable(chart.pillars || [])}
    <div class="bazi-elements"><h3>五行计数</h3>${elementRows(chart.five_elements)}<p class="field-help">${escapeHtml(chart.five_elements_note || "")}</p></div>
    <div class="bazi-reflection"><h3>可以拿来问自己的问题</h3><ol>
      <li>这份表里我最先注意到的字是什么？它和我最近的状态有没有关系？</li>
      <li>最近一次让我消耗很大的事，和我的做事方式有什么关系？下次想怎么调整？</li>
      <li>如果只改一个小习惯，哪个最可能让接下来一个月轻松一点？</li>
      <li>我正在推进的目标里，哪一个现在最需要“减少消耗”而不是“再加把劲”？</li>
    </ol><p class="field-help">这些问题只是反思的起点，不是结论；不要用它替代医疗、财务或人生重大决定。想写下答案，可以去“每日记录”；想落到行动，去“目标与任务”。</p></div>
    <ul class="bazi-notes">${(chart.notes || []).map((note) => `<li>${escapeHtml(note)}</li>`).join("")}</ul>`;
}

function fillForm(profile) {
  $("#bazi-birth-date").value = profile?.birth_date || "";
  $("#bazi-birth-time").value = profile?.birth_time || "";
  $("#bazi-true-solar").checked = Boolean(profile?.use_true_solar);
  $("#bazi-longitude").value = profile?.longitude ?? "";
  const clear = $("#bazi-clear");
  if (clear) clear.dataset.hasProfile = profile ? "true" : "false";
  const updated = $("#bazi-updated");
  if (updated) updated.textContent = profile?.updated_at ? `出生资料保存于 ${profile.updated_at.slice(0, 19).replace("T", " ")}` : "还没有保存出生资料。";
}

function renderPayload(payload) {
  fillForm(payload?.profile || null);
  const target = $("#bazi-chart");
  if (!payload?.profile || !payload?.chart) {
    renderState(target, "empty", "填好出生日期与时间并保存后，这里会显示四柱参考表。");
    return;
  }
  target.innerHTML = renderChart(payload.chart);
}

export async function loadBazi() {
  const request = ++loadId;
  loading = true;
  syncControls();
  if (available()) {
    try {
      const payload = await api("/api/bazi/profile");
      if (request !== loadId) return;
      renderPayload(payload);
      setStatus("#bazi-status", payload?.profile ? "已显示保存在本机的排盘结果。" : "填写后保存，全部计算在本机完成。");
    } catch (error) {
      if (request !== loadId) return;
      renderState($("#bazi-chart"), "error", `读取失败：${escapeHtml(error.message)}`, "data-retry=\"bazi\"");
      setStatus("#bazi-status", "读取出生资料失败。", "error");
    }
  } else {
    fillForm(null);
    renderState($("#bazi-chart"), "empty", "当前服务未提供八字参考能力。");
    setStatus("#bazi-status", "");
  }
  loading = false;
  syncControls();
}

async function saveProfile(event) {
  event.preventDefault();
  if (!available() || busy) return;
  const birthDate = $("#bazi-birth-date").value;
  const birthTime = $("#bazi-birth-time").value.slice(0, 5);
  const useTrueSolar = $("#bazi-true-solar").checked;
  const longitudeRaw = $("#bazi-longitude").value.trim();
  if (!birthDate || !birthTime) {
    setStatus("#bazi-status", "请填写出生日期与出生时间（不知道时间可以先填 12:00，但时柱会随之变化）。", "error");
    return;
  }
  if (useTrueSolar && !longitudeRaw) {
    setStatus("#bazi-status", "勾选真太阳时后需要填写出生地经度；不知道可以取消勾选并按北京时间排盘。", "error");
    return;
  }
  busy = true;
  syncControls();
  try {
    const payload = await api("/api/bazi/profile", jsonBody("PUT", {
      birth_date: birthDate,
      birth_time: birthTime,
      longitude: longitudeRaw === "" ? null : Number(longitudeRaw),
      use_true_solar: useTrueSolar,
    }));
    renderPayload(payload);
    setStatus("#bazi-status", "已保存在本机；不会发送给任何模型或网络服务。", "success");
    toast("排盘参考已更新");
  } catch (error) {
    setStatus("#bazi-status", `保存失败：${error.message}`, "error");
  } finally {
    busy = false;
    syncControls();
  }
}

async function clearProfile() {
  if (!available() || busy) return;
  const accepted = await confirmAction({
    title: "清除出生资料？",
    message: "会删除本机保存的出生日期、时间和经度；已生成的排盘结果一并消失，不会影响日记、记忆或任务。",
    confirmLabel: "清除出生资料",
  });
  if (!accepted) return;
  busy = true;
  syncControls();
  try {
    await api("/api/bazi/profile/clear", jsonBody("POST", { confirm: true }));
    renderPayload(null);
    setStatus("#bazi-status", "出生资料已从本机清除。", "success");
  } catch (error) {
    setStatus("#bazi-status", `清除失败：${error.message}`, "error");
  } finally {
    busy = false;
    syncControls();
  }
}

export function initBazi() {
  $("#bazi-form")?.addEventListener("submit", saveProfile);
  $("#bazi-clear")?.addEventListener("click", clearProfile);
  $("#bazi-true-solar")?.addEventListener("change", syncControls);
  document.addEventListener("click", (event) => {
    if (event.target.closest('[data-retry="bazi"]')) loadBazi();
    if (event.target.closest("[data-bazi-refresh]")) loadBazi();
  });
  // The page can be opened by deep link before the service status arrives.
  document.addEventListener("config-changed", () => {
    syncControls();
    if (state.view === "self") loadBazi();
  });
  syncControls();
}
