import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

async function loadModel() {
  const context = vm.createContext({});
  const filename = path.join(frontend, "journal-heatmap.js");
  const module = new vm.SourceTextModule(await readFile(filename, "utf8"), {
    context, identifier: filename,
  });
  await module.link(() => { throw new Error("no imports expected"); });
  await module.evaluate();
  return module.namespace;
}

const RAW = {
  start: "2026-05-04",
  weeks: 26,
  today: "2026-10-31",
  first_weekday: 1,
  days: {
    "2026-08-13": {
      entries: 1, words: 850, path: "00_人生路/01_日记/2026-08-13.md", legacy: false,
    },
    "2026-10-31": {
      entries: 1, words: 150, path: "00_人生路/01_日记/2026-10-31.md", legacy: false,
    },
    // 窗口外的日子不应计入摘要，也不应有格子。
    "2026-04-01": { entries: 1, words: 900, path: "00_人生路/01_日记/2026-04-01.md", legacy: false },
  },
};

function cellAt(model, key) {
  return model.columns.flat().find((cell) => cell.key === key);
}

test("26 周热力图按周排布，月份和星期标签与 jotsy 一致", async () => {
  const model = await loadModel();
  const built = model.buildJournalHeatmap(RAW);

  assert.equal(built.start, "2026-05-04");
  assert.equal(built.today, "2026-10-31");
  assert.equal(built.weeks, 26);
  assert.equal(built.firstWeekday, 1);
  assert.equal(built.columns.length, 26);
  assert.ok(built.columns.every((column) => column.length === 7));
  assert.deepEqual(
    Array.from(built.monthLabels.filter(Boolean)),
    ["5月", "6月", "7月", "8月", "9月", "10月"],
  );
  assert.deepEqual(
    Array.from(built.weekdayLabels),
    ["周一", "", "周三", "", "周五", "", "周日"],
  );
  assert.equal(built.writtenDays, 2);
  assert.equal(built.summary, "近 26 周里有 2 天写了日记");
  assert.equal(built.columns.flat().length, 182);
});

test("有日记的格子按字数分档，今天有描边标记，未来格子留空", async () => {
  const model = await loadModel();
  const built = model.buildJournalHeatmap(RAW);

  const focused = cellAt(built, "2026-08-13");
  assert.equal(focused.level, 3);
  assert.equal(focused.today, false);
  assert.equal(focused.caption, "2026年8月13日 · 1 篇 · 850 字");
  assert.equal(focused.path, "00_人生路/01_日记/2026-08-13.md");

  const today = cellAt(built, "2026-10-31");
  assert.equal(today.today, true);
  assert.equal(today.level, 1);
  assert.equal(today.caption, "2026年10月31日 · 1 篇 · 150 字");

  const empty = cellAt(built, "2026-08-12");
  assert.equal(empty.level, 0);
  assert.equal(empty.caption, "2026年8月12日 · 没有日记");

  const future = cellAt(built, "2026-11-01");
  assert.equal(future.future, true);
  assert.equal(future.level, 0);

  assert.equal(cellAt(built, "2026-04-01"), undefined);
});

test("字数档位阈值与 jotsy 相同", async () => {
  const model = await loadModel();
  assert.equal(model.journalHeatmapLevel({ entries: 0, words: 1000 }), 0);
  assert.equal(model.journalHeatmapLevel({ entries: 1, words: 0 }), 1);
  assert.equal(model.journalHeatmapLevel({ entries: 1, words: 199 }), 1);
  assert.equal(model.journalHeatmapLevel({ entries: 1, words: 200 }), 2);
  assert.equal(model.journalHeatmapLevel({ entries: 1, words: 599 }), 2);
  assert.equal(model.journalHeatmapLevel({ entries: 1, words: 600 }), 3);
  assert.equal(model.journalHeatmapLevel({ entries: 1, words: 1499 }), 3);
  assert.equal(model.journalHeatmapLevel({ entries: 1, words: 1500 }), 4);
});

test("卡片标记包含月份、星期、图例、可打开日记的格子和今日动作", async () => {
  const model = await loadModel();
  const built = model.buildJournalHeatmap(RAW);
  const html = model.journalHeatmapMarkup(built, {
    action: '<button type="button" class="life-heatmap-action" data-editor-new="life" data-new-chapter="journal">开始写今天的日记</button>',
  });

  assert.match(html, /class="life-heatmap-card"/);
  assert.match(html, /写作热力图/);
  assert.match(html, /近 26 周里有 2 天写了日记/);
  assert.match(html, />5月</);
  assert.match(html, /class="life-heatmap-weekday">周一</);
  assert.match(html, /class="life-heatmap-swatch" data-level="4"/);
  assert.match(html, /data-journal-day="2026-08-13"/);
  assert.match(html, /data-journal-path="00_人生路\/01_日记\/2026-08-13.md"/);
  assert.match(html, /data-heat-caption="2026年8月13日 · 1 篇 · 850 字"/);
  assert.match(html, /data-editor-new="life"/);
  assert.equal((html.match(/<(?:button|span)[^>]*class="life-heatmap-cell/g) || []).length, 182);
});

test("今天没有日记时，今天和卡片动作都能直接开始写", async () => {
  const model = await loadModel();
  const raw = {
    ...RAW,
    days: { "2026-08-13": RAW.days["2026-08-13"] },
  };
  const built = model.buildJournalHeatmap(raw);
  const today = cellAt(built, "2026-10-31");
  assert.equal(today.entry, null);
  assert.equal(today.caption, "2026年10月31日 · 没有日记");
  const html = model.journalHeatmapMarkup(built, {
    action: '<button type="button" class="life-heatmap-action" data-editor-new="life">开始写今天的日记</button>',
  });
  assert.match(html, /data-editor-new="life" data-new-chapter="journal" aria-label="2026年10月31日 · 没有日记，开始写今天的日记"/);
});

test("格子里的路径会转义，未知数据回退为空", async () => {
  const model = await loadModel();
  const raw = {
    ...RAW,
    days: {
      "2026-08-13": {
        entries: 1, words: 850, path: '00_人生路/01_日记/<x>&".md', legacy: false,
      },
    },
  };
  const built = model.buildJournalHeatmap(raw);
  const html = model.journalHeatmapMarkup(built);
  assert.ok(!html.includes("<x>"));
  assert.match(html, /&lt;x&gt;&amp;&quot;\.md/);

  assert.equal(model.buildJournalHeatmap(null), null);
  assert.equal(model.buildJournalHeatmap({}), null);
  assert.equal(model.buildJournalHeatmap({ start: "bad", today: "bad" }), null);
});

test("缺少 start 时从今天和 first_weekday 推回最近 26 周的周一", async () => {
  const model = await loadModel();
  const built = model.buildJournalHeatmap({
    weeks: 26, today: "2026-10-31", first_weekday: 1, days: {},
  });
  assert.equal(built.start, "2026-05-04");
  assert.equal(new Date(`${built.start}T00:00:00Z`).getUTCDay(), 1);
});
