import { applyNotebookOrder, notebooksFor } from "./notebook-model.js";

export function buildMoveFolders(catalog) {
  return [["life", "人生路"], ["knowledge", "知识库"]].map(([area, title]) => {
    const rows = catalog[area] || [];
    const titles = catalog.titles?.[area] || {};
    const root = { key: area, area, title, children: [] };
    const folders = new Map(rows.map((row) => [row.key, {
      key: row.key, title: row.title, path: row.path, area, children: [],
    }]));
    const grouped = new Set();
    for (const book of notebooksFor(area)) {
      const children = book.chapters.map((key) => folders.get(key)).filter(Boolean);
      if (!children.length) continue;
      book.chapters.forEach((key) => grouped.add(key));
      const name = titles[book.key] || book.title;
      if (book.chapters.length === 1) {
        children[0].title = name;
        root.children.push(children[0]);
      } else {
        // A display group can share a key with its first real folder (反思复盘 / 反思).
        root.children.push({ key: book.key, title: name, area, children });
      }
    }
    for (const row of rows) {
      if (grouped.has(row.key)) continue;
      const parent = folders.get(row.parent || row.chapter) || root;
      const folder = folders.get(row.key);
      folder.title = titles[row.key] || row.title;
      parent.children.push(folder);
    }
    root.children = applyNotebookOrder(root.children, catalog.order?.[area]);
    return root;
  });
}
