import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

// 恋爱系统 v2：生活运营下的二级分区，七个页面；数据在本机。
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const source = (name) => readFile(path.join(frontend, name), "utf8");

test("恋爱系统只剩七个页面，并挂在生活运营下", async () => {
  const html = await source("index.html");
  const app = await source("app.js");
  const nav = await source("nav-order.js");

  assert.match(html, /data-pane="love-view"/);
  assert.match(html, /id="love-view"/);
  assert.match(html, /data-module="ops"[^>]*data-pane-label="恋爱系统"|data-pane-label="恋爱系统"/);
  for (const [key, label, number] of [
    ["memories", "恋爱记忆", "01"],
    ["chat", "微信聊天", "02"],
    ["schedule", "排班日历", "03"],
    ["cycle", "经期关怀", "04"],
    ["gallery", "恋爱相册", "05"],
    ["bazi", "八字合婚", "06"],
    ["master", "恋爱大师", "07"],
  ]) {
    assert.match(html, new RegExp(`data-love-page="${key}"[^>]*>\\s*<span>${number}</span>${label}`),
      `缺少 ${number} ${label}`);
  }
  for (const old of ["dashboard", "import", "wechat", "stats", "analysis", "profile",
    "journal", "memory", "report", "settings"]) {
    assert.equal(html.includes(`data-love-page="${old}"`), false, `旧页面不应保留：${old}`);
  }
  assert.match(html, /id="love-settings-open"/);
  assert.match(html, /id="love-settings-drawer"/);
  assert.match(html, /id="love-settings-backdrop"/);

  const opsSub = html.slice(html.indexOf('id="nav-sub-ops"'), html.indexOf('data-view="knowledge"'));
  assert.ok(opsSub.includes('data-pane="love-view"'));
  const labels = nav.match(/NAV_LABELS = \{([^}]*)\}/)[1];
  assert.equal(labels.includes("恋爱系统"), false);
  assert.match(app, /"love-view": \(\) => \{ initLovePane\(\); loadLovePane\(\); \}/);
});

test("恋爱页面不违反工作台的内联样式 CSP", async () => {
  const love = await source("love.js");
  assert.equal(/style\s*=/.test(love), false, "CSP style-src 'self' 会阻止内联 style 属性");
  assert.equal(/<style\b/.test(love), false, "页面内不应写 <style>");
  assert.equal(/srcdoc/.test(love), false);
});

test("恋爱系统只调用本机原生 API，不直接访问外部 AI", async () => {
  const love = await source("love.js");
  for (const endpoint of [
    "/api/love/status",
    "/api/love/memory/overview",
    "/api/love/memory/notes",
    "/api/love/memory/export",
    "/api/love/chat/status",
    "/api/love/chat/calendar",
    "/api/love/chat/day",
    "/api/love/chat/sync",
    "/api/love/chat/export",
    "/api/love/schedule/month",
    "/api/love/schedule/override",
    "/api/love/gallery/overview",
    "/api/love/gallery/upload",
    "/api/love/bazi/generate",
    "/api/love/bazi/history",
    "/api/love/master/run",
    "/api/love/master/overview",
    "/api/love/weflow/component/status",
  ]) {
    assert.ok(love.includes(endpoint), `缺少接口：${endpoint}`);
  }
  assert.equal(love.includes("api.deepseek.com"), false);
  assert.equal(love.includes("/api/love/settings/api-key"), false);
  // 唯一允许的外部链接是微信组件修复文档里标注的开源项目地址。
  const withoutDocs = love.replace(/https:\/\/github\.com\/olansoft\/weflow-wcdb-api-oss/g, "");
  assert.equal(/https?:\/\//.test(withoutDocs), false);
});

test("恋爱记忆是首页，本子/编辑器/导出/删除入口齐全", async () => {
  const html = await source("index.html");
  const love = await source("love.js");
  const app = await source("app.js");
  assert.match(html, /<h2 id="love-title">恋爱记忆<\/h2>/);
  assert.match(love, /memories: "恋爱记忆"/);
  assert.match(love, /data-love-action="memory-new"/);
  assert.match(love, /写点什么/);
  assert.match(love, /openNoteEditor\(\{ context: "love", chapter: "love_journal" \}\)/);
  assert.match(love, /data-love-action="memory-create-notebook"/);
  assert.match(love, /data-love-action="memory-delete-notebook"/);
  assert.match(love, /\/api\/love\/memory\/export/);
  assert.match(love, /AI 草稿/);
  assert.match(love, /memory-promote-drafts/);
  // 默认进入恋爱记忆，而不是旧总览。
  assert.match(love, /page: "memories"/);
  assert.match(app, /loadLovePane/);
});

test("微信聊天页是单联系人、日历看板与按日查看", async () => {
  const love = await source("love.js");
  for (const className of [
    "love-chat-calendar", "love-chat-day", "is-synced", "is-unsynced", "is-no_chat",
    "love-chat-messages", "love-chat-bubble",
  ]) {
    assert.ok(love.includes(className), `缺少聊天页标记：${className}`);
  }
  assert.match(love, /data-love-action="chat-day"/);
  assert.match(love, /\/api\/love\/chat\/sessions/);
  assert.match(love, /\/api\/love\/chat\/bind/);
  assert.match(love, /\/api\/love\/chat\/export/);
  assert.match(love, /love-chat-rebind|chat-rebind/);
  assert.doesNotMatch(love, /导入聊天|微信同步|ChatLab/);
});

test("排班日历支持单日手动覆盖，经期页仍走独立接口", async () => {
  const love = await source("love.js");
  assert.match(love, /schedule: "排班日历"/);
  assert.match(love, /\/api\/love\/schedule\/month/);
  assert.match(love, /\/api\/love\/schedule\/override/);
  assert.match(love, /\/api\/love\/schedule\/note/);
  assert.match(love, /data-love-action="schedule-day"/);
  assert.match(love, /renderLoveCycle/);
  assert.match(love, /handleLoveCycleAction/);
  assert.doesNotMatch(love, /交路|时刻表|沿线天气/);
});

test("恋爱相册支持上传、相簿、全览与删除", async () => {
  const love = await source("love.js");
  for (const action of [
    "gallery-upload", "gallery-create-album", "gallery-album", "gallery-open",
    "gallery-bulk-move", "gallery-bulk-delete", "gallery-delete-album",
  ]) {
    assert.ok(love.includes(action), `缺少相册动作：${action}`);
  }
  assert.match(love, /captureVideoThumb/);
  assert.match(love, /love-gallery-grid/);
});

test("八字合婚有男女双方输入、AI 分析与历史页面", async () => {
  const love = await source("love.js");
  assert.match(love, /baziPersonFields\("him"/);
  assert.match(love, /baziPersonFields\("her"/);
  assert.match(love, /\/api\/love\/bazi\/generate/);
  assert.match(love, /\/api\/love\/bazi\/history/);
  assert.match(love, /data-love-action="bazi-select"/);
  assert.match(love, /data-love-action="bazi-delete"/);
  assert.match(love, /reports\/\$\{report\.id\}\/html/);
  for (const className of ["love-bazi-hero", "love-bazi-pair", "love-bazi-points", "love-bazi-years"]) {
    assert.ok(love.includes(className), `缺少八字样式标记：${className}`);
  }
});

test("恋爱大师有看板、skill 卡片、历史与唯一聊天入口", async () => {
  const love = await source("love.js");
  for (const marker of [
    "masterTrendChart", "data-love-action=\"master-run\"", "data-skill=\"portrait\"",
    "data-skill=\"deep_analysis\"", "data-skill=\"report\"", "data-skill=\"journal_batch\"",
    "data-love-action=\"master-chat\"", "data-love-action=\"master-select\"",
    "/api/love/master/runs/", "love-skill-card",
    'id="love-master-journal-days"', '<option value="0">全部未补日期',
    "journal_pending", "不脱敏",
  ]) {
    assert.ok(love.includes(marker), `缺少大师页标记：${marker}`);
  }
  assert.ok(!love.includes('type="number" min="1" max="60"'), "补日记不应再把范围锁死在 60 天");
  // 右侧角色层是唯一聊天入口：编辑器/其他页面不再打开第二个角色层。
  const editor = await source("note-editor.js");
  assert.match(editor, /button\.hidden = loveContext/);
});

test("设置抽屉覆盖 WeFlow、组件修复、数据目录与 AI 状态", async () => {
  const love = await source("love.js");
  for (const text of [
    "WeFlow 连接", "微信组件修复", "WeFlow 安装目录", "olansoft/weflow-wcdb-api-oss",
    "数据目录", "AI 与隐私", "love-settings-drawer",
  ]) {
    assert.ok(love.includes(text), `缺少设置内容：${text}`);
  }
  assert.match(love, /\/api\/love\/weflow\/component\/install-dir/);
  assert.match(love, /\/api\/love\/weflow\/component\/replace/);
  assert.match(love, /\/api\/love\/weflow\/component\/rollback/);
  // 没有内置 DLL 时不能静默无反应：要解释并给出“选择已有 DLL”的出口。
  assert.match(love, /还没有内置 DLL/);
  assert.match(love, /选择已有 DLL/);
  assert.match(love, /love-settings-body.*addEventListener\("click"/s);
});
