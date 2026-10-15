// 对话统一在 dsh 里（用户 2026-10-05 定调）：产品内不再有自己的对话页。
// 页面上的“开始对话 / 快捷问题”只把问题交给宿主 dsh 的输入框；独立打开时
// （没有宿主）退化为复制到剪贴板 + 明确告知去哪里提问。
import { toast } from "./core.js";

export const COMPOSE_MESSAGE = "torch:compose";
export const COMPOSE_RESULT = "torch:compose-result";

const REPLY_TIMEOUT_MS = 1500;

function embedded() {
  try {
    return Boolean(window.parent && window.parent !== window);
  } catch {
    return false;
  }
}

function fallback(question) {
  if (question && navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(question).then(
      () => toast("对话已交给 dsh：问题已复制，粘贴到 dsh 对话框即可。"),
      () => toast("对话已交给 dsh：请在本机 dsh 的对话框里提问。"),
    );
    return false;
  }
  toast("对话已交给 dsh：请在本机 dsh 的对话框里提问。");
  return false;
}

/** 请宿主 dsh 把这段话放进对话输入框（空字符串表示只聚焦）。
 *  宿主会回一条结果，所以提示说的是真的做到了还是没做到。 */
export function askHarnessToCompose(text = "") {
  const question = String(text || "").trim();
  if (!embedded()) return fallback(question);

  const pending = new Promise((resolve) => {
    let settled = false;
    const finish = (ok) => {
      if (settled) return;
      settled = true;
      window.removeEventListener("message", onMessage);
      window.clearTimeout(timer);
      resolve(ok);
    };
    const onMessage = (event) => {
      const data = event.data;
      if (data && data.type === COMPOSE_RESULT) finish(Boolean(data.ok));
    };
    const timer = window.setTimeout(() => finish(false), REPLY_TIMEOUT_MS);
    window.addEventListener("message", onMessage);
    try {
      window.parent.postMessage({ type: COMPOSE_MESSAGE, text: question }, "*");
    } catch {
      finish(false);
    }
  });

  return pending.then((ok) => {
    if (ok) {
      toast(question ? "已把这个问题放到 dsh 的对话框里。" : "对话在 dsh 里：光标已放到对话框。");
      return true;
    }
    return fallback(question);
  });
}
