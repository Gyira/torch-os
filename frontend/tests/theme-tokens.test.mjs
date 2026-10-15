import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

// 静态守卫：样式表里只允许调色板定义颜色。任何一个新写的 #hex 都会让这条测试失败，
// 从而保证“跟随 dsh 主题”不会因为某个漏网的颜色而破功。

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const HOST_SELECTOR = 'html[data-torch-host="dsh"]';

const css = await readFile(path.join(frontend, "styles.css"), "utf8");
const hostStart = css.indexOf(HOST_SELECTOR);
const hostEnd = css.indexOf("}", hostStart);
const palette = css.slice(0, hostEnd + 1);
const body = css.slice(hostEnd + 1);

function blockTokens(block) {
  return new Set(block.match(/--p-[a-z0-9-]+(?=\s*:)/g) || []);
}

const lightBlock = /:root\s*\{([^}]*)\}/.exec(palette);
const darkBlock = /html\[data-torch-theme="dark"\]\s*\{([^}]*)\}/.exec(palette);
const hostBlock = palette.slice(hostStart, hostEnd + 1);

test("styles.css 的颜色只出现在调色板里", () => {
  assert.ok(hostStart > 0, "找不到 dsh 宿主层");
  const stray = body.match(/#[0-9a-fA-F]{3,8}\b/g) || [];
  assert.deepEqual(stray, [], `调色板之外还有硬编码颜色：${stray.join(", ")}`);
  assert.ok(body.length > 5000, "样式主体没读到");
});

test("明暗两套调色板定义同一批 token", () => {
  assert.ok(lightBlock, "找不到 :root 调色板");
  assert.ok(darkBlock, "找不到暗色调色板");
  const light = blockTokens(lightBlock[1]);
  const dark = blockTokens(darkBlock[1]);
  assert.ok(light.size >= 50, `token 太少：${light.size}`);
  assert.deepEqual([...dark].sort(), [...light].sort(), "暗色缺少或多出 token");
});

test("样式主体引用的 token 都有定义，定义的 token 都有使用", () => {
  const defined = blockTokens(lightBlock[1]);
  const referenced = new Set((body.match(/var\((--p-[a-z0-9-]+)\)/g) || [])
    .map((item) => item.slice(4, -1)));
  for (const name of referenced) {
    assert.ok(defined.has(name), `${name} 没有定义`);
  }
  for (const name of defined) {
    assert.ok(body.includes(name), `${name} 定义了却没用上`);
  }
});

test("宿主层只消费 dsw token，不写死颜色", () => {
  const referenced = hostBlock.match(/var\((--[a-z0-9-]+)/g) || [];
  assert.ok(referenced.length > 20, `宿主层引用太少：${referenced.length}`);
  for (const item of referenced) {
    assert.ok(item.startsWith("var(--dsw-"), `宿主层引用了非 dsh 变量：${item}`);
  }
  for (const token of ["--p-bg", "--p-surface", "--p-ink", "--p-invert-bg", "--p-accent-bg", "--p-line"]) {
    assert.ok(hostBlock.includes(`${token}:`), `宿主层缺少 ${token}`);
  }
});

test("首帧提示与主题桥都接在启动路径上", async () => {
  const boot = await readFile(path.join(frontend, "embed-boot.js"), "utf8");
  const app = await readFile(path.join(frontend, "app.js"), "utf8");
  const html = await readFile(path.join(frontend, "index.html"), "utf8");
  assert.match(boot, /dataset\.torchTheme/, "embed-boot 要先给首帧定主题");
  assert.match(boot, /prefers-color-scheme: dark/, "独立打开时跟随系统");
  assert.match(app, /import \{ initThemeBridge \} from "\.\/theme-bridge\.js"/);
  assert.match(app, /^initThemeBridge\(\);/m);
  assert.match(html, /name="color-scheme" content="light dark"/);
  assert.ok(
    html.indexOf("embed-boot.js") < html.indexOf("styles.css"),
    "embed-boot 必须在样式表之前同步执行，否则首帧会闪",
  );
});
