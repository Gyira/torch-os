import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

async function loadModel() {
  const context = vm.createContext({});
  const filename = path.join(frontend, "bookmark-model.js");
  const module = new vm.SourceTextModule(await readFile(filename, "utf8"), {
    context, identifier: filename,
  });
  await module.link(() => { throw new Error("no imports expected"); });
  await module.evaluate();
  return module.namespace;
}

function fakeStorage(initial = {}) {
  const map = new Map(Object.entries(initial));
  return {
    getItem: (key) => (map.has(key) ? map.get(key) : null),
    setItem: (key, value) => map.set(key, String(value)),
    removeItem: (key) => map.delete(key),
  };
}

test("网址整理：补协议、小写主机、只留 http/https", async () => {
  const { normalizeBookmarkUrl } = await loadModel();
  assert.equal(normalizeBookmarkUrl("chat.deepseek.com"), "https://chat.deepseek.com/");
  assert.equal(normalizeBookmarkUrl("  HTTPS://Chat.DeepSeek.com  "), "https://chat.deepseek.com/");
  assert.equal(normalizeBookmarkUrl("https://www.zhihu.com/question/123?a=1#x"),
    "https://www.zhihu.com/question/123?a=1#x");
  assert.equal(normalizeBookmarkUrl("http://localhost:9168/"), "http://localhost:9168/");

  // 不是网页的地址一律不进书签栏
  assert.equal(normalizeBookmarkUrl("javascript:alert(1)"), "");
  assert.equal(normalizeBookmarkUrl("ftp://example.com/file"), "");
  assert.equal(normalizeBookmarkUrl("obsidian://open?vault=x"), "");
  assert.equal(normalizeBookmarkUrl("https://user:pass@example.com/"), "");
  assert.equal(normalizeBookmarkUrl("https://example.com/a b"), "");
  assert.equal(normalizeBookmarkUrl("没有点的主机"), "");
  assert.equal(normalizeBookmarkUrl(""), "");
  assert.equal(normalizeBookmarkUrl("https://example.com:99999/"), "");
  assert.equal(normalizeBookmarkUrl(`https://example.com/${"x".repeat(600)}`), "");
});

test("域名与图标：同一网址永远同一颜色，名称留空回落到域名", async () => {
  const { hostLabel, siteInitial, colorIndex, normalizeBookmarkName } = await loadModel();
  assert.equal(hostLabel("https://www.zhihu.com/question/1"), "zhihu.com");
  assert.equal(hostLabel("http://127.0.0.1:9168/"), "127.0.0.1");
  assert.equal(hostLabel("not a url"), "");
  assert.equal(siteInitial("DeepSeek 网页版", "https://chat.deepseek.com/"), "D");
  assert.equal(siteInitial("", "https://zhihu.com/"), "Z");
  assert.equal(siteInitial("考研", "https://kaoyan.com/"), "考");
  assert.equal(siteInitial("", ""), "?");
  assert.equal(colorIndex("https://chat.deepseek.com/"), colorIndex("https://chat.deepseek.com/"));
  assert.ok(colorIndex("https://a.com/") >= 0 && colorIndex("https://a.com/") < 6);
  assert.equal(normalizeBookmarkName("  ", "https://www.bilibili.com/"), "bilibili.com");
  assert.equal(normalizeBookmarkName("  DeepSeek   网页版 ", "https://x.com/"), "DeepSeek 网页版");
});

test("旧数据/脏数据不会让整条栏渲染失败", async () => {
  const { parseBookmarks, sanitizeBookmarks, BOOKMARK_LIMIT } = await loadModel();
  assert.equal(parseBookmarks("").length, 0);
  assert.equal(parseBookmarks("{坏 JSON").length, 0);
  assert.equal(parseBookmarks("null").length, 0);
  assert.equal(parseBookmarks('"字符串"').length, 0);

  const list = sanitizeBookmarks([
    { name: "DeepSeek", url: "chat.deepseek.com" },
    { name: "重复", url: "https://chat.deepseek.com/" },
    { name: "坏", url: "javascript:alert(1)" },
    null,
    { id: "bad id!", name: "知乎", url: "https://zhihu.com" },
  ]);
  assert.equal(list.length, 2);
  assert.equal(list[0].name, "DeepSeek");
  assert.equal(list[1].name, "知乎");
  assert.match(list[1].id, /^b-[a-z0-9]+$/);
  assert.ok(list.every((item) => item.url.startsWith("https://")));

  const many = Array.from({ length: 40 }, (_, index) => ({ name: `站${index}`, url: `https://site${index}.com/` }));
  assert.equal(sanitizeBookmarks(many).length, BOOKMARK_LIMIT);
});

test("添加：追加到末尾、留空用域名、重复与超限有话说", async () => {
  const { addBookmark, BOOKMARK_LIMIT } = await loadModel();
  const first = addBookmark([], { name: "", url: "chat.deepseek.com" });
  assert.equal(first.error, "");
  assert.equal(first.bookmark.name, "chat.deepseek.com");
  assert.equal(first.bookmark.url, "https://chat.deepseek.com/");
  assert.match(first.bookmark.id, /^b-/);

  const second = addBookmark(first.list, { name: "知乎", url: "https://zhihu.com" });
  assert.equal(second.list.length, 2);
  assert.equal(second.list[1].name, "知乎");
  assert.notEqual(second.list[0].id, second.list[1].id);

  const duplicate = addBookmark(second.list, { name: "再来一次", url: "HTTPS://chat.deepseek.com" });
  assert.equal(duplicate.list.length, 2);
  assert.match(duplicate.error, /已经在书签栏/);

  const invalid = addBookmark(second.list, { name: "坏地址", url: "javascript:alert(1)" });
  assert.equal(invalid.list.length, 2);
  assert.match(invalid.error, /http:\/\/ 或 https:\/\//);

  const full = Array.from({ length: BOOKMARK_LIMIT }, (_, index) => ({ name: `站${index}`, url: `https://site${index}.com/` }));
  const overflow = addBookmark(full, { name: "多一个", url: "https://extra.com/" });
  assert.equal(overflow.list.length, BOOKMARK_LIMIT);
  assert.match(overflow.error, new RegExp(`最多 ${BOOKMARK_LIMIT} 个`));
});

test("修改/删除/排序：坏网址整条不改，删到空也不会复活默认值", async () => {
  const { addBookmark, updateBookmark, removeBookmark, moveBookmark } = await loadModel();
  const base = addBookmark([], { name: "甲", url: "https://a.com" }).list;
  const two = addBookmark(base, { name: "乙", url: "https://b.com" }).list;
  const [first, second] = two;

  const renamed = updateBookmark(two, second.id, { name: "乙站" });
  assert.equal(renamed.bookmark.name, "乙站");
  assert.equal(renamed.bookmark.url, "https://b.com/");

  const badUrl = updateBookmark(two, second.id, { url: "ftp://b.com" });
  assert.match(badUrl.error, /http/);
  assert.equal(badUrl.list[1].url, "https://b.com/");

  const clash = updateBookmark(two, second.id, { url: "https://a.com/" });
  assert.match(clash.error, /已经在书签栏/);
  assert.equal(clash.list[1].url, "https://b.com/");

  assert.match(updateBookmark(two, "missing", { name: "x" }).error, /已经不在列表/);

  const moved = moveBookmark(two, second.id, -1);
  assert.equal(moved.moved, true);
  assert.equal(moved.list[0].id, second.id);
  assert.equal(moved.list[1].id, first.id);
  assert.equal(moveBookmark(two, first.id, -1).moved, false);
  assert.equal(moveBookmark(two, second.id, 1).moved, false);

  assert.equal(removeBookmark(two, second.id).removed, true);
  assert.equal(removeBookmark(removeBookmark(two, second.id).list, second.id).removed, false);
});

test("读写：键不存在才给默认书签，空列表保持为空", async () => {
  const {
    readBookmarks, writeBookmarks, BOOKMARK_STORAGE_KEY, bookmarkDefaults,
  } = await loadModel();
  const defaults = bookmarkDefaults();
  assert.equal(defaults.length, 1);
  assert.equal(defaults[0].url, "https://chat.deepseek.com/");

  const fresh = fakeStorage();
  assert.equal(JSON.stringify(readBookmarks(fresh)), JSON.stringify(defaults));

  const storage = fakeStorage();
  assert.equal(writeBookmarks(storage, []), true);
  assert.equal(storage.getItem(BOOKMARK_STORAGE_KEY), "[]");
  assert.equal(readBookmarks(storage).length, 0);

  const roundTrip = writeBookmarks(storage, [{ id: "x", name: "知乎", url: "https://zhihu.com", extra: "丢弃" }]);
  assert.equal(roundTrip, true);
  const read = readBookmarks(storage);
  assert.equal(read.length, 1);
  assert.equal(read[0].url, "https://zhihu.com/");
  assert.equal(Object.hasOwn(read[0], "extra"), false);

  const broken = { getItem: () => "{坏", setItem: () => { throw new Error("quota"); } };
  assert.equal(readBookmarks(broken).length, 0);
  assert.equal(writeBookmarks(broken, []), false);
});

test("书签栏接在工作台问候语右侧，并走宿主桥打开浏览器", async () => {
  const html = await readFile(path.join(frontend, "index.html"), "utf8");
  const topline = html.indexOf('class="workbench-topline"');
  const bar = html.indexOf('id="workbench-bookmarks"');
  const timeModule = html.indexOf('id="workbench-time"');
  assert.ok(topline > 0 && bar > topline, "书签栏必须在问候语同一行的容器里");
  assert.ok(bar < timeModule, "书签栏在待办模块之前（红框那条）");
  assert.match(html, /<nav id="workbench-bookmarks" class="bookmark-bar"[^>]*aria-label="网页书签栏"/);

  const workbench = await readFile(path.join(frontend, "workbench.js"), "utf8");
  assert.match(workbench, /initBookmarks/);

  const page = await readFile(path.join(frontend, "bookmarks.js"), "utf8");
  assert.match(page, /torch:open-url/);
  assert.match(page, /data-bookmark-manage/);
  assert.match(page, /data-bookmark-new-url/);

  const css = await readFile(path.join(frontend, "styles.css"), "utf8");
  assert.match(css, /\.bookmark-bar \{/);
  assert.match(css, /\.bookmark-gear \{/);
  assert.match(css, /\.bookmark-manage-row \{/);

  const plugin = await readFile(path.join(frontend, "..", "obsidian-plugin", "main.js"), "utf8");
  assert.match(plugin, /torch:open-url/);
  assert.match(plugin, /shell\.openExternal/);
  assert.match(plugin, /url\.protocol !== 'http:'/);
});
