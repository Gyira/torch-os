import test from "node:test";
import assert from "node:assert/strict";

import { identityDraftCount, identityInboxMarkup } from "../identity-inbox.js";

// 「身份待确认」卡片：dsh 投的候选取在这里，采纳才进自我档案。
// 这些用例只跑纯函数，不碰 DOM、不发请求。

const DRAFTS = [
  { name: "20261027-220000-field-ab12cd34.md", kind: "field", field: "name",
    field_label: "称呼", text: "称呼我小林", evidence: "我说：以后叫我小林", created: "2026-10-27T22:00:00+08:00" },
  { name: "20261027-220100-memory-9f8e7d6c.md", kind: "memory", field: "", field_label: "",
    text: "我在铁路工务段当线路工", evidence: "", created: "2026-10-27T22:01:00+08:00" },
];

test("no drafts renders nothing at all", () => {
  assert.equal(identityInboxMarkup([]), "");
  assert.equal(identityInboxMarkup(undefined), "");
  assert.equal(identityDraftCount(undefined), 0);
  assert.equal(identityDraftCount(DRAFTS), 2);
});

test("every draft gets a target and two actions", () => {
  const html = identityInboxMarkup(DRAFTS);
  assert.match(html, /身份待确认 · 2/);
  assert.match(html, /写入身份字段：称呼/);
  assert.match(html, /写进 02_记忆与自传/);
  assert.match(html, /data-identity-draft-accept="20261027-220000-field-ab12cd34\.md"/);
  assert.match(html, /data-identity-draft-reject="20261027-220100-memory-9f8e7d6c\.md"/);
  assert.match(html, /原话：我说：以后叫我小林/);
});

test("draft text is escaped, never injected as markup", () => {
  const html = identityInboxMarkup([
    { name: "x.md", kind: "memory", text: "<img src=x onerror=alert(1)>", evidence: "<script>", field_label: "" },
  ]);
  assert.equal(html.includes("<img"), false);
  assert.equal(html.includes("<script>"), false);
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
});

test("malformed drafts are skipped instead of rendering empty rows", () => {
  const html = identityInboxMarkup([null, { name: "", text: "没有名字" }, { name: "ok.md", text: "" }]);
  assert.equal(html, "");
});
