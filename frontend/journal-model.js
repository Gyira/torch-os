/** Calendar boundaries are independent of the browser's timezone. */
export function shanghaiDay(now = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(now);
  const value = (type) => parts.find((part) => part.type === type).value;
  return `${value("year")}-${value("month")}-${value("day")}`;
}

export function shiftDay(day, offset) {
  const date = new Date(`${day}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + offset);
  return date.toISOString().slice(0, 10);
}

export function validJournalDay(day, today = shanghaiDay()) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day) || day > today) return false;
  const parsed = new Date(`${day}T00:00:00Z`);
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === day;
}

export function journalFields(record = {}) {
  return { content: record.content || "", highlights: record.highlights || "", obstacles: record.obstacles || "", diet_summary: record.diet_summary || "", mood: record.mood || "", next_action: record.next_action || "", goal_id: record.goal_id == null || record.goal_id === "" ? null : Number(record.goal_id) };
}

export function journalChanged(record, draft) {
  return JSON.stringify(journalFields(record)) !== JSON.stringify(journalFields(draft));
}
