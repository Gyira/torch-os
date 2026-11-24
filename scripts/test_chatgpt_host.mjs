import test from 'node:test';
import assert from 'node:assert/strict';
import Module, { createRequire } from 'node:module';
const original = Module._load;
Module._load = function(name, ...args) {
  if (name === 'obsidian') return { Plugin: class {}, ItemView: class {}, PluginSettingTab: class {} };
  return original.call(this, name, ...args);
};
const { WorkbenchView } = createRequire(import.meta.url)('../obsidian-plugin/main.js')._test;
Module._load = original;
test('native guest is isolated, reused, clipped and disposed', async () => {
  let created = 0, removed = false;
  const guest = { attrs: {}, style: {}, setAttribute(k,v) { this.attrs[k]=v; }, addEventListener() {}, remove() { removed=true; } };
  const before = globalThis.document;
  const beforeWindow = globalThis.window;
  globalThis.window = { setTimeout:()=>1, clearTimeout() {} };
  globalThis.document = { createElement(tag) { assert.equal(tag,'webview'); created++; return guest; } };
  try {
    const leaf = {}, view = new WorkbenchView(leaf, { instanceUrl:'http://127.0.0.1:8765' });
    view.leaf = leaf; view.app = {workspace:{activeLeaf:leaf}};
    view.contentEl = { appendChild() {}, getBoundingClientRect:()=>({left:0,top:0}) };
    view.frame = { getBoundingClientRect:()=>({left:0,top:0,right:800,bottom:600}), contentWindow:{postMessage(){}} };
    view.chatgptMessage({action:'bounds',visible:true,rect:{x:20,y:100,width:900,height:900}});
    assert.equal(created,1);
    assert.equal(guest.attrs.src,'https://chatgpt.com/');
    assert.equal(guest.attrs.partition,'persist:torch-chatgpt');
    assert.equal(guest.attrs.preload,undefined);
    assert.equal(guest.attrs.nodeintegration,undefined);
    assert.equal(guest.attrs.allowpopups,undefined);
    assert.equal(guest.style.width,'780px'); assert.equal(guest.style.height,'500px');
    view.chatgptMessage({action:'bounds',visible:false}); assert.equal(guest.hidden,true);
    view.chatgptMessage({action:'bounds',visible:true,rect:{x:0,y:0,width:300,height:400}});
    assert.equal(created,1); assert.equal(guest.hidden,false);
    view.app.workspace.activeLeaf={};
    view.chatgptMessage({action:'bounds',visible:true,rect:{x:0,y:0,width:300,height:400}});
    assert.equal(guest.hidden,true);
    await view.onClose(); assert.equal(removed,true); assert.equal(view.chatgpt,null);
  } finally { globalThis.document=before; globalThis.window=beforeWindow; }
});

test('workbench warms both sites, retains them on frame reload, and replays readiness', async () => {
  const previousDocument = globalThis.document, previousWindow = globalThis.window;
  const guests = [], messages = [];
  const frame = {
    setAttribute() {}, addEventListener() {},
    contentWindow: { postMessage: message => messages.push(message) },
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 800, height: 600, right: 800, bottom: 600 }),
  };
  globalThis.window = { setTimeout: () => 1, clearTimeout() {} };
  globalThis.document = {
    body: { classList: { contains: () => false } },
    createElement() {
      const listeners = {};
      const guest = {
        hidden: false, style: {}, listeners,
        setAttribute(key, value) { this[key] = value; },
        getURL() { return this.src; },
        addEventListener(key, callback) { listeners[key] = callback; },
        remove() { this.removed = true; },
      };
      guests.push(guest);
      return guest;
    },
  };
  try {
    const leaf = {}, view = new WorkbenchView(leaf, {
      settings: {}, uiUrl: 'http://127.0.0.1:9000', ensureUiServer: async () => {},
    });
    view.leaf = leaf;
    view.app = {workspace: {activeLeaf: leaf, on() {}}};
    view.registerDomEvent = view.registerEvent = () => {};
    view.contentEl = {
      empty() {}, addClass() {}, appendChild() {}, createEl: () => frame,
      getBoundingClientRect: frame.getBoundingClientRect,
    };
    await view.onOpen();
    assert.equal(guests.length, 2);
    const dida = view.didaGuest, chat = view.chatgpt;
    for (const guest of guests) {
      guest.listeners['dom-ready']();
      assert.equal(guest.hidden, true);
    }
    const rect = {x: 0, y: 5, width: 700, height: 500};
    for (let pass = 0; pass < 2; pass++) {
      messages.length = 0;
      view.didaMessage({action: 'bounds', rect});
      assert.equal(dida.hidden, false);
      assert.equal(dida.style.transformOrigin, 'top left');
      assert.equal(dida.style.transform, 'scale(0.85)');
      assert.ok(Math.abs(parseFloat(dida.style.height) * 0.85 - rect.height) < 0.001);
      assert.ok(Math.abs(parseFloat(dida.style.width) * 0.85 - rect.width) < 0.001);
      assert.deepEqual(messages.at(-1), {type: 'torch:dida-state', message: ''});
      const menuRect = {...rect, exclusions: [{left: 80, top: 0, right: 260, bottom: 100}]};
      view.didaMessage({action: 'bounds', rect: menuRect});
      assert.equal(dida.hidden, false);
      assert.match(dida.style.clipPath, /^path/);
      assert.ok(Math.abs(parseFloat(dida.style.width) * 0.85 - rect.width) < 0.001);
      view.chatgptMessage({action: 'bounds', rect: menuRect, visible: true});
      assert.equal(chat.hidden, false);
      assert.match(chat.style.clipPath, /^path/);
      assert.equal(chat.style.width, rect.width + 'px');
      view.chatgptMessage({action: 'bounds', rect: null, visible: false});
      view.didaMessage({action: 'bounds', rect: null});
      view.chatgptMessage({action: 'bounds', visible: true, rect});
      assert.equal(chat.hidden, false);
      assert.equal(messages.at(-1).state, 'ready');
      view.reload();
      assert.equal(chat.hidden, true);
      assert.equal(dida.hidden, true);
    }
    assert.equal(guests.length, 2);
    await view.onClose();
    assert.ok(guests.every(guest => guest.removed));
  } finally {
    globalThis.document = previousDocument;
    globalThis.window = previousWindow;
  }
});
