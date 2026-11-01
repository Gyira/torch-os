import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("the AI role manager has one shell, one nav entry and one stylesheet", async () => {
  const html = await readFile(path.join(frontend, "index.html"), "utf8");
  const app = await readFile(path.join(frontend, "app.js"), "utf8");
  const css = await readFile(path.join(frontend, "role-admin.css"), "utf8");

  assert.match(html, /<section id="roles-view"[^>]*data-module="roles"[^>]*>/);
  assert.match(html, /class="nav-item" data-view="roles"/);
  assert.match(html, /href="\/assets\/role-admin\.css"/);
  assert.match(html, /id="role-admin-list"/);
  assert.match(html, /id="role-admin-editor"/);
  assert.match(html, /id="role-admin-legacy-note"/);
  assert.match(html, /id="role-admin-internal-note"/);
  assert.match(app, /roles: \{ panes: \[\], ownNav: true \}/);
  assert.match(app, /"roles-view": \(\) => \{ initRoleAdmin\(\); loadRoleAdmin\(\); \}/);
  assert.match(app, /roles: \(\) => confirmRoleAdminLeave\(\)/);
  assert.match(css, /\.roles-view\[hidden\] \{ display: none; \}/);
});

test("the role manager only talks to its own endpoints and never uses inline styles", async () => {
  const source = await readFile(path.join(frontend, "role-admin.js"), "utf8");
  const model = await readFile(path.join(frontend, "role-admin-model.js"), "utf8");
  assert.equal(/style=/.test(source), false);
  assert.equal(/<style/.test(source), false);
  assert.match(source, /\/api\/ai\/roles/);
  assert.match(source, /\/profile/);
  assert.equal(/api\/ai\/chat/.test(source), false);
  assert.equal(/localStorage/.test(model), false);
  assert.equal(/document\./.test(model), false);
});
