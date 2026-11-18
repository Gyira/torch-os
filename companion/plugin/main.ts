import { Plugin, PluginSettingTab, Setting, Notice, Modal, ItemView, WorkspaceLeaf, TFile, requestUrl } from "obsidian";
import { BaiduApi } from "../baidu/api.ts";
import { SecureTransport } from "../core/secure-transport.ts";
import { SyncEngine } from "../core/engine.ts";
import type { Issue } from "../core/engine.ts";
import { PluginJournal, PluginState, VaultNotes, ObsidianWire } from "./adapters.ts";
import { exportPair, importPair } from "../core/pairing.ts";
import { capturePage, captureText } from "../core/share.ts";
import type { Capture } from "../core/share.ts";
import { inspect } from "../core/protocol.ts";
import { hash } from "../core/protocol.ts";
import { queueEnvelope, queueDecode, queueRequest } from "../core/queue.ts";
import { contextPack, changedSources } from "../core/context-pack.ts";
import type { Pack } from "../core/context-pack.ts";
import { readableExport } from "../core/export.ts";
import { RetryGate, bounded } from "../core/retry.ts";
import { directories } from "../core/catalog.ts";

type Settings = { device: string; workspace: string; account: string; root: string; appKey: string; appSecret: string; access: string; refresh: string; expires: number;
  password: string; remember: boolean; autoHome: boolean; autoSync: boolean; excludes: string; renames: { from: string; to: string }[] };
const HOME = "torch-companion-home", encode = (s: string) => new TextEncoder().encode(s);
// Retained across plugin reloads in the same Obsidian JavaScript host.
const executorKey = Symbol.for("torch.companion.executors.v1");
const host = globalThis as typeof globalThis & { [executorKey]?: WeakSet<object> };
const executors = host[executorKey] ??= new WeakSet<object>();
const defaults = (): Settings => ({ device: crypto.randomUUID(), workspace: "", account: "", root: "/apps/Torch/companion-test", appKey: "", appSecret: "", access: "", refresh: "", expires: 0,
  password: "", remember: false, autoHome: true, autoSync: false, excludes: "", renames: [] });
const message = (e: unknown) => e instanceof Error ? e.message : "操作失败";

class InputModal extends Modal {
  constructor(plugin: TorchCompanion, title: string, fields: { label: string; value?: string; secret?: boolean; multiline?: boolean }[], done: (values: string[]) => Promise<void>) {
    super(plugin.app);
    this.setTitle(title);
    const values = fields.map(f => f.value ?? "");
    fields.forEach((field, i) => {
      const setting = new Setting(this.contentEl).setName(field.label);
      if (field.multiline) setting.addTextArea(input => input.setValue(values[i]).onChange(v => values[i] = v));
      else setting.addText(input => { input.setValue(values[i]).onChange(v => values[i] = v); if (field.secret) input.inputEl.type = "password"; });
    });
    new Setting(this.contentEl).addButton(button => button.setButtonText("继续").setCta().onClick(async () => {
      button.setDisabled(true);
      try { await done(values); this.close(); } catch (error) { new Notice(message(error)); } finally { button.setDisabled(false); }
    }));
  }
}

export default class TorchCompanion extends Plugin {
  declare settings: Settings; journal!: PluginJournal; state!: PluginState;
  status = "本地可用，尚未配对"; issues: Issue[] = []; busy = false; timer?: number;
  private saving: Promise<void> = Promise.resolve();
  private activeEngine?: SyncEngine;
  private activeKey = "";
  private retry = new RetryGate();
  private changes = 0;
  async onload() {
    const stored = (await this.loadData()) ?? {}, fresh = defaults();
    for (const [key, value] of Object.entries(fresh)) {
      if (stored[key] !== undefined && (typeof stored[key] !== typeof value || key === "renames" && !Array.isArray(stored[key]))) throw new Error("Torch 本机配置损坏，请先备份插件目录并重新配对");
    }
    this.settings = { ...fresh, ...stored };
    this.journal = new PluginJournal(this); this.state = new PluginState(this.journal);
    this.registerView(HOME, leaf => new Home(leaf, this));
    this.addSettingTab(new SettingsTab(this));
    this.addRibbonIcon("flame", "Torch 首页", () => { void this.home(); });
    this.addCommand({ id: "home", name: "打开首页", callback: () => { void this.home(); } });
    this.addCommand({ id: "sync", name: "立即同步", callback: () => { void this.sync(); } });
    this.addCommand({ id: "journal", name: "写今日日志", callback: () => { void this.journalNote(); } });
    this.addCommand({ id: "capture", name: "取回 AI 对话", callback: () => this.capture() });
    this.addCommand({ id: "queue", name: "交给电脑取回对话", callback: () => this.queueCapture() });
    this.addCommand({ id: "context-pack", name: "导出 ChatGPT 资料包", callback: () => this.contextExport() });
    this.addCommand({ id: "readable-export", name: "导出云端可读恢复副本", callback: () => { void this.recoveryExport().catch(e => new Notice(message(e))); } });
    const changed = () => {
      this.changes++;
      if (!this.busy) { this.status = "本机有变化，待同步；本地可继续编辑"; this.render(); }
      if (this.timer) window.clearTimeout(this.timer);
      this.timer = window.setTimeout(() => { if (this.settings.autoSync) void this.sync(true); }, 3000);
    };
    for (const event of ["create", "modify", "delete", "rename"] as const) this.registerEvent(this.app.vault.on(event as "modify", file => { if ((this.activeEngine?.notes as VaultNotes | undefined)?.allowed(file.path) ?? new VaultNotes(this).allowed(file.path)) changed(); }));
    this.registerEvent(this.app.vault.on("rename", (file, from) => {
      if (/\.(md|png|jpe?g|webp|gif|avif|pdf)$/i.test(file.path) && !from.startsWith("_Torch/") && !file.path.startsWith("_Torch/")) {
        this.settings.renames.push({ from, to: file.path }); void this.save();
      }
    }));
    this.registerInterval(window.setInterval(() => { if (this.settings.autoSync) void this.sync(true); }, 60000));
    this.registerDomEvent(document, "visibilitychange", () => { if (!document.hidden && this.settings.autoSync) void this.sync(true); });
    this.app.workspace.onLayoutReady(() => { if (this.settings.autoHome) void this.home(); if (this.settings.autoSync) void this.sync(true); });
  }
  onunload() { if (this.timer) window.clearTimeout(this.timer); }
  async save() {
    const snapshot = structuredClone({ ...this.settings, password: this.settings.remember ? this.settings.password : "" });
    const write = this.saving.then(() => this.saveData(snapshot));
    this.saving = write.catch(() => {}); await write;
  }
  render() { for (const leaf of this.app.workspace.getLeavesOfType(HOME)) (leaf.view as Home).render(); }
  async home() { const existing = this.app.workspace.getLeavesOfType(HOME)[0]; const leaf = existing ?? this.app.workspace.getLeaf("tab"); if (!existing) await leaf.setViewState({ type: HOME, active: true }); await this.app.workspace.revealLeaf(leaf); }
  async token(force = false): Promise<string> {
    if (!force && this.settings.access && Date.now() + 60000 < this.settings.expires) return this.settings.access;
    if (!this.settings.refresh) throw new Error("请先在插件设置中授权百度网盘");
    await this.oauth(new URLSearchParams({ grant_type: "refresh_token", refresh_token: this.settings.refresh, client_id: this.settings.appKey, client_secret: this.settings.appSecret }));
    return this.settings.access;
  }
  async oauth(form: URLSearchParams) {
    const r = await new ObsidianWire().send({ url: "https://openapi.baidu.com/oauth/2.0/token", method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: form.toString() });
    if (r.status !== 200) throw new Error("百度授权失败，请重新获取授权码");
    const data = JSON.parse(new TextDecoder().decode(r.bytes));
    if (typeof data.access_token !== "string" || !Number.isFinite(data.expires_in)) throw new Error("百度没有返回有效授权");
    this.settings.access = data.access_token; this.settings.refresh = data.refresh_token || this.settings.refresh; this.settings.expires = Date.now() + data.expires_in * 1000; await this.save();
  }
  async cloud(requireMarker = true) {
    if (!this.settings.workspace || !this.settings.account || !this.settings.password) throw new Error("尚未配对或同步密码未解锁，请打开插件设置");
    if (requireMarker && await this.markerIdentity() !== this.settings.workspace) throw new Error("当前笔记库身份缺失或不匹配；停止同步，请重新核对配对");
    const api = new BaiduApi(new ObsidianWire(), { token: force => this.token(force) }, this.settings.root);
    const binding = { protocol: 1 as const, workspace: this.settings.workspace, account: this.settings.account, remoteRoot: this.settings.root };
    return { api, binding, transport: await SecureTransport.open(api, binding, this.settings.password) };
  }
  async engine() {
    if (await this.markerIdentity() !== this.settings.workspace) throw new Error("工作区身份变化，停止同步以避免串库");
    const key = JSON.stringify([this.settings.workspace, this.settings.account, this.settings.root, this.settings.device, this.settings.password, this.settings.excludes]);
    if (this.activeEngine && key === this.activeKey) return this.activeEngine;
    if (this.activeEngine?.running) throw new Error("同步执行中，请稍后更改配置");
    const { binding, transport } = await this.cloud();
    const notes = new VaultNotes(this, this.settings.excludes.split("\n").map(s => s.trim()).filter(Boolean));
    notes.refreshScope();
    this.activeKey = key;
    this.activeEngine = new SyncEngine({ state: this.state, journal: this.journal, notes, remote: transport, binding, device: this.settings.device, atomic: notes });
    return this.activeEngine;
  }
  async sync(automatic = false) {
    if (this.busy || executors.has(this.app.vault) || automatic && !this.retry.allowed()) return;
    executors.add(this.app.vault);
    this.busy = true; this.status = "同步中，本地编辑可继续"; this.render();
    try {
      const count = this.settings.renames.length, observed = this.changes, engine = await this.engine();
      (engine.notes as VaultNotes).refreshScope();
      const result = await engine.sync(this.settings.renames.slice(0, count));
      this.settings.renames.splice(0, count); await this.save();
      this.issues = result.issues;
      const warnings = (engine.notes as VaultNotes).warnings;
      this.status = (this.issues.length ? `本地内容保留，有 ${this.issues.length} 项需处理` : observed !== this.changes ? "本轮网盘操作已确认；期间本机有变化，下一轮继续核对" : "网盘已确认；其他离线设备接收状态未知") + (warnings.length ? `；${warnings.length} 个附件超出预算，原件保留` : "");
      this.retry.success();
    }
    catch (error) { this.retry.failure(); this.status = "待同步：" + message(error); }
    finally { this.busy = false; executors.delete(this.app.vault); this.render(); }
  }
  async resolve(file: string, path: string, body: Uint8Array | null, heads: string[]) {
    if (this.busy || executors.has(this.app.vault)) throw new Error("上一轮同步尚未结束，请稍后确认");
    this.busy = true; executors.add(this.app.vault);
    try { await (await this.engine()).resolve(file, path, body, heads); }
    finally { this.busy = false; executors.delete(this.app.vault); }
  }
  async marker() {
    const path = "_Torch/工作区.md", vault = this.app.vault, file = vault.getAbstractFileByPath(path);
    if (file instanceof TFile) {
      if (await this.markerIdentity() !== this.settings.workspace) throw new Error("当前库身份与配对配置不一致");
    } else { if (!vault.getAbstractFileByPath("_Torch")) await vault.createFolder("_Torch"); await vault.create(path, `---\nstorage_version: 1\nworkspace_id: ${this.settings.workspace}\n---\n\n# Torch 工作区\n`); }
  }
  async markerIdentity() {
    const file = this.app.vault.getAbstractFileByPath("_Torch/工作区.md");
    if (!file) return null;
    if (!(file instanceof TFile) || file.stat.size > 8192) throw new Error("工作区身份文件无效");
    const text = (await this.app.vault.read(file)).replace(/\r\n/g, "\n");
    const match = /^---\n([\s\S]*?)\n---(?:\n|$)/.exec(text), data = match?.[1] ?? "";
    const ids = [...data.matchAll(/^workspace_id: ([a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12})$/gm)];
    if (ids.length !== 1 || !/^storage_version: 1$/m.test(data)) throw new Error("工作区身份损坏或协议版本不支持");
    return ids[0][1];
  }
  async initialize() {
    if (this.busy || (await this.state.load())) throw new Error("当前库已有同步状态，不能重新初始化");
    if (!this.settings.password || this.settings.password.length < 12) throw new Error("请设置至少 12 位同步密码并保存恢复材料");
    // Query the real account identity; a user-entered label is not authoritative.
    const response = await bounded(requestUrl({ url: "https://pan.baidu.com/rest/2.0/xpan/nas?method=uinfo&access_token=" + encodeURIComponent(await this.token()), throw: false }));
    if (response.status !== 200 || response.json.errno !== 0 || !response.json.baidu_name) throw new Error("无法验证百度账号身份");
    this.settings.account = String(response.json.uk ?? response.json.baidu_name);
    const marker = await this.markerIdentity();
    if (marker && this.settings.workspace && marker !== this.settings.workspace) throw new Error("现有工作区与设置不同，请保留原身份并重新核对");
    this.settings.workspace = marker || this.settings.workspace || crypto.randomUUID();
    await this.save(); // preserve setup identity if the app exits after cloud initialization
    const { api, transport } = await this.cloud(false); await api.initializeDirectories(); await transport.initializeEmpty(); await this.marker(); await this.save(); this.status = "配对完成，可先手动同步测试笔记"; this.render();
  }
  async journalNote(day?: string) {
    const now = new Date(), date = day ?? `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) throw new Error("日期格式无效");
    const path = `${directories.journal}/${date}.md`, notes = new VaultNotes(this);
    if (!await notes.read(path)) await notes.create(path, encode(`---\nid: ${crypto.randomUUID()}\ntype: journal\ndate: ${date}\n---\n\n# ${date}\n\n`));
    await this.app.workspace.getLeaf(false).openFile(this.app.vault.getAbstractFileByPath(path) as TFile);
  }
  quickNote() {
    new InputModal(this, "快速记录", [{ label: "标题" }, { label: "正文", multiline: true }], async ([title, body]) => {
      if (!body.trim()) throw new Error("请填写正文");
      const path = `${directories.essay}/速记-${crypto.randomUUID()}.md`;
      await new VaultNotes(this).create(path, encode(`---\nid: ${crypto.randomUUID()}\ntype: essay\nchapter: essay\ntitle: ${JSON.stringify(title || "快速记录")}\n---\n\n${body}\n`));
      new Notice("本地已保存，待同步"); await this.app.workspace.getLeaf(false).openFile(this.app.vault.getAbstractFileByPath(path) as TFile);
    }).open();
  }
  capture() {
    new InputModal(this, "取回对话 / 粘贴正文", [{ label: "公开分享链接（可留空）" }, { label: "正文兜底（填写后不再抓取网页）", multiline: true }, { label: "轮次（默认全部，如 1,3-5）" }, { label: "仅回答：填写 yes 开启" }], async ([url, text, rounds, answers]) => {
      if (text.trim() && (rounds.trim() || answers.trim())) throw new Error("粘贴正文请先选好范围；不自动猜测角色与轮次");
      const result = text.trim() ? await captureText(text, url) : await capturePage(url, async target => {
        const r = await bounded(requestUrl({ url: target, throw: false }));
        if (r.status !== 200 || r.arrayBuffer.byteLength > 2000000) throw new Error("分享不可访问或过长，可粘贴正文"); return r.text;
      }, { rounds, answersOnly: answers.trim().toLowerCase() === "yes" });
      await this.preview(result);
    }).open();
  }
  async preview(result: Capture, saved?: { id: string; note: string }) {
    const identity = saved ?? { id: crypto.randomUUID(), note: crypto.randomUUID() };
    if (!saved) { const bytes = encode(JSON.stringify({ ...identity, capture: result, created: new Date().toISOString() })); await this.journal.create(`previews/${Date.now()}-${await hash(bytes)}.json`, bytes); }
    const modal = new Modal(this.app); modal.setTitle("预览确认后归档");
    modal.contentEl.createEl("p", { text: "原文保存不调用模型；确认后写入本机，随后由同步模块上传。" });
    const title = modal.contentEl.createEl("input", { value: result.title });
    let allowPartial = !result.partial;
    if (result.partial) new Setting(modal.contentEl).setName("附件未取回，确认仅保存已取得的文本").addToggle(t => t.setValue(false).onChange(v => allowPartial = v));
    const body = modal.contentEl.createEl("pre", { text: result.markdown }); body.style.whiteSpace = "pre-wrap";
    new Setting(modal.contentEl).addButton(button => button.setButtonText("确认归档").setCta().onClick(async () => {
      button.setDisabled(true);
      try {
        if (!allowPartial) throw new Error("请先确认文本范围；未取回附件不能标为完整归档");
        const notes = new VaultNotes(this), path = `${directories.inbox}/对话-${result.contentHash}.md`;
        const old = await notes.read(path);
        if (old && !new TextDecoder().decode(old).split("---", 3)[1]?.split("\n").includes("content_hash: " + result.contentHash)) throw new Error("归档目标已被其他内容占用，请保留原文件并核对路径");
        if (!old) await notes.create(path, encode(`---\nid: ${identity.note}\nshare_import_id: ${identity.id}\ntype: knowledge\nchapter: inbox\nsource: ${JSON.stringify(result.source)}\ncontent_hash: ${result.contentHash}\ncapture_method: ${result.method}\ncompleteness: ${result.partial ? "partial" : result.method === "paste" ? "user_provided" : "complete"}\n---\n\n# ${title.value.replace(/[\r\n]/g, " ")}\n\n${result.markdown}`));
        new Notice(old ? "已存在，打开既有归档" : "本地归档完成，待同步"); modal.close();
        await this.app.workspace.getLeaf(false).openFile(this.app.vault.getAbstractFileByPath(path) as TFile);
      } catch (error) { new Notice(message(error)); } finally { button.setDisabled(false); }
    })); modal.open();
  }
  async saveRequest(action: "share_preview" | "share_commit" | "cancel", fields: Record<string, unknown>) {
    const request = queueRequest(this.settings.workspace, this.settings.device, action, fields);
    await new VaultNotes(this).create(`_Torch/收件/${request.request_id}.md`, encode(queueEnvelope(request)));
    new Notice("请求已保存在本机，等待同步和权威电脑处理；尚未取回或归档"); this.render();
  }
  queueCapture() {
    new InputModal(this, "交给电脑处理（不会立即归档）", [{ label: "公开分享链接" }, { label: "轮次（如 1,3-5；默认全部）" }, { label: "仅回答：填写 yes" }, { label: "保留原因（可选）" }], async ([url, rounds, answers, note]) => {
      await this.saveRequest("share_preview", { source_url: url, rounds, answers_only: answers.toLowerCase().trim() === "yes", client_note: note });
    }).open();
  }
  async queueView(id: string) {
    const notes = new VaultNotes(this), raw = await notes.read(`_Torch/回执/${id}.md`);
    const modal = new Modal(this.app); modal.setTitle("电脑处理状态");
    if (!raw) {
      modal.contentEl.createEl("p", { text: "尚未收到回执。需要本机上传、权威电脑上线并运行消费者，再同步回本机。" });
      new Setting(modal.contentEl).addButton(b => b.setButtonText("提交取消请求").onClick(async () => { await this.saveRequest("cancel", { target: id }); modal.close(); }));
    } else {
      const result = queueDecode(new TextDecoder().decode(raw));
      if (result.request_id !== id || result.workspace_id !== this.settings.workspace) throw new Error("回执身份不匹配");
      modal.contentEl.createEl("p", { text: String(result.status) });
      if (result.status === "preview_ready") {
        modal.contentEl.createEl("pre", { text: String(result.markdown) }).style.whiteSpace = "pre-wrap";
        let partial = result.completeness !== "partial";
        if (!partial) new Setting(modal.contentEl).setName("确认只保存已取得的文本，附件未取回").addToggle(t => t.onChange(v => partial = v));
        const title = modal.contentEl.createEl("input", { value: String(result.title) });
        new Setting(modal.contentEl).setName("归档到知识库收件箱").addButton(b => b.setButtonText("确认此预览并交给电脑归档").onClick(async () => {
          if (!partial) { new Notice("请先核对并确认未取回的附件"); return; }
          b.setDisabled(true);
          try { await this.saveRequest("share_commit", { preview_request: id, content_hash: result.content_hash, title: title.value, chapter: "inbox", allow_partial: partial }); modal.close(); }
          catch (e) { new Notice(message(e)); b.setDisabled(false); }
        }));
      } else if (result.status === "archived" && typeof result.path === "string") {
        new Setting(modal.contentEl).addButton(b => b.setButtonText("打开已归档笔记").onClick(async () => {
          const file = this.app.vault.getAbstractFileByPath(String(result.path));
          if (file instanceof TFile) { await this.app.workspace.getLeaf(false).openFile(file); modal.close(); }
          else new Notice("已收到归档回执，正文尚待同步，请稍后重试");
        }));
      } else if (result.error) modal.contentEl.createEl("p", { text: String(result.error) });
    }
    modal.open();
  }
  contextExport() {
    new InputModal(this, "选定笔记导出资料包", [{ label: "每行一条完整相对路径（默认不选任何笔记）", multiline: true }], async ([paths]) => {
      const notes = new VaultNotes(this), selected = paths.split("\n").map(s => s.trim()).filter(Boolean);
      const pack = await contextPack(selected, p => notes.read(p));
      const modal = new Modal(this.app); modal.setTitle("资料包预览：不会自动上传 ChatGPT");
      modal.contentEl.createEl("p", { text: "未检出密钥不代表已脱敏。仅含手机已取得的选定正文，附件未包含。新增笔记需要重新选入。" });
      for (const file of pack.files) { const details = modal.contentEl.createEl("details"); details.createEl("summary", { text: file.name }); details.createEl("pre", { text: file.markdown }); }
      new Setting(modal.contentEl).addButton(b => b.setButtonText("确认导出到本机 _Torch/导出").onClick(async () => {
        b.setDisabled(true);
        try {
          if ((await changedSources(pack, p => notes.read(p))).length) throw new Error("源笔记已变化，请重新预览");
          const root = `_Torch/导出/${crypto.randomUUID()}`;
          await this.app.vault.createFolder(root);
          for (const file of pack.files) await this.app.vault.create(root + "/" + file.name, file.markdown);
          const bytes = encode(JSON.stringify(pack)); await this.journal.create(`packs/${Date.now()}-${await hash(bytes)}.json`, bytes);
          new Notice("已导出本机文件，请自行上传或替换 ChatGPT 项目资料；导出目录不参与同步"); modal.close(); this.render();
        } catch (e) { new Notice(message(e)); b.setDisabled(false); }
      })); modal.open();
    }).open();
  }
  async recoveryExport() {
    const { transport, binding } = await this.cloud(), result = await readableExport(transport, binding);
    const root = `_Torch/恢复导出/${crypto.randomUUID()}`, vault = this.app.vault;
    await vault.createFolder(root);
    for (const file of result.files) {
      const path = root + "/" + file.path, parent = path.slice(0, path.lastIndexOf("/"));
      if (!vault.getAbstractFileByPath(parent)) await vault.createFolder(parent);
      if (file.path.endsWith(".md")) await vault.create(path, new TextDecoder("utf-8", { fatal: true }).decode(file.body));
      else await vault.createBinary(path, file.body.slice().buffer);
    }
    await vault.create(root + "/恢复清单.json", JSON.stringify(result.manifest, null, 2));
    new Notice("已导出普通文件副本和冲突清单，未覆盖当前笔记；目录：" + root);
  }
  async review(issue: Issue) {
    const { transport, binding } = await this.cloud(), view = await inspect(transport, binding, path => path === issue.path);
    const heads = view.heads.get(issue.file) ?? [];
    if (!heads.length) throw new Error("没有可处理的云端版本，请先同步");
    const modal = new Modal(this.app); modal.setTitle("保留原文并处理冲突");
    if (!issue.path.endsWith(".md")) {
      modal.contentEl.createEl("p", { text: "附件的现有文件保留。可把各版本保存到本机恢复区核对，再在 Obsidian 中明确替换并更新链接。附件不以文字编辑器合并。" });
      for (const head of heads.filter(h => h.revision.content)) new Setting(modal.contentEl).setName(`版本 ${head.id.slice(0, 10)}`).addButton(b => b.setButtonText("保存独立版本副本").onClick(async () => {
        try {
          const root = "_Torch/附件恢复"; if (!this.app.vault.getAbstractFileByPath(root)) await this.app.vault.createFolder(root);
          const path = `${root}/${head.id}.${issue.path.split(".").at(-1)}`;
          if (!this.app.vault.getAbstractFileByPath(path)) await this.app.vault.createBinary(path, (await transport.getObject(head.revision.content!)).slice().buffer);
          new Notice("副本已保存，本机原附件未覆盖");
        } catch (e) { new Notice(message(e)); }
      }));
      modal.open(); return;
    }
    modal.contentEl.createEl("p", { text: issue.path + "；原始版本继续留在历史中。删除和改名暂需回原生笔记核对。" });
    const target = modal.contentEl.createEl("input", { value: issue.path }); target.style.width = "100%";
    modal.contentEl.createEl("p", { text: "保存路径：重名独立笔记请改为新文件名，避免覆盖另一篇笔记。" });
    const local = await new VaultNotes(this).read(issue.path);
    if (issue.status === "local_missing_requires_review") {
      new Setting(modal.contentEl).setName("本机文件缺失不等于远端应删除").addButton(button => button.setButtonText("确认同步删除请求").onClick(async () => {
        try { await this.resolve(issue.file, issue.path, null, heads.map(h => h.id)); modal.close(); await this.sync(); } catch (e) { new Notice(message(e)); }
      }));
    }
    if (heads.every(h => h.revision.kind === "delete") && local) {
      new Setting(modal.contentEl).setName("远端已确认删除，本机原文可移到恢复区保留").addButton(button => button.setButtonText("确认移入本机恢复区").onClick(async () => {
        try {
          const file = this.app.vault.getAbstractFileByPath(issue.path);
          if (!(file instanceof TFile)) throw new Error("原文件已变化，请刷新");
          const recovery = "_Torch/本机恢复";
          if (!this.app.vault.getAbstractFileByPath(recovery)) await this.app.vault.createFolder(recovery);
          await this.app.vault.rename(file, `${recovery}/${crypto.randomUUID()}.md`);
          new Notice("原文保留在 _Torch/本机恢复，可在 Obsidian 中打开或移回"); modal.close(); await this.sync();
        } catch (e) { new Notice(message(e)); }
      }));
    }
    const editor = modal.contentEl.createEl("textarea"); editor.value = local ? new TextDecoder().decode(local) : ""; editor.style.minHeight = "250px"; editor.style.width = "100%";
    for (const head of heads) {
      const text = head.revision.content ? new TextDecoder().decode(await transport.getObject(head.revision.content)) : "（此版本请求删除）";
      const details = modal.contentEl.createEl("details"); details.createEl("summary", { text: `版本 ${head.id.slice(0, 10)} · ${head.revision.device}` });
      details.createEl("pre", { text });
      if (head.revision.content) details.createEl("button", { text: "选入编辑区" }).onclick = () => { editor.value = text; };
    }
    new Setting(modal.contentEl).addButton(button => button.setButtonText("确认此正文为合并版本").setCta().onClick(async () => {
      button.setDisabled(true);
      try { await this.resolve(issue.file, target.value, encode(editor.value), heads.map(h => h.id)); await this.sync(); modal.close(); }
      catch (error) { new Notice(message(error)); } finally { button.setDisabled(false); }
    })); modal.open();
  }
}

class Home extends ItemView {
  plugin: TorchCompanion;
  constructor(leaf: WorkspaceLeaf, plugin: TorchCompanion) { super(leaf); this.plugin = plugin; }
  getViewType() { return HOME; } getDisplayText() { return "Torch"; } getIcon() { return "flame"; }
  async onOpen() { this.render(); }
  render() {
    const host = this.contentEl; host.empty(); host.createEl("h2", { text: "Torch" }); host.createEl("p", { text: this.plugin.status });
    const actions: [string, () => unknown][] = [["写今日日志", () => this.plugin.journalNote()], ["快速记录", () => this.plugin.quickNote()], ["取回对话", () => this.plugin.capture()], ["交给电脑处理", () => this.plugin.queueCapture()], ["导出 ChatGPT 资料包", () => this.plugin.contextExport()], ["立即同步", () => this.plugin.sync()]];
    for (const [label, action] of actions) host.createEl("button", { text: label }).onclick = () => { Promise.resolve(action()).catch(e => new Notice(message(e))); };
    host.createEl("h3", { text: "最近笔记" });
    for (const file of this.app.vault.getMarkdownFiles().filter(f => /^(人生路\/|自我档案\/|生活运营\/|知识库\/)/.test(f.path)).sort((a, b) => b.stat.mtime - a.stat.mtime).slice(0, 8)) host.createEl("p").createEl("a", { text: file.basename }).onclick = () => { void this.app.workspace.getLeaf(false).openFile(file); };
    if (this.plugin.issues.length) host.createEl("h3", { text: "需处理" });
    for (const issue of this.plugin.issues) host.createEl("button", { text: `${issue.path} · ${issue.status}` }).onclick = () => { void this.plugin.review(issue).catch(e => new Notice(message(e))); };
    const pending = host.createDiv();
    void this.pending(pending).catch(e => { pending.createEl("p", { text: message(e) }); });
  }
  async pending(host: HTMLElement) {
    const requests = this.app.vault.getMarkdownFiles().filter(f => /^_Torch\/收件\/[a-f0-9-]{36}\.md$/.test(f.path)).sort((a, b) => b.stat.mtime - a.stat.mtime).slice(0, 12);
    if (requests.length) host.createEl("h3", { text: "电脑处理队列（含历史）" });
    for (const file of requests) host.createEl("button", { text: file.basename.slice(0, 10) + " · 查看回执" }).onclick = () => { void this.plugin.queueView(file.basename).catch(e => new Notice(message(e))); };
    const previews = (await this.plugin.journal.list("previews/")).sort().slice(-5).reverse();
    if (previews.length) host.createEl("h3", { text: "最近的本机预览（可恢复）" });
    for (const key of previews) {
      const raw = await this.plugin.journal.read(key); if (!raw) continue;
      const saved = JSON.parse(new TextDecoder().decode(raw));
      host.createEl("button", { text: saved.capture.title }).onclick = () => { void this.plugin.preview(saved.capture, saved).catch(e => new Notice(message(e))); };
    }
    const keys = (await this.plugin.journal.list("packs/")).sort().slice(-1);
    if (keys.length) {
      const packs = await Promise.all(keys.map(async key => JSON.parse(new TextDecoder().decode((await this.plugin.journal.read(key))!)) as Pack));
      const last = packs.sort((a, b) => a.exported_at.localeCompare(b.exported_at)).at(-1)!;
      const notes = new VaultNotes(this.plugin), changed = await changedSources(last, path => notes.read(path));
      host.createEl("p", { text: `上次资料包：${last.exported_at}；${changed.length ? `${changed.length} 篇已变化，需更新` : "上次选源未变化"}。是否已上传 ChatGPT 未知。` });
    }
  }
}

class SettingsTab extends PluginSettingTab {
  plugin: TorchCompanion;
  constructor(plugin: TorchCompanion) { super(plugin.app, plugin); this.plugin = plugin; }
  display() {
    const host = this.containerEl, p = this.plugin; host.empty(); host.createEl("h2", { text: "Torch Companion 配对与同步" });
    host.createEl("p", { text: "先在独立测试库验收。不要让百度桌面同步或其他同步引擎同时管理本库。配置与授权保存在本机插件目录，不同步 .obsidian。" });
    host.createEl("p", { text: "默认范围：四库 Markdown；笔记引用的小图片/PDF（单个 1 MiB、总计 50 MiB）；受限电脑收件/回执。传输内容与提交 AES-GCM 加密，网盘仍可见哈希、大小和活动。首次上传前请核对下方排除路径并保存恢复材料。" });
    for (const [key, label, secret] of [["appKey", "百度 AppKey", false], ["appSecret", "百度 AppSecret", true], ["root", "独立云端目录（/apps/应用名/…）", false], ["password", "同步密码（至少 12 位，保存恢复材料）", true]] as const) {
      new Setting(host).setName(label).addText(input => { input.setValue(p.settings[key]).onChange(async value => { p.settings[key] = value; await p.save(); }); if (secret) input.inputEl.type = "password"; });
    }
    new Setting(host).setName("记住同步密码").setDesc("开启后密码保存在本机插件配置；关闭则重启后需要解锁。不要把该配置交给其他同步工具。").addToggle(t => t.setValue(p.settings.remember).onChange(async v => { p.settings.remember = v; await p.save(); }));
    new Setting(host).setName("百度授权").addButton(b => b.setButtonText("打开授权页").onClick(() => window.open(`https://openapi.baidu.com/oauth/2.0/authorize?response_type=code&client_id=${encodeURIComponent(p.settings.appKey)}&redirect_uri=oob&scope=basic,netdisk`)))
      .addButton(b => b.setButtonText("填写授权码").onClick(() => new InputModal(p, "百度授权码", [{ label: "授权码", secret: true }], async ([code]) => { await p.oauth(new URLSearchParams({ grant_type: "authorization_code", code, client_id: p.settings.appKey, client_secret: p.settings.appSecret, redirect_uri: "oob" })); new Notice("百度授权已保存到本机"); }).open()));
    new Setting(host).setName("首台设备初始化").setDesc("仅初始化专用空云端目录；已使用的目录不能重建。").addButton(b => b.setButtonText("初始化并配对").onClick(async () => { try { await p.initialize(); new Notice("配对完成"); } catch (e) { new Notice(message(e)); } }));
    new Setting(host).setName("初始化中断后恢复本机配对").setDesc("只验证既有云端绑定，不重建云端目录。").addButton(b => b.setButtonText("验证并补全本机身份").onClick(async () => { try { await (await p.cloud(false)).transport.verifyBinding(); await p.marker(); await p.save(); new Notice("配对身份已验证"); } catch (e) { new Notice(message(e)); } }));
    new Setting(host).setName("导出到手机 / 新设备").addButton(b => b.setButtonText("生成加密配对码").onClick(() => new InputModal(p, "加密配对", [{ label: "临时配对密码（至少 12 位）", secret: true }], async ([password]) => {
      await (await p.cloud()).transport.verifyBinding();
      const { workspace, account, root, appKey, appSecret, access, refresh, expires, password: syncPassword } = p.settings;
      const value = { version: 1, workspace, account, root, appKey, appSecret, access, refresh, expires, password: syncPassword };
      const pair = await exportPair(value, password); const modal = new Modal(p.app); modal.setTitle("复制到新设备后关闭"); const input = modal.contentEl.createEl("textarea"); input.value = pair; input.style.width = "100%"; input.style.minHeight = "200px"; modal.open();
    }).open()));
    new Setting(host).setName("接收其他设备配对").addButton(b => b.setButtonText("导入配对码").onClick(() => new InputModal(p, "导入加密配对", [{ label: "配对码", multiline: true }, { label: "临时配对密码", secret: true }], async ([value, password]) => {
      if (await p.state.load()) throw new Error("当前库已有同步状态，请在新测试库配对");
      const incoming = await importPair(value.trim(), password) as Settings & { version: number };
      if (incoming.version !== 1 || typeof incoming.workspace !== "string" || !incoming.workspace || typeof incoming.password !== "string") throw new Error("配对配置无效");
      const before = p.settings;
      p.settings = { ...defaults(), ...incoming, device: before.device, autoSync: false, remember: false };
      try { await (await p.cloud(false)).transport.verifyBinding(); await p.marker(); await p.save(); } catch (e) { p.settings = before; throw e; }
      new Notice("配对已完成，请先手动同步"); this.display();
    }).open()));
    new Setting(host).setName("自动同步").addToggle(t => t.setValue(p.settings.autoSync).onChange(async v => { p.settings.autoSync = v; await p.save(); }));
    new Setting(host).setName("打开库时进入 Torch 首页").addToggle(t => t.setValue(p.settings.autoHome).onChange(async v => { p.settings.autoHome = v; await p.save(); }));
    new Setting(host).setName("本机排除路径前缀（每行一条）").setDesc("改变范围不会传播删除；再次纳入时会核对本地改动。").addTextArea(t => t.setValue(p.settings.excludes).onChange(async v => { p.settings.excludes = v; await p.save(); }));
  }
}
