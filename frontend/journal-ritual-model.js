// 日记闭环的面板：纯函数（不碰 DOM），node 测试直接加载。
// 面板本身在 journal-review.js；这里只负责「数据长什么样」和「要提交什么」。

const esc = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[character]));

const MAX_ANSWER = 2000;
const MAX_FREE = 8000;

/** 取某个二级标题下的正文；没有这一节就返回空串。 */
export function sectionText(content, heading) {
  const lines = String(content || "").replace(/\r\n/g, "\n").split("\n");
  const out = [];
  let inside = false;
  for (const line of lines) {
    const match = /^##\s+(.+?)\s*$/.exec(line);
    if (match) {
      inside = match[1] === heading;
      continue;
    }
    if (inside) out.push(line);
  }
  return out.join("\n").trim();
}

/** 已有日记里的「我想说的」，保存前回填，避免整篇替换时丢掉手写内容。 */
export function existingFreeText(content) {
  return sectionText(content, "我想说的");
}

function signalList(digest) {
  const signals = digest?.signals || [];
  if (!signals.length) return '<p class="field-help">今天没有留下可用的记录。</p>';
  return `<ul class="field-help">${signals.map((line) => `<li>${esc(line)}</li>`).join("")}</ul>`;
}

function questionFields(questions, values) {
  if (!questions?.length) return '<p class="field-help">今天没有想问的。</p>';
  return questions.map((item, index) => {
    const value = values?.answers?.[index] ?? "";
    return `<div class="field">
      <label for="jr-q-${index}">${esc(item.prompt)}</label>
      <textarea id="jr-q-${index}" data-jr-answer="${index}" rows="3" maxlength="${MAX_ANSWER}"
        placeholder="一两句就行，没想的就空着">${esc(value)}</textarea>
      <p class="field-help">${esc(item.why || "")}</p>
    </div>`;
  }).join("");
}

/** 盘点 + 三个问题 + 自由写；values 用来从预览返回时回填。 */
export function reviewMarkup(payload, values = {}) {
  const digest = payload?.digest || {};
  const questions = payload?.questions || [];
  const note = payload?.note || {};
  const free = values.freeText ?? existingFreeText(note.content || "");
  const warning = note.exists
    ? '<p class="inline-warning">这篇已经有内容：保存会整篇替换，旧版本会先备份。</p>'
    : "";

  return `<div class="journal-review">
    <p class="field-help">${esc(digest.day || "")} · ${esc(digest.weekday || "")}</p>
    <p class="eyebrow">今天</p>
    <p><strong>${esc(digest.headline || "今天还没有留下痕迹")}</strong></p>
    ${signalList(digest)}
    ${warning}

    <p class="eyebrow" style="margin-top:18px">问三个问题</p>
    ${questionFields(questions, { ...values, answers: values.answers || questions.map(() => "") })}

    <div class="field">
      <label for="jr-free">还想说点什么</label>
      <textarea id="jr-free" rows="4" maxlength="${MAX_FREE}"
        placeholder="今天最想留下的一句话">${esc(free)}</textarea>
    </div>

    <div class="form-grid">
      <div class="field"><label for="jr-next">下一步</label>
        <input id="jr-next" maxlength="200" placeholder="明天最小的一步" value="${esc(values.nextStep || "")}" /></div>
      <div class="field"><label for="jr-mood">心情（可选）</label>
        <input id="jr-mood" maxlength="20" placeholder="平静 / 清醒 / 累" value="${esc(values.mood || "")}" /></div>
    </div>

    <label class="check-label"><input type="checkbox" id="jr-sealed"${values.sealed ? " checked" : ""} />
      这就算写完（封存，不再改）</label>

    <div class="form-actions" style="margin-top:18px">
      <button type="button" class="button primary" data-jr-action="compose">生成草稿</button>
      ${values.chatAvailable ? '<button type="button" class="button secondary" data-jr-action="chat-open">聊着写（约 5 分钟）</button>' : ""}
      <span class="status-message" id="jr-status" role="status" aria-live="polite"></span>
    </div>
  </div>`;
}

/** 预览：原文直接给出来，保存前能看清算不算一篇。 */
export function previewMarkup(content, day = "") {
  return `<div class="journal-preview">
    <p class="field-help">下面是会写进 <code>00_人生路/01_日记/${esc(day)}.md</code> 的全部内容。</p>
    <pre class="source-content preserve-lines">${esc(content || "")}</pre>
    <div class="form-actions" style="margin-top:18px">
      <button type="button" class="button secondary" data-jr-action="back">返回修改</button>
      <button type="button" class="button primary" data-jr-action="write">保存到日记</button>
      <span class="status-message" id="jr-status" role="status" aria-live="polite"></span>
    </div>
  </div>`;
}

/** 写完之后的回流：这段话要不要顺手存成一条反思/心得。 */
export function distillMarkup(candidates) {
  const items = (candidates || []).slice(0, 2);
  if (!items.length) return "";
  return `<div class="journal-distill">
    <p class="eyebrow">写完了</p>
    <p class="field-help">这段话要不要顺手存成一条${items[0].kind === "insight" ? "心得" : "反思"}？存了就不用再回来找。</p>
    ${items.map((item, index) => `<div class="field">
      <label>${esc(item.title)}</label>
      <p class="field-help">${esc(item.body)}</p>
      <button type="button" class="button secondary" data-jr-action="distill" data-jr-index="${index}">
        存到${esc(item.kind === "insight" ? "心得" : "反思")}（${esc(item.path.split("/").slice(-2, -1)[0] || "章节")}）
      </button>
    </div>`).join("")}
    <div class="form-actions" style="margin-top:14px">
      <button type="button" class="button" data-jr-action="done">今天先这样</button>
      <span class="status-message" id="jr-status" role="status" aria-live="polite"></span>
    </div>
  </div>`;
}

/** 聊着写：一次只显示一句，回答框在下面；历史折叠。 */
export function chatMarkup(messages, { draftable = true } = {}) {
  const list = messages || [];
  const last = list[list.length - 1];
  const history = list.slice(0, -1).slice(-6);
  const speaker = (role) => (role === "user" ? "我" : "搭档");
  return `<div class="journal-chat">
    <p class="eyebrow">聊着写</p>
    ${last ? `<div class="field">
      <label>${esc(speaker(last.role))}</label>
      <p class="preserve-lines field-help">${esc(last.text)}</p>
    </div>` : ""}
    <div class="field">
      <label for="jr-chat-input">我</label>
      <textarea id="jr-chat-input" rows="4" maxlength="2000"
        placeholder="随便说，不用组织语言"></textarea>
    </div>
    <div class="form-actions">
      <button type="button" class="button primary" data-jr-action="chat-send">发出去</button>
      ${draftable ? '<button type="button" class="button secondary" data-jr-action="chat-draft">聊够了，写出来</button>' : ""}
      <button type="button" class="text-button" data-jr-action="chat-back">回到问题卡</button>
    </div>
    <span class="status-message" id="jr-status" role="status" aria-live="polite"></span>
    ${history.length ? `<details><summary>前面的对话</summary>${history.map((item) => (
      `<p class="field-help">${esc(speaker(item.role))}：${esc(item.text)}</p>`)).join("")}</details>` : ""}
  </div>`;
}

/** 表单 → /api/journal/compose 的请求体；空回答直接丢掉。 */
export function composeBody(form, day) {
  const answers = (form?.answers || [])
    .map((item) => ({ prompt: String(item?.prompt || "").trim(), answer: String(item?.answer || "").trim() }))
    .filter((item) => item.answer);
  return {
    day,
    free_text: String(form?.freeText || "").trim(),
    answers,
    next_step: String(form?.nextStep || "").trim(),
    summary: String(form?.summary || "").trim(),
    mood: String(form?.mood || "").trim(),
    sealed: Boolean(form?.sealed),
    mode: "template",
  };
}
