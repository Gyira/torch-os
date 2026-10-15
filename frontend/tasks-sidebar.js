// 任务页的二级侧边栏（2026-10-08）：置顶区 + 三大可折叠组（任务看板 / 清单管理 / 标签管理）。
// 第 2 轮照滴答实测规格补齐：清单与标签的「＋」「…」菜单、文件夹树（可折叠）、清单行的快速添加。
import { escapeHtml } from "./core.js";
import { VIEWS, INBOX, viewCounts, countBy, listTree, pinnedItems, listIcon } from "./tasks-model.js";
import { openPopover } from "./tasks-picker.js";

const ICONS = { today: "☀", week: "🗓", inbox: "📥", done: "✅" };

function group(container, { label, collapsed, onToggle, hint, onAdd, addLabel }) {
  const section = document.createElement("section");
  section.className = "time-group";
  const head = document.createElement("div");
  head.className = "time-group-head";
  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "time-group-toggle";
  toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
  toggle.innerHTML = `<span class="time-group-arrow" aria-hidden="true">${collapsed ? "▸" : "▾"}</span>${escapeHtml(label)}` +
    (hint ? `<span class="time-group-hint">${escapeHtml(hint)}</span>` : "");
  toggle.addEventListener("click", () => onToggle());
  head.append(toggle);
  if (onAdd) {
    const add = document.createElement("button");
    add.type = "button";
    add.className = "time-group-add";
    add.textContent = "＋";
    add.setAttribute("aria-label", addLabel || `新增${label}`);
    add.title = addLabel || `新增${label}`;
    add.addEventListener("click", () => onAdd());
    head.append(add);
  }
  const body = document.createElement("div");
  body.className = "time-group-body";
  body.hidden = Boolean(collapsed);
  section.append(head, body);
  container.append(section);
  return body;
}

function item(host, { id, icon, label, count, active, kind, onClick, actions = [] }) {
  const row = document.createElement("div");
  row.className = `time-side-row${active ? " is-active" : ""}`;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "time-side-item";
  button.dataset.timeKind = kind;
  button.dataset.timeId = id;
  button.setAttribute("aria-current", active ? "true" : "false");
  button.innerHTML = `<span class="time-side-icon" aria-hidden="true">${icon || ""}</span>` +
    `<span class="time-side-label">${escapeHtml(label)}</span>` +
    (count ? `<span class="time-side-count">${count}</span>` : "");
  button.addEventListener("click", onClick);
  row.append(button);
  if (actions.length) {
    const tools = document.createElement("span");
    tools.className = "time-side-tools";
    for (const action of actions) {
      const tool = document.createElement("button");
      tool.type = "button";
      tool.className = "time-side-tool";
      tool.textContent = action.icon;
      tool.title = action.title;
      tool.setAttribute("aria-label", `${action.title}：${label}`);
      tool.addEventListener("click", (event) => {
        event.stopPropagation();
        action.onClick(tool);
      });
      tools.append(tool);
    }
    row.append(tools);
  }
  host.append(row);
  return row;
}

/** 色点或默认小图标（清单/标签共用）。 */
function iconHtml(entry) {
  if (entry.color) {
    return `<span class="task-tag-dot" style="--tag-color:${escapeHtml(entry.color)}"></span>`;
  }
  return escapeHtml(listIcon(entry));
}

function openRowMenu(anchor, rows) {
  openPopover(anchor, (layer, close) => {
    for (const row of rows) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "time-popover-row";
      button.innerHTML = `<span class="time-popover-label">${escapeHtml(row.label)}</span>`;
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        close();
        row.onClick();
      });
      layer.append(button);
    }
  });
}

/**
 * 画侧边栏。
 * @param {object} options
 *   tasks / lists / folders / tags：后端板面数据
 *   selection：{view} | {list} | {tag}
 *   collapsed：折叠状态，键为 views / lists / tags / `folder:<名>`
 *   onSelect(selection) / onToggleGroup(name) / onQuickAdd(listName) / onAddList() / onAddTag()
 *   onListAction(action, entry) / onFolderAction(action, name) / onTagAction(action, entry)
 */
export function renderTaskSidebar(container, options) {
  const {
    tasks = [], lists = [], folders = [], tags = [], selection = {},
    collapsed = {},
    onSelect = () => {}, onToggleGroup = () => {}, onQuickAdd = () => {},
    onAddList = () => {}, onAddTag = () => {},
    onListAction = () => {}, onFolderAction = () => {}, onTagAction = () => {},
  } = options;
  const counts = viewCounts(tasks);
  const listCounts = countBy(tasks, "list_name");
  const tagCounts = countBy(tasks, "tags");
  const view = selection.view || (selection.list || selection.tag ? "" : "today");

  container.replaceChildren();

  // 置顶区（模块左上角的瓦片，点击直达；照滴答图 9）
  const pinned = pinnedItems(lists, tags);
  if (pinned.length) {
    const box = document.createElement("div");
    box.className = "time-pinned";
    box.setAttribute("role", "group");
    box.setAttribute("aria-label", "置顶");
    for (const item_ of pinned) {
      const tile = document.createElement("button");
      tile.type = "button";
      tile.className = "time-pinned-tile";
      tile.title = `置顶：${item_.name}`;
      tile.innerHTML = iconHtml(item_) + `<span>${escapeHtml(item_.name)}</span>`;
      tile.addEventListener("click", () => onSelect(item_.kind === "list"
        ? { list: item_.name } : { tag: item_.name }));
      box.append(tile);
    }
    container.append(box);
  }

  // 一、任务看板
  const board = group(container, {
    label: "任务看板", collapsed: collapsed.views, onToggle: () => onToggleGroup("views"),
  });
  for (const entry of VIEWS) {
    const count = entry.id === "inbox" ? listCounts[INBOX] : counts[entry.id];
    item(board, {
      id: entry.id, icon: ICONS[entry.id] || "", label: entry.label, count: count || 0,
      active: !selection.list && !selection.tag && view === entry.id, kind: "view",
      onClick: () => onSelect({ view: entry.id }),
    });
  }

  // 二、清单管理（根级清单 + 文件夹 > 清单）
  const listBody = group(container, {
    label: "清单管理", collapsed: collapsed.lists, onToggle: () => onToggleGroup("lists"),
    hint: `${lists.length} 个`, onAdd: onAddList, addLabel: "添加清单",
  });
  const pushList = (host, entry) => item(host, {
    id: entry.name, icon: iconHtml(entry), label: entry.name,
    count: listCounts[entry.name] || 0, active: selection.list === entry.name, kind: "list",
    onClick: () => onSelect({ list: entry.name }),
    actions: [
      { icon: "＋", title: "在这个清单里加任务", onClick: () => onQuickAdd(entry.name) },
      { icon: "…", title: "清单操作", onClick: (anchor) => openRowMenu(anchor, [
        { label: "编辑", onClick: () => onListAction("edit", entry) },
        { label: entry.pinned ? "取消置顶" : "置顶", onClick: () => onListAction("pin", entry) },
        { label: "复制", onClick: () => onListAction("duplicate", entry) },
        { label: "删除", onClick: () => onListAction("delete", entry) },
      ]) },
    ],
  });
  for (const tree of listTree(lists, folders)) {
    if (!tree.folder) {
      for (const entry of tree.lists) pushList(listBody, entry);
      continue;
    }
    const closed = Boolean(collapsed[`folder:${tree.folder}`]);
    const folderBody = group(listBody, {
      label: `${tree.folder}`, collapsed: closed,
      onToggle: () => onToggleGroup(`folder:${tree.folder}`),
      hint: `${tree.lists.length} 个`,
    });
    // 文件夹那一行的「…」：重命名 / 解散（解散只把清单放回根级，不删清单）
    const head = folderBody.parentElement.querySelector(".time-group-head");
    const tools = document.createElement("span");
    tools.className = "time-side-tools";
    const more = document.createElement("button");
    more.type = "button";
    more.className = "time-side-tool";
    more.textContent = "…";
    more.title = "文件夹操作";
    more.setAttribute("aria-label", `文件夹操作：${tree.folder}`);
    more.addEventListener("click", (event) => {
      event.stopPropagation();
      openRowMenu(more, [
        { label: "重命名", onClick: () => onFolderAction("rename", tree.folder) },
        { label: "解散文件夹（清单回到根级）", onClick: () => onFolderAction("dissolve", tree.folder) },
      ]);
    });
    tools.append(more);
    head.append(tools);
    for (const entry of tree.lists) pushList(folderBody, entry);
  }

  // 三、标签管理
  const tagBody = group(container, {
    label: "标签管理", collapsed: collapsed.tags, onToggle: () => onToggleGroup("tags"),
    hint: tags.length ? `${tags.length} 个` : "", onAdd: onAddTag, addLabel: "添加标签",
  });
  for (const tag of tags) {
    item(tagBody, {
      id: tag.name, icon: iconHtml(tag), label: tag.name, count: tagCounts[tag.name] || 0,
      active: selection.tag === tag.name, kind: "tag",
      onClick: () => onSelect({ tag: tag.name }),
      actions: [{
        icon: "…", title: "标签操作",
        onClick: (anchor) => openRowMenu(anchor, [
          { label: "编辑", onClick: () => onTagAction("edit", tag) },
          { label: tag.pinned ? "取消置顶" : "置顶", onClick: () => onTagAction("pin", tag) },
          { label: "删除", onClick: () => onTagAction("delete", tag) },
        ]),
      }],
    });
  }
  if (!tags.length) {
    const empty = document.createElement("p");
    empty.className = "time-side-empty field-help";
    empty.textContent = "还没有标签：点上面的 ＋，或在添加栏的「标签」里输入名字。";
    tagBody.append(empty);
  }
}
