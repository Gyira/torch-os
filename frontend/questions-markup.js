// 主动提问的纯渲染：一张卡片，一个问题，三个出口（记下来 / 跳过 / 别再问这个）。
// 答完当天不自动出下一题，但给一个「再问一个」— 我想多聊几个的时候不用等明天。
// 不 import core.js，node --test 可以直接跑。

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ESCAPES[char]);

export function questionCardMarkup(question) {
  if (!question || !question.id || !question.prompt) return "";
  const placeholder = question.placeholder || "";
  const why = String(question.why || "").trim();
  const note = question.queued
    ? (why ? `<p class="question-why">为什么问这个：${escape(why)}</p>` : "")
    : (question.hint ? `<p class="question-hint">${escape(question.hint)}</p>` : "");
  return `<section class="question-card" data-question-id="${escape(question.id)}" aria-label="导师提问">
      <div class="question-head">
        <p class="question-kicker">导师提问${question.queued ? " · 从四库里挑的" : ""}</p>
        <span class="question-count">${Number(question.remaining) > 1 ? `还有 ${Number(question.remaining)} 个` : ""}</span>
      </div>
      <h3 class="question-prompt">${escape(question.prompt)}</h3>
      ${note}
      <textarea class="question-input" data-question-input rows="3" placeholder="${escape(placeholder)}" aria-label="${escape(question.prompt)}"></textarea>
      <div class="question-actions">
        <button type="button" class="button primary" data-question-action="answer">记下来</button>
        <button type="button" class="text-button" data-question-action="skip">跳过</button>
        <button type="button" class="text-button" data-question-action="decline">别再问这个</button>
      </div>
    </section>`;
}

export function questionDoneMarkup({ exhausted = false } = {}) {
  if (exhausted) {
    return `<section class="question-card question-done" data-question-done>
        <p class="question-kicker">导师提问</p>
        <p class="question-done-text">题库问完了——去「与神对话」随便聊，我边聊边记。</p>
      </section>`;
  }
  return `<section class="question-card question-done" data-question-done>
      <p class="question-kicker">导师提问</p>
      <p class="question-done-text">今天的问题答完了。还想继续聊的话，我这儿还有。</p>
      <div class="question-actions">
        <button type="button" class="button secondary" data-question-action="another">再问一个</button>
      </div>
    </section>`;
}
