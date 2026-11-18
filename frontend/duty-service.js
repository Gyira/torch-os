import { api } from "./core.js";
import { shanghaiDay } from "./journal-model.js";

let cached = null, loadedAt = 0, pending = null;
let generation = 0;
export function invalidateDutySchedule() { cached = null; loadedAt = 0; pending = null; generation += 1; }
document.addEventListener("torch:duty-changed", invalidateDutySchedule);
export function loadDutySchedule() {
  if (cached?.today === shanghaiDay() && Date.now() - loadedAt < 30000) return Promise.resolve(cached);
  if (pending) return pending;
  const current = generation;
  pending = api("/api/love/schedule/upcoming?days=31", { timeoutMs: 15000 })
    .then((value) => { if (current === generation) { cached = value; loadedAt = Date.now(); } return value; })
    .finally(() => { if (current === generation) pending = null; });
  return pending;
}

export function dutyTrainCodes(day) {
  if (!day?.in_range || day.work !== true || day.rest === true) return [];
  const codes = [];
  for (const match of String(day.duty || "").toUpperCase().matchAll(/([GDCZTKYS]\d{1,5})(?:\s*[/／]\s*([GDCZTKYS]?\d{1,5}))?/g)) {
    codes.push(match[1]);
    if (match[2]) {
      const other = match[2];
      codes.push(/^[A-Z]/.test(other) ? other : match[1].slice(0, Math.max(1, match[1].length - other.length)) + other);
    }
  }
  return [...new Set(codes)].slice(0, 8);
}

export function chooseDutyTrain(rows) {
  return rows.find((row) => ["running", "at_station"].includes(row?.realtime?.status))
    || rows.filter((row) => row?.realtime?.status === "before_depart")
      .sort((a, b) => (a.realtime.countdown_minutes ?? Infinity) - (b.realtime.countdown_minutes ?? Infinity))[0]
    || rows.find((row) => row?.found) || rows[0] || null;
}
