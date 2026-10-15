import { shanghaiDay, shiftDay, validJournalDay } from "./journal-model.js";

export { shanghaiDay, shiftDay, validJournalDay };

/** All calendar arithmetic uses date-only UTC values, never the browser timezone. */
export function mondayOf(day = shanghaiDay()) {
  const weekday = new Date(`${day}T00:00:00Z`).getUTCDay();
  return shiftDay(day, -((weekday + 6) % 7));
}

export function rateLabel(value, scale = 1) {
  return typeof value === "number" && Number.isFinite(value) ? `${Math.round(value * scale)}%` : "—";
}

/** A failed attempt keeps its key; changing its payload starts a new draft. */
export function requestDraft(keyFactory = () => crypto.randomUUID()) {
  let fingerprint = null;
  let key = null;
  return {
    forPayload(payload) {
      const next = JSON.stringify(payload);
      if (next !== fingerprint) { key = keyFactory(); fingerprint = next; }
      return { ...payload, request_key: key };
    },
    clear() { fingerprint = null; key = null; },
  };
}

export const habitLabels = { done: "已完成", skipped: "主动休息", unrecorded: "未记录" };
export const weekdayLabels = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];
