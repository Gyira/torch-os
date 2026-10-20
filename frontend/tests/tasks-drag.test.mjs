// 任务拖放的数据层：只接受自己的 MIME，不误接文件/文本拖放。
import test from "node:test";
import assert from "node:assert/strict";
import {
  TASK_DRAG_MIME, setTaskDragData, isTaskDrag, readTaskDragKey,
  LIST_DRAG_MIME, setListDragData, isListDrag, readListDragName,
} from "../tasks-drag.js";

function transfer({ types = [], data = new Map(), failSet = false } = {}) {
  return {
    types,
    effectAllowed: "",
    setData(type, value) {
      if (failSet) throw new Error("denied");
      data.set(type, String(value));
    },
    getData(type) { return data.get(type) || ""; },
  };
}

test("setTaskDragData marks the drag as a move and stores the key", () => {
  const data = new Map();
  const dt = transfer({ data });
  assert.equal(setTaskDragData(dt, "a".repeat(64)), true);
  assert.equal(dt.effectAllowed, "move");
  assert.equal(data.get(TASK_DRAG_MIME), "a".repeat(64));
  assert.equal(data.get("text/plain"), "a".repeat(64));
  assert.equal(setTaskDragData(null, "x"), false);
  assert.equal(setTaskDragData(transfer({ failSet: true }), "x"), false);
});

test("isTaskDrag only accepts our custom MIME", () => {
  assert.equal(isTaskDrag({ dataTransfer: transfer({ types: [TASK_DRAG_MIME] }) }), true);
  assert.equal(isTaskDrag({ dataTransfer: transfer({ types: ["Files"] }) }), false);
  assert.equal(isTaskDrag({ dataTransfer: transfer({ types: ["text/plain"] }) }), false);
  assert.equal(isTaskDrag({}), false);
});

test("readTaskDragKey reads the custom payload and tolerates missing data", () => {
  const data = new Map([[TASK_DRAG_MIME, "k".repeat(64)]]);
  assert.equal(readTaskDragKey({ dataTransfer: transfer({ data }) }), "k".repeat(64));
  assert.equal(readTaskDragKey({ dataTransfer: transfer() }), "");
  assert.equal(readTaskDragKey({}), "");
});

test("list drags use their own MIME so task drops never steal them", () => {
  const data = new Map();
  const dt = transfer({ data });
  assert.equal(setListDragData(dt, "心愿清单"), true);
  assert.equal(dt.effectAllowed, "move");
  assert.equal(data.get(LIST_DRAG_MIME), "心愿清单");
  assert.equal(setListDragData(null, "心愿清单"), false);
  assert.equal(setListDragData(transfer({ failSet: true }), "心愿清单"), false);

  assert.equal(isListDrag({ dataTransfer: transfer({ types: [LIST_DRAG_MIME] }) }), true);
  assert.equal(isListDrag({ dataTransfer: transfer({ types: [TASK_DRAG_MIME] }) }), false);
  assert.equal(isTaskDrag({ dataTransfer: transfer({ types: [LIST_DRAG_MIME] }) }), false);
  assert.equal(isListDrag({}), false);

  assert.equal(readListDragName({ dataTransfer: transfer({ data }) }), "心愿清单");
  assert.equal(readListDragName({ dataTransfer: transfer() }), "");
  assert.equal(readListDragName({}), "");
});
