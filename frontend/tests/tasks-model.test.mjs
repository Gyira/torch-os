// 时间管理模块的纯函数：视图过滤、分组、排序、计数、日期工具。
import test from "node:test";
import assert from "node:assert/strict";
import {
  dayOffset, relativeDay, filterTasks, viewCounts, countBy, groupTasks, sortTasks,
  boardTitle, priorityMeta, VIEWS, listTree, pinnedItems, listIcon,
  QUADRANTS, quadrantBuckets, monthCells, monthTitle, tasksByDay,
  addMonths, weekCells, dayCells, weekRangeCells, yearMonths,
  timeToMinutes, minutesToLabel, clockLabel, timeLabel, sortDayTasks, layoutBlocks,
  firstBusyHour, listColorMap, taskColor, shiftAnchor, calendarViewLabel, calendarTitle,
  CALENDAR_VIEWS, MULTIDAY_OPTIONS, MULTIWEEK_OPTIONS, defaultCalendarState,
} from "../tasks-model.js";

const TODAY = "2026-10-08";

function task(overrides = {}) {
  return {
    key: "k", path: "00-System/任务/任务.md", legacy: false, list_name: "收集箱",
    title: "任务", done: false, done_on: "", date: "", priority: "none", tags: [],
    pinned: false, created: "2026-10-01",
    ...overrides,
  };
}

const TASKS = [
  task({ title: "今天的事", date: TODAY }),
  task({ title: "逾期的事", date: "2026-10-05" }),
  task({ title: "明天的事", date: "2026-10-09" }),
  task({ title: "下周的事", date: "2026-10-20" }),
  task({ title: "没日期的事" }),
  task({ title: "别人清单的任务", date: TODAY, list_name: "心愿清单", tags: ["心愿"] }),
  task({ title: "已经做完的", done: true, done_on: "2026-10-07", date: TODAY }),
];

test("dayOffset walks calendar days as plain strings", () => {
  assert.equal(dayOffset("2026-10-08", 1), "2026-10-09");
  assert.equal(dayOffset("2026-10-31", 1), "2026-11-01");
  assert.equal(dayOffset("2026-03-01", -1), "2026-02-28");
  assert.equal(dayOffset("2026-10-08", 6), "2026-10-14");
  assert.equal(dayOffset("", 1), "");
  assert.equal(relativeDay(TODAY, TODAY), "今天");
  assert.equal(relativeDay("2026-10-09", TODAY), "明天");
  assert.equal(relativeDay("2026-10-07", TODAY), "昨天");
  assert.equal(relativeDay("2026-10-20", TODAY), "10-20");
});

test("the three board views pick the right tasks", () => {
  const titles = (list) => list.map((item) => item.title);
  assert.deepEqual(titles(filterTasks(TASKS, "today", { today: TODAY })), ["今天的事", "别人清单的任务"]);
  assert.deepEqual(titles(filterTasks(TASKS, "inbox", { today: TODAY })),
    ["今天的事", "逾期的事", "明天的事", "下周的事", "没日期的事"]);
  assert.deepEqual(titles(filterTasks(TASKS, "done", { today: TODAY })), ["已经做完的"]);

  // 显示已完成：外部勾选后工作台里能直接看到勾上的那一行
  assert.deepEqual(titles(filterTasks(TASKS, "today", { today: TODAY, showDone: true })),
    ["今天的事", "别人清单的任务", "已经做完的"]);
  assert.deepEqual(titles(filterTasks(TASKS, "inbox", { today: TODAY, showDone: true })),
    ["今天的事", "逾期的事", "明天的事", "下周的事", "没日期的事", "已经做完的"]);
  assert.deepEqual(viewCounts(TASKS, { today: TODAY, showDone: true }),
    { today: 3, inbox: 6, done: 1 });

  // 清单 / 标签视图：已完成也留着（列表里只看未完成会让人以为东西丢了）
  assert.equal(filterTasks(TASKS, "today", { list: "心愿清单" }).length, 1);
  assert.equal(filterTasks(TASKS, "today", { tag: "心愿" }).length, 1);
  assert.deepEqual(viewCounts(TASKS, { today: TODAY }), { today: 2, inbox: 5, done: 1 });
  assert.deepEqual(countBy(TASKS, "list_name"), { 收集箱: 5, 心愿清单: 1 });
  assert.deepEqual(countBy(TASKS, "tags"), { 心愿: 1 });
});

test("grouping by time / list / tag / priority / created", () => {
  const sample = TASKS.filter((item) => !item.done && item.date && item.date <= "2026-10-09");
  const byTime = groupTasks(sample, "time", { today: TODAY });
  assert.deepEqual(byTime.map((group) => group.label), ["已逾期", "今天", "明天"]);
  assert.deepEqual(byTime[1].tasks.map((item) => item.title), ["今天的事", "别人清单的任务"]);

  // 分组本身不过滤：拿到什么分什么（已完成的任务也会进它那一天）
  const raw = groupTasks(TASKS, "time", { today: TODAY });
  assert.deepEqual(raw.map((group) => group.label), ["已逾期", "今天", "明天", "以后", "无日期"]);
  assert.deepEqual(raw[1].tasks.map((item) => item.title), ["今天的事", "别人清单的任务", "已经做完的"]);

  const byList = groupTasks(TASKS, "list", { today: TODAY, listOrder: ["心愿清单", "收集箱"] });
  assert.deepEqual(byList.map((group) => group.label), ["心愿清单", "收集箱"]); // 按定义顺序

  const byTag = groupTasks([...TASKS, task({ title: "两个标签", tags: ["a", "b"] })], "tag", { today: TODAY });
  assert.deepEqual([...byTag.map((group) => group.label)].sort(), ["#a", "#b", "#心愿", "无标签"].sort());
  assert.equal(byTag.at(-1).label, "无标签"); // 没标签的永远排最后
  const two = byTag.find((group) => group.label === "#a");
  assert.deepEqual(two.tasks.map((item) => item.title), ["两个标签"]);

  const byPriority = groupTasks([
    task({ title: "高", priority: "high" }), task({ title: "无" }), task({ title: "中", priority: "medium" }),
  ], "priority", { today: TODAY });
  assert.deepEqual(byPriority.map((group) => group.label), ["高优先级", "中优先级", "无优先级"]);
  assert.equal(priorityMeta("high").flag, "⏫");
  assert.equal(priorityMeta("hack").value, "none");

  assert.deepEqual(groupTasks(TASKS, "none").length, 1);
  assert.deepEqual(groupTasks(TASKS, "none")[0].tasks.length, TASKS.length);
});

test("sorting keeps pinned tasks on top and honours the direction", () => {
  const items = [
    task({ title: "B", date: "2026-10-09" }),
    task({ title: "A", date: "2026-10-07" }),
    task({ title: "无日期" }),
    task({ title: "置顶的", date: "2026-10-20", pinned: true }),
  ];
  assert.deepEqual(sortTasks(items, "time", "old").map((item) => item.title),
    ["置顶的", "A", "B", "无日期"]); // 置顶最前；空日期沉底
  assert.deepEqual(sortTasks(items, "time", "new").map((item) => item.title),
    ["置顶的", "B", "A", "无日期"]);
  // 按标题：置顶仍在最前，其余按标题序（中文/拉丁的先后交给 Intl 排序规则，这里只验 A 在 B 前）
  const byTitle = sortTasks(items, "title", "old").map((item) => item.title);
  assert.equal(byTitle[0], "置顶的");
  assert.ok(byTitle.indexOf("A") < byTitle.indexOf("B"));
  const byPriority = sortTasks([
    task({ title: "无" }), task({ title: "高", priority: "high" }), task({ title: "低", priority: "low" }),
  ], "priority", "old");
  assert.deepEqual(byPriority.map((item) => item.title), ["高", "低", "无"]);
  // 同年同月同日生：按笔记路径稳定排序（旧任务看 key/title）
  const same = sortTasks([
    task({ title: "第二条", path: "00-System/任务/b.md" }),
    task({ title: "第一条", path: "00-System/任务/a.md" }),
  ], "time", "old");
  assert.deepEqual(same.map((item) => item.title), ["第一条", "第二条"]);
});

test("boardTitle names the current selection", () => {
  assert.equal(boardTitle({ view: "today" }), "今天");
  assert.equal(boardTitle({ view: "week" }), "今天");  // 旧的本机状态回落到今天
  assert.equal(boardTitle({ list: "客户A" }), "客户A");
  assert.equal(boardTitle({ tag: "重要" }), "#重要");
  assert.equal(boardTitle({}), "今天");
  assert.deepEqual(VIEWS.map((view) => view.id), ["today", "inbox", "done"]);
});

test("listTree groups root lists first and folds folders in order", () => {
  const lists = [
    { name: "收集箱", folder: "", color: "#3b82f6" },
    { name: "客户D", folder: "事业", color: "#ef4444" },
    { name: "购物", folder: "", color: "" },
    { name: "周报", folder: "事业", color: "#22c55e" },
  ];
  const tree = listTree(lists, ["事业", "空文件夹"]);
  assert.deepEqual(tree.map((group) => group.folder), ["", "事业", "空文件夹"]);
  assert.deepEqual(tree[0].lists.map((entry) => entry.name), ["收集箱", "购物"]);
  assert.deepEqual(tree[1].lists.map((entry) => entry.name), ["客户D", "周报"]);
  assert.deepEqual(tree[2].lists, []);  // 空文件夹也留在树上（手写 front matter 里的）
  // 清单写了文件夹、但 文件夹 列表里没有的，也要出现（不凭空丢清单）
  assert.deepEqual(listTree([{ name: "X", folder: "临时" }], []).map((g) => g.folder), ["临时"]);
  assert.deepEqual(listTree([], []), []);
});

test("pinnedItems keeps lists before tags and carries colors", () => {
  const pinned = pinnedItems(
    [{ name: "心愿清单", pinned: true, color: "#8b5cf6" }, { name: "收集箱", pinned: false }],
    [{ name: "重要", pinned: true, color: "#ef4444" }, { name: "随手", pinned: false }],
  );
  assert.deepEqual(pinned, [
    { kind: "list", name: "心愿清单", color: "#8b5cf6" },
    { kind: "tag", name: "重要", color: "#ef4444" },
  ]);
  assert.deepEqual(pinnedItems(), []);
  assert.equal(listIcon({ name: "x" }), "☰");
  assert.equal(listIcon({ name: "x", color: "#fff" }), "");
});

test("quadrantBuckets maps priority to the eisenhower names", () => {
  const buckets = quadrantBuckets([
    task({ title: "高", priority: "high" }),
    task({ title: "中", priority: "medium", date: TODAY }),
    task({ title: "低", priority: "low" }),
    task({ title: "无日期", priority: "none" }),
    task({ title: "有日期", priority: "none", date: TODAY }),
    task({ title: "已完成的", priority: "high", done: true }),
  ]);
  assert.deepEqual(buckets.map((b) => b.label), ["重要且紧急", "重要不紧急", "不重要但紧急", "不重要不紧急"]);
  assert.deepEqual(buckets.map((b) => b.total), [1, 1, 1, 2]);  // 已完成的不进象限
  assert.deepEqual(buckets[3].undated.map((t) => t.title), ["无日期"]);
  assert.deepEqual(buckets[3].dated.map((t) => t.title), ["有日期"]);
  assert.deepEqual(buckets[0].hint, "高优先级");
  assert.deepEqual(quadrantBuckets(), QUADRANTS.map((q) => ({ ...q, total: 0, undated: [], dated: [] })));
});

test("monthCells lays out whole sunday-first weeks", () => {
  // 2026-10-01 是周四：前面补 4 天（9/27 周日 起），10 月 31 天，后面补齐到整周
  const cells = monthCells("2026-10");
  assert.equal(cells.length % 7, 0);
  assert.equal(cells[0], "2026-09-27");
  assert.equal(cells[4], "2026-10-01");
  assert.equal(cells[4 + 30], "2026-10-31");
  // 2026-10-01 是周四、10-31 是周六：9/27（周日）打头刚好 5 整周，不需要补 11 月
  assert.equal(cells.at(-1), "2026-10-31");
  assert.equal(cells.length, 35);
  assert.deepEqual(monthCells("2026-02"), monthCells("2026-02"));
  assert.equal(monthCells("2026-02").length % 7, 0);
  assert.deepEqual(monthCells(""), []);
  assert.equal(monthTitle("2026-10"), "2026年10月");
  assert.equal(monthTitle("2026-12", 1), "2027年1月");
  assert.equal(monthTitle("2026-01", -1), "2025年12月");
});

test("tasksByDay ignores undated tasks", () => {
  const buckets = tasksByDay([
    task({ title: "A", date: TODAY }), task({ title: "B", date: TODAY }), task({ title: "C" }),
  ]);
  assert.deepEqual(Object.keys(buckets), [TODAY]);
  assert.equal(buckets[TODAY].length, 2);
  assert.deepEqual(tasksByDay(), {});
});

// ===== 日历七种视图那批纯函数（2026-10-11）=====

test("calendar cells walk weeks, day ranges and whole years", () => {
  // 2026-10-11 是周日；10-07 是周三
  assert.deepEqual(weekCells("2026-10-11"), ["2026-10-11", "2026-10-12", "2026-10-13",
    "2026-10-14", "2026-10-15", "2026-10-16", "2026-10-17"]);
  assert.deepEqual(weekCells("2026-10-07").slice(0, 1), ["2026-10-04"]);          // 周日开头
  assert.deepEqual(weekCells("2026-10-07", 1).slice(0, 1), ["2026-10-05"]);       // 周一开头
  assert.deepEqual(weekCells(""), []);
  assert.deepEqual(dayCells("2026-10-30", 3), ["2026-10-30", "2026-10-31", "2026-11-01"]);
  assert.deepEqual(dayCells("2026-10-30", 0), []);
  // 多周：从锚点那周的开头起整两周
  const fortnight = weekRangeCells("2026-10-07", 2);
  assert.equal(fortnight.length, 14);
  assert.equal(fortnight[0], "2026-10-04");
  assert.equal(fortnight.at(-1), "2026-10-17");

  const months = yearMonths(2026);
  assert.equal(months.length, 12);
  assert.deepEqual(months.map((item) => item.title).slice(0, 3), ["1月", "2月", "3月"]);
  assert.equal(months[0].month, "2026-01");
  assert.equal(months[9].cells[0].slice(0, 7) <= "2026-10", true);  // 10 月的格子从整周开头起
  assert.ok(months.every((item) => item.cells.length % 7 === 0));

  // 加月份：跨年 + 短月收口
  assert.equal(addMonths("2026-10-11", 1), "2026-11-11");
  assert.equal(addMonths("2026-12-15", 1), "2027-01-15");
  assert.equal(addMonths("2026-01-31", 1), "2026-02-28");
  assert.equal(addMonths("2026-08-31", -1), "2026-07-31");
  assert.equal(addMonths("", 1), "");
});

test("task times parse, label and lay out incl. overlaps", () => {
  assert.deepEqual(timeToMinutes("14:00"), { start: 840, end: 900 });          // 只填开始 = 1 小时
  assert.deepEqual(timeToMinutes("09:00-10:30"), { start: 540, end: 630 });
  assert.equal(timeToMinutes(""), null);
  assert.equal(timeToMinutes("25:00"), null);
  assert.equal(minutesToLabel(840), "14:00");
  assert.equal(clockLabel(8 * 60), "8:00");
  assert.equal(timeLabel("08:00"), "8:00");
  assert.equal(timeLabel("09:00-10:30"), "9:00-10:30");
  assert.equal(timeLabel(""), "");

  const day = [
    task({ title: "晚的", time: "14:00-15:00" }),
    task({ title: "全天" }),
    task({ title: "早的", time: "09:00" }),
  ];
  assert.deepEqual(sortDayTasks(day).map((item) => item.title), ["全天", "早的", "晚的"]);
  assert.deepEqual(sortDayTasks().length, 0);

  // 三块互相重叠 → 三列；随后一块不重叠 → 重新从第一列开始
  const blocks = layoutBlocks([
    task({ title: "A", time: "09:00-10:00" }),
    task({ title: "B", time: "09:30-10:30" }),
    task({ title: "C", time: "09:45-10:15" }),
    task({ title: "D", time: "11:00-12:00" }),
    task({ title: "无时间" }),
  ]);
  assert.deepEqual(blocks.map((block) => block.task.title), ["A", "B", "C", "D"]);
  assert.deepEqual(blocks.map((block) => block.columns), [3, 3, 3, 1]);
  assert.deepEqual(blocks.map((block) => block.column), [0, 1, 2, 0]);
  assert.deepEqual(blocks[0], { ...blocks[0], start: 540, end: 600 });
  assert.equal(layoutBlocks([]).length, 0);

  assert.equal(firstBusyHour([task({ time: "08:30" })]), 7);   // 早于 7 点也留到 7 点折叠带上
  assert.equal(firstBusyHour([task({ time: "05:10" })]), 5);
  assert.equal(firstBusyHour([]), 7);
  assert.equal(firstBusyHour([task({ time: "21:00" })]), 7);
});

test("colors, anchor shifting and the header labels", () => {
  const colors = listColorMap([
    { name: "收集箱", color: "#3b82f6" }, { name: "心愿清单", color: "" }]);
  assert.deepEqual(colors, { 收集箱: "#3b82f6" });
  assert.equal(taskColor(task({ list_name: "收集箱" }), colors), "#3b82f6");
  assert.equal(taskColor(task({ list_name: "心愿清单" }), colors), "");
  assert.deepEqual(listColorMap(), {});

  const state = { days: 3, weeks: 2 };
  assert.equal(shiftAnchor("2026-10-11", "day", 1), "2026-10-12");
  assert.equal(shiftAnchor("2026-10-11", "week", -1), "2026-10-04");
  assert.equal(shiftAnchor("2026-10-11", "month", 1), "2026-11-11");
  assert.equal(shiftAnchor("2026-10-11", "year", 1), "2027-10-11");
  assert.equal(shiftAnchor("2026-10-11", "multiday", 1, state), "2026-10-14");
  assert.equal(shiftAnchor("2026-10-11", "multiweek", -1, state), "2026-09-27");
  assert.equal(shiftAnchor("2026-10-11", "agenda", 1), "2026-10-18");

  assert.deepEqual(CALENDAR_VIEWS.map((item) => item.id),
    ["year", "month", "week", "day", "agenda", "multiday", "multiweek"]);
  assert.deepEqual(MULTIDAY_OPTIONS, [2, 3, 5, 7]);
  assert.deepEqual(MULTIWEEK_OPTIONS, [2, 3, 4]);
  const defaults = defaultCalendarState("2026-10-11");
  assert.deepEqual(defaults, { view: "month", anchor: "2026-10-11", days: 3, weeks: 2, weekStart: 0, showDone: false });
  assert.equal(calendarViewLabel({ view: "month" }), "月视图");
  assert.equal(calendarViewLabel({ view: "multiday", days: 5 }), "多日视图（5 天）");
  assert.equal(calendarViewLabel({ view: "multiweek" }), "多周视图（2 周）");
  assert.equal(calendarViewLabel({ view: "nope" }), "月视图");   // 认不出的回月视图
  assert.equal(calendarTitle({ view: "year", anchor: "2026-10-11" }), "2026年");
  assert.equal(calendarTitle({ view: "month", anchor: "2026-10-11" }), "2026年10月");
  assert.equal(calendarTitle({ view: "day", anchor: "2026-10-11" }), "2026年10月");

  // tasksByDay 的 showDone 开关（日历默认不画已完成）
  const byDay = tasksByDay([
    task({ title: "干完的", date: TODAY, done: true }), task({ title: "没干完", date: TODAY })], { showDone: false });
  assert.deepEqual(byDay[TODAY].map((item) => item.title), ["没干完"]);
});
