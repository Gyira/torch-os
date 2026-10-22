import { $, state, api, jsonBody, setStatus } from "./core.js";

// 本地定时提醒的设置面板（设置页“定时提醒”）。工作台上的“提醒与草稿”列表
// 2026-10-05 按用户要求删除——提醒以后以铃铛推送的形式回来；这里只留设置与
// “立即生成”动作。

let scheduleId = 0;
const kindLabels = { morning: "早报", evening: "晚报", weekly: "周报" };
const TIME_PATTERN = /^(?:[01][0-9]|2[0-3]):[0-5][0-9]$/;
const available = () => state.config?.capabilities?.scheduler === true;
const weeklyAvailable = () => state.config?.capabilities?.scheduler_weekly === true;

export async function loadSchedulePanel() {
  if (!available()) {
    setStatus("#schedule-status", state.config ? "当前运行的服务尚未支持定时提醒。" : "正在检查服务能力…");
    return;
  }
  const request = ++scheduleId;
  setStatus("#schedule-status", "正在读取定时设置…");
  try {
    const result = await api("/api/schedule");
    if (request !== scheduleId) return;
    const settings = result.settings || {};
    $("#schedule-morning-enabled").checked = Boolean(settings.morning_enabled);
    $("#schedule-morning-time").value = settings.morning_time || "08:00";
    $("#schedule-evening-enabled").checked = Boolean(settings.evening_enabled);
    $("#schedule-evening-time").value = settings.evening_time || "21:30";
    const weekly = weeklyAvailable();
    ["#schedule-weekly-enabled", "#schedule-weekly-day", "#schedule-weekly-time", "#schedule-run-weekly"].forEach((selector) => {
      const node = $(selector);
      if (node) node.disabled = !weekly;
    });
    const weeklyHelp = $("#schedule-weekly-help");
    if (weeklyHelp) {
      weeklyHelp.textContent = weekly
        ? "周报汇总截至触发日的最近 7 天（任务、习惯、日记与身体记录天数），只列事实，不做评分。"
        : "当前服务版本未提供周报草稿，请更新并重启本地服务后再设置。";
    }
    if (weekly) {
      const enabled = $("#schedule-weekly-enabled");
      const day = $("#schedule-weekly-day");
      const weeklyTime = $("#schedule-weekly-time");
      if (enabled) enabled.checked = Boolean(settings.weekly_enabled);
      if (day) day.value = String(Number.isInteger(settings.weekly_day) ? settings.weekly_day : 6);
      if (weeklyTime) weeklyTime.value = settings.weekly_time || "20:00";
    }
    const runs = Array.isArray(result.today_runs) ? result.today_runs : [];
    const failing = Number(result.runtime?.consecutive_failures) || 0;
    const runtimeText = result.runtime ? (failing ? `调度连续失败 ${failing} 次` : "调度正常") : "";
    const runsText = runs.length ? `今天已生成 ${runs.length} 条草稿` : "今天还没有生成草稿";
    setStatus("#schedule-status", runtimeText ? `${runsText} · ${runtimeText}` : runsText, failing ? "error" : "success");
  } catch (error) {
    if (request !== scheduleId) return;
    setStatus("#schedule-status", `读取失败：${error.message}`, "error");
  }
}

async function saveSchedule(event) {
  event.preventDefault();
  if (!available()) return;
  const morningTime = $("#schedule-morning-time").value.trim();
  const eveningTime = $("#schedule-evening-time").value.trim();
  if (!TIME_PATTERN.test(morningTime) || !TIME_PATTERN.test(eveningTime)) {
    setStatus("#schedule-feedback", "时间必须是 HH:MM（例如 08:00）。", "error");
    return;
  }
  const button = event.currentTarget.querySelector("button[type=submit]");
  button.disabled = true;
  setStatus("#schedule-feedback", "正在保存…");
  try {
    const payload = {
      morning_enabled: $("#schedule-morning-enabled").checked,
      morning_time: morningTime,
      evening_enabled: $("#schedule-evening-enabled").checked,
      evening_time: eveningTime,
    };
    if (weeklyAvailable() && $("#schedule-weekly-time")) {
      const weeklyTime = $("#schedule-weekly-time").value.trim();
      const weeklyDay = Number($("#schedule-weekly-day").value);
      if (!TIME_PATTERN.test(weeklyTime) || !Number.isInteger(weeklyDay) || weeklyDay < 0 || weeklyDay > 6) {
        setStatus("#schedule-feedback", "周报日期或时间无效，请重新选择。", "error");
        button.disabled = false;
        return;
      }
      Object.assign(payload, {
        weekly_enabled: $("#schedule-weekly-enabled").checked,
        weekly_day: weeklyDay,
        weekly_time: weeklyTime,
      });
    }
    await api("/api/schedule", jsonBody("PUT", payload));
    setStatus("#schedule-feedback", "定时设置已保存；到点后会生成草稿。", "success");
    await loadSchedulePanel();
  } catch (error) {
    setStatus("#schedule-feedback", `${error.message} 输入已保留，可以重试。`, "error");
  } finally { button.disabled = false; }
}

async function runNow(kind, button) {
  if (!available() || button.disabled) return;
  if (kind === "weekly" && !weeklyAvailable()) return;
  button.disabled = true;
  setStatus("#schedule-feedback", `正在生成${kindLabels[kind]}草稿…`);
  try {
    const result = await api("/api/schedule/run-now", jsonBody("POST", { kind }));
    setStatus("#schedule-feedback", result.created ? `${kindLabels[kind]}草稿已生成。` : `今天已有${kindLabels[kind]}草稿，未重复生成。`, "success");
  } catch (error) {
    setStatus("#schedule-feedback", `${error.message} 可以重试。`, "error");
  } finally { button.disabled = false; }
}

export function initInbox() {
  $("#schedule-form").addEventListener("submit", saveSchedule);
  $("#schedule-run-morning").addEventListener("click", (event) => runNow("morning", event.currentTarget));
  $("#schedule-run-evening").addEventListener("click", (event) => runNow("evening", event.currentTarget));
  $("#schedule-run-weekly")?.addEventListener("click", (event) => runNow("weekly", event.currentTarget));
  document.addEventListener("config-changed", () => {
    if (state.view === "settings") loadSchedulePanel();
  });
}
