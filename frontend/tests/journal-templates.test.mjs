import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

async function loadModel() {
  const context = vm.createContext({});
  const filename = path.join(frontend, "journal-templates.js");
  const module = new vm.SourceTextModule(await readFile(filename, "utf8"), {
    context, identifier: filename,
  });
  await module.link(() => { throw new Error("no imports expected"); });
  await module.evaluate();
  return module.namespace;
}

// 2026-10-31 01:31（Asia/Shanghai）：晚间复盘推荐。
const NIGHT = new Date("2026-10-30T17:31:00Z");
const MORNING = new Date("2026-10-30T23:00:00Z");
const EVENING = new Date("2026-10-31T12:00:00Z");
const SUNDAY_EVENING = new Date("2026-11-01T12:00:00Z");
const DAY = "2026-10-31";

test("八套内置模板按日常 → 复盘 → 情绪 → 生活的顺序排列", async () => {
  const model = await loadModel();
  assert.deepEqual(
    Array.from(model.JOURNAL_TEMPLATES, (template) => [template.id, template.name]),
    [
      ["daily", "每日记录"],
      ["morning", "晨间日记"],
      ["evening", "晚间复盘"],
      ["gratitude", "感恩日记"],
      ["question", "每日一问"],
      ["weekly", "周回顾"],
      ["emotion", "情绪梳理"],
      ["reading", "读书笔记"],
    ],
  );
  assert.deepEqual(
    Array.from(model.TEMPLATE_CATEGORIES, (category) => category.label),
    ["日常", "复盘", "情绪", "生活"],
  );
  for (const template of model.JOURNAL_TEMPLATES) {
    assert.ok(template.description.length > 0, `${template.id} 缺少说明`);
    assert.ok(template.blocks.length > 0, `${template.id} 缺少正文块`);
  }
});

test("日期、星期、时间与周数按日记那一天解析", async () => {
  const model = await loadModel();
  const variables = model.templateVariables(DAY, { now: NIGHT });
  assert.equal(variables.date, "2026年10月31日");
  assert.equal(variables.weekday, "周六");
  assert.equal(variables.time, "01:31");
  assert.equal(variables.week, "44");
  assert.equal(variables.yearProgress, "83%");
  assert.equal(model.isoWeekNumber(new Date("2026-01-01T00:00:00Z")), 1);
  assert.equal(model.isoWeekNumber(new Date("2027-01-01T00:00:00Z")), 53);
});

test("“换一题”只改题库偏移，同一天保持同一题", async () => {
  const model = await loadModel();
  const base = model.templateVariables(DAY, { now: NIGHT });
  const shifted = model.templateVariables(DAY, { now: NIGHT, questionShift: 1 });
  const wrapped = model.templateVariables(DAY, { now: NIGHT, questionShift: 30 });
  assert.notEqual(base.question, shifted.question);
  assert.equal(base.question, wrapped.question);
  assert.equal(model.templateVariables("2026-11-01", { now: NIGHT }).question,
    model.templateVariables("2026-11-01", { now: EVENING }).question);
});

test("模板正文编译成可读的 Markdown：加粗小标题、空行、清单与分隔线", async () => {
  const model = await loadModel();
  const variables = model.templateVariables(DAY, { now: NIGHT });
  const evening = model.resolveTemplate(model.templateById("evening"), variables);
  assert.equal(evening.title, "复盘 · 2026年10月31日");
  assert.match(evening.body, /\*\*✅ 今天完成了\*\*：/);
  assert.match(evening.body, /\*\*📅 明天的安排\*\*：/);
  assert.match(evening.body, /- \[ \]\n- \[ \]/);
  assert.ok(evening.body.includes("---"));
  assert.ok(evening.body.indexOf("今天完成了") < evening.body.indexOf("做得好的地方"));
  assert.ok(evening.body.indexOf("做得好的地方") < evening.body.indexOf("明天的安排"));

  const daily = model.resolveTemplate(model.templateById("daily"), variables);
  assert.equal(daily.title, "2026年10月31日 周六");
  assert.match(daily.body, /\*\*📖 今天的故事\*\*：/);
  assert.match(daily.body, /\*\*🌙 写给今天的一句话\*\*：/);

  const reading = model.resolveTemplate(model.templateById("reading"), variables);
  assert.match(reading.body, /\*\*📚 书名\*\*：/);
  assert.match(reading.body, /\*\*🖋️ 作者\*\*：/);
});

test("套用后的光标落在第一个可填写的空行", async () => {
  const model = await loadModel();
  assert.equal(model.firstEmptyLineOffset("abc"), 3);
  assert.equal(model.firstEmptyLineOffset("\n\nabc"), 0);
  assert.equal(model.firstEmptyLineOffset("a\n\nb"), 2);
  const variables = model.templateVariables(DAY, { now: NIGHT });
  const body = model.resolveTemplate(model.templateById("daily"), variables).body;
  assert.ok(model.firstEmptyLineOffset(body) > 0);
});

test("日记正文把模板标题接成 H1，正文放在后面", async () => {
  const model = await loadModel();
  assert.equal(
    model.journalTemplateBody("复盘 · 2026年10月31日", "**✅ 今天完成了**：\n"),
    "# 复盘 · 2026年10月31日\n\n**✅ 今天完成了**：\n",
  );
  assert.equal(model.journalTemplateBody("", ""), "");
});

test("只有当天日期骨架的日记才算空白，用户写的 H1 不会被覆盖", async () => {
  const model = await loadModel();
  assert.equal(model.isBlankJournalBody("", "2026-10-31"), true);
  assert.equal(model.isBlankJournalBody("# 2026-10-31（第44周，周六）\n\n", "2026-10-31"), true);
  // 后端骨架用点号日期（journal_note.DEFAULT_TEMPLATE）。
  assert.equal(model.isBlankJournalBody("# 2026.10.31（第44周，周六）\n", "2026-10-31"), true);
  assert.equal(model.isBlankJournalBody("# 今天很累", "2026-10-31"), false);
  assert.equal(model.isBlankJournalBody("# 2026-10-31 今天很累", "2026-10-31"), false);
  assert.equal(model.isBlankJournalBody("# 2026-10-31（第44周，周六）\n\n写了两句", "2026-10-31"), false);
  assert.equal(model.isBlankJournalBody("# 2026-10-30（第44周，周五）", "2026-10-31"), false);
});

test("推荐规则：时段看当下、星期看日记日期，补写时不看时段", async () => {
  const model = await loadModel();
  const morning = model.templateById("morning");
  const evening = model.templateById("evening");
  const weekly = model.templateById("weekly");
  assert.equal(model.scoreTemplate(morning, { day: DAY, now: MORNING }), 1);
  assert.equal(model.scoreTemplate(morning, { day: DAY, now: NIGHT }), null);
  assert.equal(model.scoreTemplate(evening, { day: DAY, now: NIGHT }), 1);
  assert.equal(model.scoreTemplate(evening, { day: DAY, now: EVENING }), 1);
  assert.equal(model.scoreTemplate(weekly, { day: "2026-11-01", now: EVENING }), 1);
  assert.equal(model.scoreTemplate(weekly, { day: DAY, now: EVENING }), null);
  assert.equal(model.scoreTemplate(model.templateById("daily"), { day: DAY, now: NIGHT }), null);
  // 补写昨天：时段条件整类跳过，只有时段规则的模板就不推荐。
  assert.equal(model.scoreTemplate(morning, { day: "2026-10-30", now: MORNING }), null);
});

test("面板排序把推荐的放最前，同分保持内置顺序", async () => {
  const model = await loadModel();
  const ranked = model.rankedTemplates({ day: DAY, now: NIGHT });
  assert.equal(ranked[0].template.id, "evening");
  assert.equal(ranked[0].recommended, true);
  assert.deepEqual(
    Array.from(ranked.slice(1), (item) => item.template.id),
    ["daily", "morning", "gratitude", "question", "weekly", "emotion", "reading"],
  );
  const sunday = model.rankedTemplates({ day: "2026-11-01", now: SUNDAY_EVENING });
  assert.deepEqual(
    Array.from(sunday.slice(0, 2), (item) => item.template.id),
    ["evening", "weekly"],
  );
});

test("建议条：推荐的在前，最近用过的其次，最多五个且不重复", async () => {
  const model = await loadModel();
  const plain = model.suggestTemplates({ day: DAY, now: NIGHT });
  assert.deepEqual(
    Array.from(plain, (item) => item.template.id),
    ["evening", "daily", "morning", "gratitude", "question"],
  );
  const withRecent = model.suggestTemplates({ day: DAY, now: NIGHT, recent: ["question", "evening"] });
  assert.deepEqual(
    Array.from(withRecent, (item) => item.template.id),
    ["evening", "question", "daily", "morning", "gratitude"],
  );
  const limited = model.suggestTemplates({ day: DAY, now: NIGHT, limit: 3 });
  assert.equal(limited.length, 3);
});

test("面板筛选：全部、推荐与有模板的分类，且筛选项只在有模板时出现", async () => {
  const model = await loadModel();
  const ranked = model.rankedTemplates({ day: DAY, now: NIGHT });
  assert.deepEqual(
    Array.from(model.templateFilters(ranked), (filter) => filter.key),
    ["all", "recommended", "daily", "review", "mood", "life"],
  );
  assert.deepEqual(
    Array.from(model.filterTemplates(ranked, { filter: "recommended" }), (item) => item.template.id),
    ["evening"],
  );
  assert.deepEqual(
    Array.from(model.filterTemplates(ranked, { filter: "mood" }), (item) => item.template.id),
    ["gratitude", "emotion"],
  );
  const noRecommendation = model.rankedTemplates({ day: "2026-10-29", now: new Date("2026-10-29T04:00:00Z") });
  assert.equal(model.templateFilters(noRecommendation).some((filter) => filter.key === "recommended"), false);
});

test("搜索匹配名称、说明与分类，多个词按“且”计算", async () => {
  const model = await loadModel();
  assert.deepEqual(
    Array.from(model.JOURNAL_TEMPLATES.filter((template) => model.templateMatchesQuery(template, "复盘")), (template) => template.id),
    ["evening", "weekly"],
  );
  assert.deepEqual(
    Array.from(model.JOURNAL_TEMPLATES.filter((template) => model.templateMatchesQuery(template, "日常 记录")), (template) => template.id),
    ["daily"],
  );
  assert.equal(model.templateMatchesQuery(model.templateById("reading"), "读书"), true);
  assert.equal(model.templateMatchesQuery(model.templateById("reading"), "不存在的"), false);
  assert.equal(model.templateMatchesQuery(model.templateById("reading"), "   "), true);
});

test("建议条与面板标记使用可访问的按钮、推荐徽章与颜色类", async () => {
  const model = await loadModel();
  const ranked = model.rankedTemplates({ day: DAY, now: NIGHT });
  const chips = model.templateStripMarkup(model.suggestTemplates({ day: DAY, now: NIGHT }));
  assert.match(chips, /data-template-id="evening"/);
  assert.match(chips, /晚间复盘/);
  assert.match(chips, /template-recommend/);
  assert.match(chips, /data-accent="purple"/);
  assert.match(chips, /aria-label="套用模板：晚间复盘，推荐"/);

  const row = model.templateRowMarkup(ranked[0].template, true);
  assert.match(row, /class="template-row"/);
  assert.match(row, /aria-label="预览模板：晚间复盘，回看今天的得与失，把明天安排妥当，推荐"/);
  assert.match(row, /template-badge is-large/);

  const filters = model.templateFilterMarkup(model.templateFilters(ranked), "review");
  assert.match(filters, /data-template-filter="review" aria-pressed="true"/);
  assert.match(filters, /data-template-filter="all" aria-pressed="false"/);
});

test("空结果提示会转义搜索词", async () => {
  const model = await loadModel();
  assert.match(model.templateEmptyMarkup(""), /这里还没有模板/);
  const html = model.templateEmptyMarkup('<script>alert("x")</script>');
  assert.ok(!html.includes("<script>"));
  assert.match(html, /&lt;script&gt;/);
});

test("编辑器页面包含建议条、模板面板与预览步的挂载点", async () => {
  const html = await readFile(path.join(frontend, "index.html"), "utf8");
  for (const id of [
    "note-editor-template-strip", "template-strip-chips", "template-open-all",
    "template-strip-dismiss", "note-editor-template-sheet", "template-search",
    "template-filters", "template-results", "template-preview-step",
    "template-preview-hint", "template-preview-back", "template-preview-use",
    "template-preview-shuffle", "note-editor-template-undo",
    "note-editor-template-undo-text", "note-editor-template-undo-btn",
  ]) {
    assert.match(html, new RegExp(`id="${id}"`), `缺少 #${id}`);
  }
  assert.match(html, /从模板开始/);
  assert.match(html, /选择模板/);
  assert.match(html, /使用这个模板/);
});
