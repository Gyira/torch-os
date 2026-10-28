// 列车追踪的纯函数：车次校验、关注列表、卡片摘要。DOM 与请求在 train-tracker-widget.js。
export const TRAIN_WATCH_KEY = "torch-os:train-tracker";
export const MAX_WATCHED_TRAINS = 20;
export const TRAIN_CODE_PATTERN = /^[A-Z]\d{1,5}$/;

export function normalizeTrainCode(value) {
  const code = String(value ?? "").trim().toUpperCase();
  return TRAIN_CODE_PATTERN.test(code) ? code : "";
}

/** 兼容旧版直接存数组 ["G1"]，也接受 { codes, selected }。 */
export function normalizeWatch(value) {
  const source = Array.isArray(value)
    ? { codes: value, selected: "" }
    : (value && typeof value === "object" ? value : {});
  const codes = [];
  for (const raw of Array.isArray(source.codes) ? source.codes : []) {
    const code = normalizeTrainCode(raw);
    if (code && !codes.includes(code)) codes.push(code);
    if (codes.length >= MAX_WATCHED_TRAINS) break;
  }
  const selected = normalizeTrainCode(source.selected);
  return { codes, selected: codes.includes(selected) ? selected : (codes[0] || "") };
}

export function addWatchCode(watch, value) {
  const current = normalizeWatch(watch);
  const code = normalizeTrainCode(value);
  if (!code) return { ...current, error: "车次号格式不正确（例如 G1、D123）" };
  if (current.codes.includes(code)) return { ...current, selected: code, error: "" };
  if (current.codes.length >= MAX_WATCHED_TRAINS) {
    return { ...current, error: `最多关注 ${MAX_WATCHED_TRAINS} 趟车` };
  }
  return { codes: [...current.codes, code], selected: code, error: "" };
}

export function removeWatchCode(watch, value) {
  const current = normalizeWatch(watch);
  const code = normalizeTrainCode(value);
  const codes = current.codes.filter((item) => item !== code);
  return {
    codes,
    selected: current.selected === code ? (codes[0] || "") : current.selected,
    error: "",
  };
}

export function selectWatchCode(watch, value) {
  const current = normalizeWatch(watch);
  const code = normalizeTrainCode(value);
  return { ...current, selected: current.codes.includes(code) ? code : current.selected, error: "" };
}

export function formatCountdown(minutes) {
  const value = Number(minutes);
  if (!Number.isFinite(value) || value < 0) return "";
  if (value < 60) return `${Math.round(value)} 分钟`;
  const hours = Math.floor(value / 60);
  const rest = Math.round(value % 60);
  return rest ? `${hours} 小时 ${rest} 分钟` : `${hours} 小时`;
}

export function statusTone(realtime) {
  const status = String(realtime?.status || "");
  if (status === "running") return "running";
  if (status === "before_depart") return "waiting";
  if (status === "at_station") return "station";
  if (status === "arrived") return "done";
  return "unknown";
}

/** 卡片只给摘要；完整经停站留在弹窗里。 */
export function cardModel(payload, code = "") {
  const train = payload?.train;
  const realtime = payload?.realtime;
  const requested = normalizeTrainCode(code) || normalizeTrainCode(payload?.code);
  if (!requested) {
    return {
      code: "添加车次", status: "追踪你关心的列车", route: "",
      meta: "点击添加车次", progress: 0, tone: "empty",
    };
  }
  if (!payload?.found || !train) {
    return {
      code: requested, status: "缺少时刻表", route: "",
      meta: "导入时刻表，或从 12306 同步", progress: 0, tone: "unknown",
    };
  }
  const route = `${train.from_station || "—"} → ${train.to_station || "—"}`;
  let meta = realtime?.message || "";
  if (realtime?.status === "before_depart") {
    const depart = realtime.next_depart_time || train.depart_time || "";
    const countdown = formatCountdown(realtime.countdown_minutes);
    meta = `${depart ? `${depart} 发车` : "待发车"}${countdown ? ` · ${countdown}` : ""}`;
  } else if (realtime?.status === "running") {
    meta = `${realtime.current_station || "—"} → ${realtime.next_station || "—"}`;
    if (realtime.next_arrive_time) meta += ` · ${realtime.next_arrive_time} 到`;
  } else if (realtime?.status === "at_station") {
    const countdown = formatCountdown(realtime.countdown_minutes);
    meta = `停靠 ${realtime.current_station || "—"}${countdown ? ` · ${countdown} 后发车` : ""}`;
  } else if (realtime?.status === "arrived") {
    meta = `已到达 ${train.to_station || "—"}`;
  } else if (!meta) {
    meta = `${train.depart_time || "—"} → ${train.arrive_time || "—"}`;
  }
  return {
    code: normalizeTrainCode(train.train_code) || requested,
    status: realtime?.status_text || "状态未知",
    route,
    meta,
    progress: Math.max(0, Math.min(100, Number(realtime?.progress_pct) || 0)),
    tone: statusTone(realtime),
  };
}
