// 「身份待确认」：dsh 从对话里提炼的关于我的事，先在这里过一遍。
// 采纳 = 后端写进身份字段或 02_记忆与自传；丢弃 = 只挪走卡片，四库一个字不动。
// 纯函数，不碰 DOM、不 import core.js，方便 node --test 直接跑。

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ESCAPES[char]);

const TARGET_TEXT = {
  field: (item) => `写入身份字段：${item.field_label || item.field || "未指定"}`,
  memory: () => "写进 02_记忆与自传",
};

export function identityDraftCount(items) {
  return Array.isArray(items) ? items.length : 0;
}

export function identityInboxMarkup(items) {
  const list = Array.isArray(items) ? items.filter((item) => item && item.name && item.text) : [];
  if (!list.length) return "";
  const rows = list.map((item) => {
    const target = (TARGET_TEXT[item.kind] || TARGET_TEXT.memory)(item);
    return `<li class="pillar-inbox-item">
        <p class="pillar-inbox-text">${escape(item.text)}</p>
        <p class="field-help">${escape(target)}</p>
        ${item.evidence ? `<p class="pillar-inbox-evidence">原话：${escape(item.evidence)}</p>` : ""}
        <div class="pillar-inbox-actions">
          <button type="button" class="button secondary" data-identity-draft-accept="${escape(item.name)}">采纳</button>
          <button type="button" class="text-button" data-identity-draft-reject="${escape(item.name)}">丢弃</button>
        </div>
      </li>`;
  }).join("");
  return `<section class="pillar-card pillar-inbox-card" aria-label="身份待确认">
      <p class="eyebrow">身份待确认 · ${list.length}</p>
      <p class="field-help">这些是「与神对话」从对话里提炼的、关于我的事；采纳后才会写进自我档案。</p>
      <ul class="pillar-inbox-list">${rows}</ul>
    </section>`;
}
