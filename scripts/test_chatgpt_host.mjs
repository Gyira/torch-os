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
