// 个性化数据桥：权威副本在 <个人数据库>/.torch-os/personal-data.json，localStorage 只是本窗口缓存。
//
// 启动顺序（app.js）：先 initPersonalData() 把库里的值灌进 localStorage，再初始化各小组件；
// 之后任何 `localStorage.setItem/removeItem` 都会防抖镜像回后端。这样换一台电脑、
// 或换一个浏览器打开同一个个人数据库，小组件偏好/城市/排序都还在。
import { api, jsonBody } from "./core.js";

export const PERSONAL_DATA_URL = "/api/personal-data";
const MIRROR_DELAY = 400;
const RETRY_DELAY = 5000;
// 天气缓存是 30 分钟就过期的纯缓存，不属于“用户个性化设置”，不往库里写。
const SKIP_KEYS = new Set(["torch-os:weather"]);
// 缓存归属：Obsidian 里不同个人数据库可能共用同一个 localStorage origin，
// 记下当前缓存属于哪个 Vault，换库时先清缓存再灌新库的值（不是 torch 前缀，不会被镜像）。
const OWNER_KEY = "__torch_personal_owner";

let mirrorTimer = 0;
let flushing = false;
let mirrorInstalled = false;
let hydrating = false;
const pending = new Map();
// localStorage 配额满时（常见于两张本机背景图），个性化键退到内存覆盖层；
// 权威副本仍会写进 .torch-os，所以本页和下次启动都能读到。
const fallback = new Map();

function localStorageRef() {
  try {
    return globalThis.localStorage || globalThis.window?.localStorage || null;
  } catch {
    return null;
  }
}

/** 只接管 torch-os 自己的键；别的应用/插件写到同一域下的 localStorage 不碰。 */
export function isPersonalKey(key) {
  return typeof key === "string" && /^(?:torch-os:|torch:|torch\.)[A-Za-z0-9:._-]{1,200}$/.test(key);
}

function localKeys(store) {
  const keys = [];
  if (typeof store.length === "number" && typeof store.key === "function") {
    for (let index = 0; index < store.length; index += 1) {
      const key = store.key(index);
      if (key) keys.push(key);
    }
    return keys;
  }
  return Object.keys(store || {}).filter((key) => typeof key === "string");
}

function schedule(delay = MIRROR_DELAY) {
  const setTimer = globalThis.setTimeout || globalThis.window?.setTimeout;
  const clearTimer = globalThis.clearTimeout || globalThis.window?.clearTimeout;
  if (!setTimer) return;
  if (mirrorTimer) clearTimer?.(mirrorTimer);
  mirrorTimer = setTimer(flush, delay);
}

async function flush() {
  mirrorTimer = 0;
  if (flushing) { schedule(); return; }
  if (!pending.size) return;
  flushing = true;
  const values = Object.fromEntries(pending);
  pending.clear();
  let failed = false;
  try {
    await api(PERSONAL_DATA_URL, { ...jsonBody("PUT", { values }), timeoutMs: 8000 });
  } catch (error) {
    // 4xx（只读库、键不合法、超限）重试也没用：放弃这一批，避免每 5 秒撞一次 409。
    // 网络错误/5xx 才留在待写队列里稍后重试；页面继续用 localStorage，不阻塞操作。
    if (!(Number(error?.status) >= 400 && Number(error?.status) < 500)) {
      failed = true;
      for (const [key, value] of Object.entries(values)) {
        if (!pending.has(key)) pending.set(key, value);
      }
    }
  } finally {
    flushing = false;
    if (pending.size) schedule(failed ? RETRY_DELAY : MIRROR_DELAY);
  }
}

function queue(values) {
  let queued = false;
  for (const [key, value] of Object.entries(values || {})) {
    if (!isPersonalKey(key) || SKIP_KEYS.has(key)) continue;
    pending.set(key, value === null ? null : String(value));
    queued = true;
  }
  if (queued) schedule();
}

/** 页面即将关闭/隐藏时，用 keepalive fetch 把还没到防抖时间的写入发出去。 */
function flushPendingOnHide() {
  if (!pending.size || typeof fetch !== "function") return;
  const body = JSON.stringify({ values: Object.fromEntries(pending) });
  // keepalive 请求体有约 64KB 上限；大图 data URL 留给本地缓存，下次启动再同步。
  if (body.length > 60_000) return;
  const sent = new Map(pending);
  pending.clear();
  try {
    fetch(PERSONAL_DATA_URL, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body,
      keepalive: true,
    }).catch(() => { /* 页面已经走了，失败时只能依赖 localStorage */ });
  } catch {
    for (const [key, value] of sent) if (!pending.has(key)) pending.set(key, value);
  }
}

/** 页面里所有个性化键的写入都会经过这里（patch Storage.prototype，只对当前 localStorage 生效）。 */
function installMirror() {
  if (mirrorInstalled) return;
  const store = localStorageRef();
  const proto = globalThis.Storage?.prototype;
  if (!store || !proto || typeof proto.setItem !== "function") return;
  const originalSet = proto.setItem;
  const originalGet = proto.getItem;
  const originalRemove = proto.removeItem;
  proto.setItem = function patchedSetItem(key, value) {
    const text = String(value);
    const name = String(key);
    try {
      originalSet.call(this, key, text);
      fallback.delete(name);
    } catch (error) {
      // 只对个性化键兜底：配额满时仍能改设置，值由 .torch-os 保存。
      if (!(this === store && isPersonalKey(name))) throw error;
      fallback.set(name, text);
    }
    if (this === store && !hydrating) queue({ [name]: text });
  };
  proto.getItem = function patchedGetItem(key) {
    const name = String(key);
    const stored = originalGet.call(this, key);
    if (stored === null && this === store && fallback.has(name)) return fallback.get(name);
    return stored;
  };
  proto.removeItem = function patchedRemoveItem(key) {
    const name = String(key);
    originalRemove.call(this, key);
    fallback.delete(name);
    if (this === store && !hydrating && isPersonalKey(name)) queue({ [name]: null });
  };
  const win = globalThis.window || globalThis;
  if (typeof win.addEventListener === "function") win.addEventListener("pagehide", flushPendingOnHide);
  const doc = globalThis.document;
  if (typeof doc?.addEventListener === "function") {
    doc.addEventListener("visibilitychange", () => {
      if (doc.visibilityState === "hidden") flushPendingOnHide();
    });
  }
  mirrorInstalled = true;
}

/**
 * 启动时同步一次：后端有值的键覆盖本机缓存；本机多出来的键（升级前的老数据）推回后端。
 * 确认读到后端后才开启镜像；读不到就用本机缓存启动，本次会话不补写，下次启动再同步。
 */
export async function initPersonalData() {
  const store = localStorageRef();
  let remote = {};
  let loaded = false;
  let vaultId = "";
  try {
    // 只等 3 秒：后端慢/不在时先用本机缓存把页面开起来，下次启动再同步。
    const payload = await api(PERSONAL_DATA_URL, { timeoutMs: 3000 });
    if (payload?.values && typeof payload.values === "object") remote = payload.values;
    vaultId = typeof payload?.vault_id === "string" ? payload.vault_id : "";
    loaded = true;
  } catch {
    remote = {};
  }
  const toPush = {};
  let switched = false;
  hydrating = true;
  try {
    // 没读到后端时不要拿本机缓存去“补写”：可能只是超时，补写会覆盖库里更新的值。
    if (store && loaded) {
      // 先把升级前的本机值拍个快照：下面灌库里的值会覆盖同名键，快照用于首次迁移。
      const snapshot = {};
      for (const key of localKeys(store)) {
        if (!isPersonalKey(key) || SKIP_KEYS.has(key)) continue;
        try { snapshot[key] = store.getItem(key) ?? ""; } catch { /* ignore */ }
      }
      const owner = store.getItem?.(OWNER_KEY) || "";
      const sameVault = Boolean(vaultId) && owner === vaultId;
      // 没有归属标记但本机已有设置 = 升级后第一次运行（本机是旧数据的来源）。
      const firstUpgrade = Boolean(vaultId) && !owner && Object.keys(snapshot).length > 0;
      switched = Boolean(vaultId) && !sameVault && !firstUpgrade;
      if (switched) {
        // 同一浏览器 origin 可能先后打开过多个个人数据库：先清掉上一个库的缓存。
        for (const key of localKeys(store)) {
          if (isPersonalKey(key)) { try { store.removeItem(key); } catch { /* ignore */ } }
        }
      }
      for (const [key, value] of Object.entries(remote)) {
        if (!isPersonalKey(key) || SKIP_KEYS.has(key) || typeof value !== "string") continue;
        // 首次升级时本机旧值优先，库里的值只补本机没有的键。
        if (firstUpgrade && Object.hasOwn(snapshot, key)) continue;
        try { store.setItem(key, value); } catch { fallback.set(key, value); }
      }
      if (!switched) {
        if (firstUpgrade) {
          Object.assign(toPush, snapshot);
        } else {
          for (const key of localKeys(store)) {
            if (!isPersonalKey(key) || SKIP_KEYS.has(key) || Object.hasOwn(remote, key)) continue;
            try { toPush[key] = store.getItem(key) ?? ""; } catch { /* ignore */ }
          }
        }
      }
      if (vaultId) { try { store.setItem(OWNER_KEY, vaultId); } catch { /* ignore */ } }
    }
  } finally {
    hydrating = false;
  }
  // 只有确认读到了库里的数据才开启镜像：否则小组件启动时写默认值可能覆盖库里更新的设置。
  // 这次会话先用本机缓存；下次成功连上后端时，未同步的本机键会作为“local-only”补写。
  if (loaded) installMirror();
  if (Object.keys(toPush).length) queue(toPush);
  return {
    synced: loaded,
    vault: vaultId,
    switched,
    remote: loaded ? Object.keys(remote).length : 0,
    pushed: Object.keys(toPush).length,
  };
}
