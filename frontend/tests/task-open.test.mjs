// 打开任务文档：宿主检测、obsidian:// 编码、插件消息形状与浏览器兜底。
import test from "node:test";
import assert from "node:assert/strict";
import {
  createObsidianNote, isObsidianHost, newNoteMessage, obsidianNewUri, obsidianOpenUri,
  openNoteMessage, openTaskDocument,
} from "../task-open.js";

test("only the Obsidian embedded query counts as a host bridge", () => {
  assert.equal(isObsidianHost("?embed=1&host=obsidian&theme=dark"), true);
  assert.equal(isObsidianHost("?embed=1&panel=1"), false);
  assert.equal(isObsidianHost(""), false);
  assert.equal(isObsidianHost("?host=obsidian"), false);
});

test("obsidian:// URI encodes vault and relative path", () => {
  assert.equal(obsidianOpenUri("我的 库", "00-System/任务/写方案 2.md"),
    "obsidian://open?vault=%E6%88%91%E7%9A%84%20%E5%BA%93&file=00-System%2F%E4%BB%BB%E5%8A%A1%2F%E5%86%99%E6%96%B9%E6%A1%88%202.md");
  assert.equal(obsidianOpenUri("", "a.md"), "");
  assert.equal(obsidianOpenUri("库", ""), "");
});

test("open message only carries the relative path", () => {
  assert.deepEqual(openNoteMessage("00-System/任务/a.md"),
    { type: "torch:open-note", path: "00-System/任务/a.md" });
});

test("obsidian://new encodes vault, path and optional content", () => {
  const vault = "我的 库";
  const target = "03_知识库/00_收件箱/未命名.md";
  assert.equal(obsidianNewUri(vault, target),
    `obsidian://new?vault=${encodeURIComponent(vault)}&file=${encodeURIComponent(target)}`);
  assert.match(obsidianNewUri(vault, target, "正文"), /&content=/);
  assert.equal(obsidianNewUri("", target), "");
  assert.equal(obsidianNewUri(vault, ""), "");
});

test("new-note message carries the path and prefilled content", () => {
  assert.deepEqual(newNoteMessage("01_自我档案/想法/a.md", "hello"),
    { type: "torch:new-note", path: "01_自我档案/想法/a.md", content: "hello" });
});

function withEnvironment({ search = "", parent, open }, run) {
  const hadLocation = Object.hasOwn(globalThis, "location");
  const oldLocation = globalThis.location;
  const oldParent = globalThis.parent;
  const oldOpen = globalThis.open;
  Object.defineProperty(globalThis, "location", { value: { search }, configurable: true });
  globalThis.parent = parent;
  globalThis.open = open;
  try {
    return run();
  } finally {
    if (hadLocation) Object.defineProperty(globalThis, "location", { value: oldLocation, configurable: true });
    else delete globalThis.location;
    globalThis.parent = oldParent;
    globalThis.open = oldOpen;
  }
}

test("inside Obsidian the open request is posted to the host bridge", () => {
  const posted = [];
  const task = { path: "00-System/任务/写方案.md", legacy: false };
  withEnvironment({
    search: "?embed=1&host=obsidian",
    parent: { postMessage: (...args) => posted.push(args) },
    open: () => { throw new Error("不应在宿主桥里打开外部链接"); },
  }, () => {
    assert.equal(openTaskDocument(task, { vault_name: "我的库" }), true);
  });
  assert.deepEqual(posted, [[{ type: "torch:open-note", path: "00-System/任务/写方案.md" }, "*"]]);
});

test("in a plain browser it falls back to obsidian://", () => {
  const opened = [];
  const task = { path: "00-System/任务（tasks）.md", legacy: true };
  withEnvironment({
    search: "",
    parent: undefined,
    open: (uri, target) => opened.push([uri, target]),
  }, () => {
    assert.equal(openTaskDocument(task, { vault_name: "知识 库" }), true);
  });
  assert.equal(opened.length, 1);
  assert.equal(opened[0][0],
    "obsidian://open?vault=%E7%9F%A5%E8%AF%86%20%E5%BA%93&file=00-System%2F%E4%BB%BB%E5%8A%A1%EF%BC%88tasks%EF%BC%89.md");
  assert.equal(opened[0][1], "_blank");
});

test("inside Obsidian a new note is created by the host bridge", () => {
  const posted = [];
  withEnvironment({
    search: "?embed=1&host=obsidian",
    parent: { postMessage: (...args) => posted.push(args) },
    open: () => { throw new Error("不应在宿主桥里打开外部链接"); },
  }, () => {
    assert.equal(createObsidianNote("03_知识库/00_收件箱/a.md", "我的库", "预填正文"), true);
  });
  assert.deepEqual(posted, [[
    { type: "torch:new-note", path: "03_知识库/00_收件箱/a.md", content: "预填正文" }, "*",
  ]]);
});
