import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

// 章节轨分组是纯函数：不启动浏览器，也不读取个人数据。
// pillars.js 顶部只有 import/常量/函数声明，所以用合成模块顶替依赖，
// 只执行 buildRailItems 需要的部分。
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const LIFE_GROUPS = [
  { key: "insight", title: "心得体会", chapters: ["insight_life", "insight_work", "insight_study"] },
  { key: "review", title: "反思复盘", chapters: ["reflection", "review_week", "review_month"] },
  { key: "goal", title: "目标", chapters: ["goal_year", "goal_quarter", "goal_month"] },
  { key: "project", title: "项目", chapters: ["project_plan", "work_review_project"] },
];

// 与 /api/pillars 的 life 章节顺序一致；只测排序，不关心 path/type。
const LIFE_CHAPTERS = [
  ["journal", "日记"],
  ["task", "任务与待办"],
  ["reflection", "反思"],
  ["insight_life", "生活心得"],
  ["insight_work", "工作心得"],
  ["insight_study", "学习心得"],
  ["review_week", "周复盘"],
  ["review_month", "月复盘"],
  ["goal_year", "年度目标"],
  ["goal_quarter", "季度目标"],
  ["goal_month", "月度目标"],
  ["habit", "习惯追踪"],
  ["project_plan", "项目计划"],
  ["work_review_project", "项目复盘"],
  ["work_review_meeting", "会议收获"],
  ["work_review_pitfall", "踩坑记录"],
].map(([key, title]) => ({ key, title, path: `00_人生路/${key}`, default_type: "note" }));

const IMPORTS = {
  "./core.js": ["$", "api", "confirmAction", "escapeHtml", "formatRelativeDate", "state", "toast"],
  "./pillar-markdown.js": ["renderMarkdownDocument"],
  "./pillar-ops.js": ["bindOpsEvents", "mountOps"],
  "./pillar-habits.js": ["bindHabitEvents", "mountHabits"],
  "./pillar-steward.js": ["openSteward"],
  "./identity-inbox.js": ["identityInboxMarkup"],
  "./archive-markup.js": ["archiveMarkup"],
  "./journal-model.js": ["shanghaiDay"],
  "./journal-ritual.js": ["openJournalReview"],
  "./note-editor.js": ["deleteNoteFile", "initNoteEditor", "invalidateChapterCache", "openNoteEditor"],
  "./notebook-create.js": ["initNotebookCreate", "openNotebookCreateDialog"],
  "./share-link.js": ["initShareLink", "openDeepSeekAsk", "openShareLinkDialog"],
  "./roles.js": ["openRoleDrawer"],
  "./notebook-model.js": [
    "DEFAULT_LIFE_WRITE_CHAPTER", "buildNotebookCards", "canDeleteNotebook",
    "customNotebookName", "customNotebookPillar", "defaultWriteChapter",
    "greetingForHour", "journalHeading", "knowledgeHeroState", "lifeHeroState",
    "mergeNotesByRecency", "noteCard", "noteCards", "notebookById",
    "notebookForChapter", "notebookTotal", "notebooksFor", "subchaptersFor",
  ],
  "./ops-ai.js": ["initOpsAI", "loadOpsAI"],
  "./task-open.js": ["createObsidianNote", "isObsidianHost", "obsidianOpenUri", "openNoteMessage", "openTaskDocument"],
};

let pillarsPromise = null;

async function loadPillars() {
  if (!pillarsPromise) {
    pillarsPromise = (async () => {
      const context = vm.createContext({});
      const stubs = new Map();
      for (const [specifier, names] of Object.entries(IMPORTS)) {
        stubs.set(specifier, new vm.SyntheticModule(names, function () {
          for (const name of names) this.setExport(name, () => {});
        }, { context }));
      }
      const filename = path.join(frontend, "pillars.js");
      const module = new vm.SourceTextModule(await readFile(filename, "utf8"), { context, identifier: filename });
      await module.link((specifier) => {
        const stub = stubs.get(specifier);
        if (!stub) throw new Error(`pillars.js 出现了测试未覆盖的 import：${specifier}`);
        return stub;
      });
      await module.evaluate();
      return module.namespace;
    })();
  }
  return pillarsPromise;
}

const itemKeys = (items) => Array.from(items, (item) => `${item.type}:${item.key}`);
const chapterKeys = (group) => Array.from(group.chapters, (chapter) => chapter.key);

test("life groups appear where their first chapter appears and chapter order is kept", async () => {
  const { buildRailItems } = await loadPillars();
  const rail = buildRailItems(LIFE_CHAPTERS, LIFE_GROUPS);

  assert.deepEqual(itemKeys(rail.items), [
    "chapter:journal",
    "chapter:task",
    "group:review",
    "group:insight",
    "group:goal",
    "chapter:habit",
    "group:project",
    "chapter:work_review_meeting",
    "chapter:work_review_pitfall",
  ]);

  const groups = new Map(Array.from(rail.items).filter((item) => item.type === "group")
    .map((item) => [item.key, item]));
  assert.deepEqual(Array.from(groups.keys()), ["review", "insight", "goal", "project"]);
  assert.equal(groups.get("review").title, "反思复盘");
  assert.deepEqual(chapterKeys(groups.get("review")), ["reflection", "review_week", "review_month"]);
  assert.deepEqual(chapterKeys(groups.get("insight")), ["insight_life", "insight_work", "insight_study"]);
  assert.deepEqual(chapterKeys(groups.get("goal")), ["goal_year", "goal_quarter", "goal_month"]);
  assert.deepEqual(chapterKeys(groups.get("project")), ["project_plan", "work_review_project"]);
  // 分组内章节不重复出现在一级。
  assert.equal(rail.items.filter((item) => item.type === "chapter").length, 5);
  assert.equal(rail.items.filter((item) => item.type === "group").length, 4);
});

test("groupFirstKeys and chapterGroupKeys describe the first occurrence of every group", async () => {
  const { buildRailItems } = await loadPillars();
  const rail = buildRailItems(LIFE_CHAPTERS, LIFE_GROUPS);

  assert.deepEqual({ ...rail.groupFirstKeys }, {
    review: "reflection",
    insight: "insight_life",
    goal: "goal_year",
    project: "project_plan",
  });
  assert.equal(rail.chapterGroupKeys.reflection, "review");
  assert.equal(rail.chapterGroupKeys.review_month, "review");
  assert.equal(rail.chapterGroupKeys.insight_study, "insight");
  assert.equal(rail.chapterGroupKeys.goal_quarter, "goal");
  assert.equal(rail.chapterGroupKeys.work_review_project, "project");
  assert.equal(rail.chapterGroupKeys.journal, undefined);
  assert.equal(rail.chapterGroupKeys.habit, undefined);
});

test("pillars without groups keep the flat rail in manifest order", async () => {
  const { buildRailItems } = await loadPillars();
  for (const groups of [undefined, null, []]) {
    const rail = buildRailItems(LIFE_CHAPTERS, groups);
    assert.equal(rail.items.length, LIFE_CHAPTERS.length);
    assert.ok(rail.items.every((item) => item.type === "chapter"));
    assert.deepEqual(itemKeys(rail.items), LIFE_CHAPTERS.map((chapter) => `chapter:${chapter.key}`));
    assert.deepEqual({ ...rail.groupFirstKeys }, {});
    assert.deepEqual({ ...rail.chapterGroupKeys }, {});
  }
});

test("unknown chapter keys and empty groups are omitted instead of rendering dead rows", async () => {
  const { buildRailItems } = await loadPillars();
  const chapters = [
    { key: "a", title: "A" },
    { key: "b", title: "B" },
    { key: "c", title: "C" },
  ];
  const groups = [
    { key: "empty", title: "空分组", chapters: ["missing"] },
    { key: "ab", title: "AB", chapters: ["b", "missing"] },
    { key: "blank", title: "没有章节" },
  ];
  const rail = buildRailItems(chapters, groups);

  assert.deepEqual(itemKeys(rail.items), ["chapter:a", "group:ab", "chapter:c"]);
  const group = rail.items.find((item) => item.type === "group");
  assert.deepEqual(chapterKeys(group), ["b"]);
  assert.deepEqual({ ...rail.groupFirstKeys }, { ab: "b" });
  assert.equal(rail.chapterGroupKeys.b, "ab");
  assert.equal(rail.chapterGroupKeys.a, undefined);
});

test("a group is ordered by the manifest, not by the order inside the groups payload", async () => {
  const { buildRailItems } = await loadPillars();
  const chapters = [
    { key: "c", title: "C" },
    { key: "a", title: "A" },
    { key: "b", title: "B" },
    { key: "d", title: "D" },
  ];
  const rail = buildRailItems(chapters, [{ key: "g", title: "G", chapters: ["b", "a"] }]);

  assert.deepEqual(itemKeys(rail.items), ["chapter:c", "group:g", "chapter:d"]);
  assert.deepEqual(chapterKeys(rail.items[1]), ["a", "b"]);
});

test("duplicate chapters and duplicate group keys do not produce duplicate rail rows", async () => {
  const { buildRailItems } = await loadPillars();
  const chapters = [
    { key: "a", title: "A" },
    { key: "a", title: "A again" },
    { key: "b", title: "B" },
  ];
  const rail = buildRailItems(chapters, [
    { key: "g", title: "First", chapters: ["a"] },
    { key: "g", title: "Second", chapters: ["b"] },
  ]);

  assert.deepEqual(itemKeys(rail.items), ["group:g", "chapter:b"]);
  assert.deepEqual(chapterKeys(rail.items[0]), ["a"]);
  assert.equal(rail.items[0].title, "First");
});

test("empty input is safe", async () => {
  const { buildRailItems } = await loadPillars();
  const rail = buildRailItems();
  assert.deepEqual(itemKeys(rail.items), []);
  assert.deepEqual({ ...rail.groupFirstKeys }, {});
  assert.deepEqual({ ...rail.chapterGroupKeys }, {});
});
