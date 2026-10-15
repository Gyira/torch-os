import { escapeHtml } from "./core.js";

function taskList(items, empty) {
  if (!items?.length) return `<p class="meta">${escapeHtml(empty)}</p>`;
  return `<ul class="journal-review-list">${items.map((item) => `<li>${escapeHtml(item.title)}${item.due_date ? `<span class="meta"> · 截止 ${escapeHtml(item.due_date)}</span>` : ""}</li>`).join("")}</ul>`;
}

export function reviewHtml(review) {
  return `<div class="journal-review-content"><span class="eyebrow">LOCAL DRAFT</span><h3>${escapeHtml(review.date)} · 本地复盘草稿</h3><p class="preserve-lines">${escapeHtml(review.summary || "这一天还没有记录，可以先写下一个片段。")}</p>${review.warning ? `<p class="inline-warning">${escapeHtml(review.warning)}</p>` : ""}${review.goal ? `<p class="meta">关联目标：${escapeHtml(review.goal.title)}</p>` : ""}<div class="form-grid"><div><h4>当天完成的任务</h4>${taskList(review.completed_tasks, "未找到这一天完成的任务记录。")}</div><div><h4>目前未完成的相关任务</h4>${taskList(review.open_tasks, "当前没有需要列出的未完成任务。")}</div></div><h4>可以继续想一想</h4><ul class="journal-review-list">${(review.reflection_prompts || []).map((prompt) => `<li>${escapeHtml(prompt)}</li>`).join("")}</ul>${review.next_action ? `<p class="preserve-lines"><strong>记录的下一步：</strong>${escapeHtml(review.next_action)}</p>` : ""}<p class="field-help">这是根据本地记录整理的草稿，不替你判断成败，也没有自动写入任务或记忆。未完成任务反映当前状态，并非历史状态快照。</p></div>`;
}

function statItem(label, value, unit) {
  return `<div class="rhythm-item"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(unit)}</small></div>`;
}

function overdueList(items) {
  if (!items?.length) return '<p class="meta">生成时刻没有已到期的未完成任务。</p>';
  return `<ul class="journal-review-list">${items.map((item) => `<li>${escapeHtml(item.title)}${item.due_date ? `<span class="meta"> · 截止 ${escapeHtml(item.due_date)}</span>` : ""}</li>`).join("")}</ul>`;
}

export function monthlyReviewHtml(review) {
  const range = review.range || {};
  const journal = review.journal || {};
  const tasks = review.tasks || {};
  const study = review.study || {};
  const habits = review.habits || {};
  const body = review.body || {};
  const subjects = Array.isArray(study.subjects) ? study.subjects : [];
  const completed = Array.isArray(tasks.completed_examples) ? tasks.completed_examples : [];
  const focus = Array.isArray(review.next_month_focus) ? review.next_month_focus : [];
  const period = `${range.start || ""} 至 ${range.end || ""}${range.partial ? "（本月至今）" : "（整月）"}`;
  return `<div class="journal-review-content"><span class="eyebrow">LOCAL DRAFT</span><h3>${escapeHtml(review.month || "")} · 月度复盘草稿</h3><p class="meta">统计窗口：${escapeHtml(period)}</p><p class="preserve-lines">${escapeHtml(review.summary || "")}</p>${review.warning ? `<p class="inline-warning">${escapeHtml(review.warning)}</p>` : ""}<div class="profile-rhythm">${statItem("日记天数", Number(journal.days_recorded) || 0, "天")}${statItem("完成任务", Number(tasks.completed) || 0, "项")}${statItem("学习时长", Number(study.minutes) || 0, "分钟")}${statItem("学习天数", Number(study.days_recorded) || 0, "天")}${statItem("习惯完成", Number(habits.done) || 0, "次")}${statItem("习惯跳过", Number(habits.skipped) || 0, "次")}${statItem("身体记录", Number(body.days_recorded) || 0, "天")}</div><div class="form-grid"><div><h4>完成的行动（最多 3 个例子）</h4>${completed.length ? `<ul class="journal-review-list">${completed.map((title) => `<li>${escapeHtml(title)}</li>`).join("")}</ul>` : '<p class="meta">没有按完成时间归到本月已完成任务的记录。</p>'}</div><div><h4>生成时刻已到期的未完成任务</h4>${overdueList(tasks.overdue_examples)}</div></div>${subjects.length ? `<h4>本月学习科目</h4><ul class="journal-review-list">${subjects.map((item) => `<li>${escapeHtml(item.title)}<span class="meta"> · ${escapeHtml(Number(item.minutes) || 0)} 分钟</span></li>`).join("")}</ul>` : ""}<h4>下月关注点（草稿）</h4><ul class="journal-review-list">${focus.map((item) => `<li>${escapeHtml(item.text || "")}</li>`).join("")}</ul><p class="field-help">${escapeHtml(review.next_month_note || "关注点只是根据本月记录的草稿：不会自动创建任务或写记忆。")}</p><h4>可以继续想一想</h4><ul class="journal-review-list">${(review.reflection_prompts || []).map((prompt) => `<li>${escapeHtml(prompt)}</li>`).join("")}</ul><p class="field-help">这是本地草稿：只汇总已保存记录和生成时的任务状态，不含评价、没有写入任务或记忆，也不替代你自己的判断。未记录不代表没有发生。</p></div>`;
}
