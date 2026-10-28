// 工作台「值班」小组件的纯函数：把 /api/love/schedule/upcoming 的结果整理成卡片与弹窗模型。
// 数据来自排班表；没有值乘的日子 duty 为空，状态仍是上班/休息/未覆盖。

export const DUTY_STRIP_DAYS = 7;

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

export function formatMonthDay(value) {
  const match = ISO_DATE.exec(String(value || ""));
  return match ? `${match[2]}-${match[3]}` : "";
}

export function formatFullDay(value, weekday = "") {
  const match = ISO_DATE.exec(String(value || ""));
  if (!match) return "";
  return `${Number(match[2])}月${Number(match[3])}日${weekday ? ` ${weekday}` : ""}`;
}

export function dutyStateLabel(day) {
  if (!day || !day.inRange) return "未覆盖";
  if (day.rest) return "休息";
  if (day.work) return "上班";
  return "未覆盖";
}

export function normalizeDutyPayload(payload) {
  const days = (Array.isArray(payload?.days) ? payload.days : [])
    .map((item) => ({
      date: String(item?.date || "").trim(),
      weekday: String(item?.weekday || "").trim(),
      inRange: item?.in_range === true,
      work: item?.work === true,
      rest: item?.rest === true,
      duty: String(item?.duty || "").replace(/\s+/g, " ").trim(),
      note: String(item?.note || "").replace(/\s+/g, " ").trim(),
    }))
    .filter((item) => ISO_DATE.test(item.date));
  return {
    today: String(payload?.today || "").trim(),
    start: String(payload?.start || "").trim(),
    found: payload?.found === true,
    error: String(payload?.error || "").trim(),
    days,
  };
}

/** 卡片只显示今天 + 接下来 7 天；完整 14 天留给弹窗。 */
export function dutyCardModel(payload) {
  const data = normalizeDutyPayload(payload);
  const today = data.days[0] || null;
  const state = dutyStateLabel(today);
  const tone = !today || !today.inRange ? "out" : today.rest ? "rest" : "work";
  const train = today?.duty || "";
  const next = data.days.find((day) => today && day.date > today.date && day.work);
  const nextText = next
    ? `下次上班 ${formatMonthDay(next.date)}${next.duty ? ` · ${next.duty}` : ""}`
    : "近期没有上班安排";
  const strip = data.days.slice(0, DUTY_STRIP_DAYS).map((day) => ({
    date: day.date,
    day: day.date.slice(8),
    weekday: day.weekday.slice(-1),
    inRange: day.inRange,
    work: day.work,
    rest: day.rest,
    duty: day.duty,
    isToday: Boolean(today && day.date === today.date),
    label: `${day.date} ${day.weekday}，${dutyStateLabel(day)}${day.duty ? `，值乘：${day.duty}` : ""}`,
  }));
  return {
    dateLabel: today ? formatFullDay(today.date, today.weekday) : "正在读取…",
    state,
    tone,
    train,
    trainText: train || (today?.rest ? "今天休息" : today?.work ? "今天上班" : "排班表未覆盖"),
    nextText,
    strip,
    today,
    found: data.found,
    error: data.error,
  };
}

/** 弹窗里按天列出；保留备注，方便值班时快速看一眼。 */
export function dutyDialogRows(payload) {
  return normalizeDutyPayload(payload).days.map((day) => ({
    ...day,
    state: dutyStateLabel(day),
    monthDay: formatMonthDay(day.date) || day.date,
  }));
}
