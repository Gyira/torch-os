// 本子卡片拖拽排序（2026-11-24）。
// 这里只处理拖动与落点：顺序由调用方保存，后端写到库里的
// `.torch-os/notebook-order.json`，下次打开仍按这个顺序显示。
// 纯 DOM 交互，不读 localStorage；保存失败时调用方重新渲染即可恢复旧顺序。
import { api } from "./core.js";

const DRAGGING = "is-dragging";
const DROP_TARGET = "is-drop-target";

export function saveNotebookOrder(pillar, keys) {
  return api("/api/notebooks/order", {
    method: "POST",
    body: JSON.stringify({ pillar, keys }),
  });
}

/**
 * 让本子卡片可以整卡拖动重排。
 * `cardSelector` 必须匹配带 `data-notebook-drag-key` 的卡片外层；
 * 落点回调收到 `{ view, key, keys }`，`keys` 是拖动后的完整顺序。
 */
export function bindNotebookDrag({ cardSelector, onDrop } = {}) {
  const selector = String(cardSelector || "");
  if (!selector) return () => {};
  let drag = null;

  const cardsIn = (grid) => Array.from(grid.querySelectorAll(`:scope > ${selector}`));
  const clearTargets = () => {
    drag?.grid.querySelectorAll(`.${DROP_TARGET}`).forEach((node) => {
      node.classList.remove(DROP_TARGET);
    });
  };
  const clear = () => {
    drag?.grid.classList.remove("is-sorting");
    drag?.grid.querySelectorAll(`.${DRAGGING}, .${DROP_TARGET}`).forEach((node) => {
      node.classList.remove(DRAGGING, DROP_TARGET);
    });
    // 拖动结束后卡片可能已被重新渲染，旧节点上的标记也一并清掉。
    drag?.card.classList.remove(DRAGGING);
    drag = null;
  };

  document.addEventListener("dragstart", (event) => {
    if (drag) return;
    const card = event.target.closest?.(selector);
    if (!card) return;
    // 「•••」管理菜单和恋爱本子的删除按钮不是拖动把手。
    if (event.target.closest?.(".library-more, .love-notebook-delete")) {
      event.preventDefault();
      return;
    }
    const grid = card.parentElement;
    if (!grid?.classList?.contains("notebook-grid")) return;
    drag = { card, grid };
    card.classList.add(DRAGGING);
    grid.classList.add("is-sorting");
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = "move";
      try {
        event.dataTransfer.setData("text/plain", card.dataset.notebookDragKey || "");
      } catch { /* 某些内嵌浏览器禁止 setData，不影响拖动本身 */ }
    }
  });

  document.addEventListener("dragover", (event) => {
    if (!drag) return;
    const target = event.target.closest?.(selector);
    if (!target || target === drag.card || target.parentElement !== drag.grid) {
      clearTargets();
      return;
    }
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
    for (const card of cardsIn(drag.grid)) {
      card.classList.toggle(DROP_TARGET, card === target);
    }
  });

  document.addEventListener("drop", (event) => {
    if (!drag) return;
    const target = event.target.closest?.(selector);
    if (!target || target.parentElement !== drag.grid) {
      clear();
      return;
    }
    event.preventDefault();
    const { card, grid } = drag;
    if (target !== card) {
      // 按指针在目标卡片左/右半区决定插到前面还是后面；多行网格仍按阅读顺序。
      const rect = target.getBoundingClientRect();
      if (event.clientX < rect.left + rect.width / 2) target.before(card);
      else target.after(card);
    }
    const keys = cardsIn(grid)
      .map((node) => String(node.dataset.notebookDragKey || ""))
      .filter(Boolean);
    const detail = {
      key: String(card.dataset.notebookDragKey || ""),
      view: String(card.dataset.notebookDragView || ""),
      keys,
    };
    clear();
    if (target !== card) {
      Promise.resolve(onDrop?.(detail)).catch(() => {
        // 保存失败由调用方回滚并提示；这里吞掉，避免未处理的 rejection。
      });
    }
  });

  document.addEventListener("dragend", clear);
  return clear;
}
