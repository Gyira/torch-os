// 任务拖放（2026-10-17）：任务行携带一个 task key，侧栏清单 / 四象限面板读取它。
// 只做浏览器的 HTML5 drag 数据层，不碰 DOM 结构；写入仍由 time-module 的 post 负责。
export const TASK_DRAG_MIME = "application/x-torch-task-key";

/** dragstart 时写入被拖任务 key；没有 dataTransfer（测试/旧环境）时返回 false。 */
export function setTaskDragData(dataTransfer, key) {
  if (!dataTransfer || !key) return false;
  try {
    dataTransfer.effectAllowed = "move";
    dataTransfer.setData(TASK_DRAG_MIME, String(key));
    dataTransfer.setData("text/plain", String(key));
    return true;
  } catch {
    return false;
  }
}

/** dragover/drop 时判断是否是我们自己的任务拖放（不要接管文件/文本拖放）。 */
export function isTaskDrag(event) {
  const types = event?.dataTransfer?.types;
  if (!types) return false;
  try {
    return Array.from(types).includes(TASK_DRAG_MIME);
  } catch {
    return false;
  }
}

/** drop 时读取 key；只在 drop 事件里读得到 dataTransfer 的自定义类型。 */
export function readTaskDragKey(event) {
  try {
    return event?.dataTransfer?.getData(TASK_DRAG_MIME) || "";
  } catch {
    return "";
  }
}
