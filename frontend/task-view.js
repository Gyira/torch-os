// Pure task filtering so the due views stay testable without a DOM.
// ``today`` is a Shanghai calendar day (YYYY-MM-DD).

export function filterTasks(tasks, filter, today) {
  const rows = Array.isArray(tasks) ? tasks : [];
  const open = rows.filter((task) => ["todo", "in_progress"].includes(task.status));
  if (filter === "all") return rows;
  if (filter === "completed") return rows.filter((task) => task.status === "completed");
  if (filter === "due_today") return open.filter((task) => task.due_date === today);
  if (filter === "overdue") return open.filter((task) => Boolean(task.due_date) && task.due_date < today);
  return open;
}

export const TASK_FILTERS = ("open, due_today, overdue, completed, all").split(", ");

export const TASK_SORTS = ("default, priority, due, updated, manual").split(", ");

const PRIORITY_RANK = { urgent: 0, high: 1, medium: 2, low: 3 };
const NO_DUE = "9999-12-31";

function priorityRank(task) {
  return PRIORITY_RANK[task?.priority] ?? 4;
}

function dueKey(task) {
  return task?.due_date || NO_DUE;
}

function byId(left, right) {
  return Number(left?.id ?? 0) - Number(right?.id ?? 0);
}

// Read-only ordering for the task list. ``default`` keeps the server order
// (status → due → manual slot); ``manual`` reads the stored sort_order slots.
export function orderTasks(tasks, mode = "default") {
  const rows = Array.isArray(tasks) ? [...tasks] : [];
  if (mode === "priority") {
    return rows.sort((left, right) => priorityRank(left) - priorityRank(right)
      || dueKey(left).localeCompare(dueKey(right)) || byId(left, right));
  }
  if (mode === "due") {
    return rows.sort((left, right) => dueKey(left).localeCompare(dueKey(right))
      || priorityRank(left) - priorityRank(right) || byId(left, right));
  }
  if (mode === "updated") {
    return rows.sort((left, right) => String(right?.updated_at || "").localeCompare(String(left?.updated_at || ""))
      || byId(left, right));
  }
  if (mode === "manual") {
    return rows.sort((left, right) => (Number(left?.sort_order) || 0) - (Number(right?.sort_order) || 0)
      || byId(left, right));
  }
  return rows;
}

export function moveTask(tasks, taskId, offset) {
  const rows = Array.isArray(tasks) ? [...tasks] : [];
  const index = rows.findIndex((task) => String(task?.id) === String(taskId));
  const target = index + Number(offset);
  if (index < 0 || !Number.isInteger(target) || target < 0 || target >= rows.length || target === index) return rows;
  [rows[index], rows[target]] = [rows[target], rows[index]];
  return rows;
}
