// 横幅编辑器：✎ 按钮或双击横幅打开；改座右铭 / 图片列表 / 遮罩 / 焦点 / 模式 / 字体。
// 图片列表只记路径；选本机文件时先上传到 <个人数据库>/.torch-os/横幅背景/，再写进 身份（identity）.md。
import { $, api, escapeHtml, jsonBody, setStatus, toast } from "./core.js";
import {
  normalizeBanner, imageSrc, backdropVars, safeFont, firstFontName, BANNER_FONT_GROUPS,
  parseMottos, mottoText,
} from "./banner.js";

const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
const IMAGE_SUFFIX = /\.(?:png|jpe?g|webp|gif)$/i;
const IMAGE_TYPE = /^image\/(?:png|jpeg|webp|gif)$/i;

let source = null; // 最近一次 payload 里的 { revision, banner }
// 编辑器内的图片列表：value 是要写进 front-matter 的路径/链接，preview 只用于列表与预览。
// 新选的本机图片用 data URL 立即预览（CSP 允许 data:），保存后统一从 /api/identity/hero?i=N 读。
let heroItems = [];
let uploading = false;
// 中间主位已从界面移除（2026-10-31 用户要求热力图铺满）；保存时保留旧值，
// 避免一次普通保存把历史设置冲掉。
let centerStatValue = "streak";

export function setBannerSource(payload) {
  source = { revision: String(payload?.revision || ""), banner: normalizeBanner(payload) };
}

/** 字体下拉：候选表来自 banner.js（照 Apex 的字体栈清单）；
 *  不在候选里的历史值（手写在 身份（identity）.md 里的）保留成一个额外选项，不悄悄丢掉。 */
function buildFontOptions(select, current) {
  select.replaceChildren();
  const known = new Set([""]);
  const defaultOption = document.createElement("option");
  defaultOption.value = "";
  defaultOption.textContent = "默认（座右铭衬线、统计用界面字体）";
  select.append(defaultOption);
  for (const group of BANNER_FONT_GROUPS) {
    const box = document.createElement("optgroup");
    box.label = group.label;
    for (const font of group.fonts) {
      known.add(font.value);
      const option = document.createElement("option");
      option.value = font.value;
      option.textContent = font.label;
      box.append(option);
    }
    select.append(box);
  }
  if (current && !known.has(current)) {
    const option = document.createElement("option");
    option.value = current;
    option.textContent = `${firstFontName(current)}（自定义）`;
    select.append(option);
  }
  select.value = current || "";
}

function currentFocal() {
  const x = Math.round(Number($("#banner-focal-x").value) || 0);
  const y = Math.round(Number($("#banner-focal-y").value) || 0);
  return `${x}% ${y}%`;
}

function accentValue() {
  const value = String($("#banner-accent").value || "").toLowerCase();
  return /^#[0-9a-f]{6}$/.test(value) ? value : "#bff038";
}

function renderHeroList() {
  const list = $("#banner-hero-list");
  if (!list) return;
  if (!heroItems.length) {
    list.innerHTML = '<p class="field-help">还没有横幅图片；选本机图片或添加 https 链接，多张每半小时轮换。</p>';
    return;
  }
  list.innerHTML = heroItems.map((item, index) => `
    <div class="banner-hero-item">
      <img class="banner-hero-thumb" src="${escapeHtml(item.preview)}" alt="" loading="lazy" />
      <span class="banner-hero-name" title="${escapeHtml(item.value)}">${escapeHtml(item.value)}</span>
      <button type="button" class="small-button banner-hero-remove" data-banner-hero-remove="${index}" aria-label="移除第 ${index + 1} 张横幅图片">移除</button>
    </div>`).join("");
}

function addHero(value, preview = "") {
  if (heroItems.length >= 12) {
    setStatus("#banner-feedback", "最多 12 张横幅图片。", "error");
    return false;
  }
  heroItems.push({ value, preview: preview || value });
  renderHeroList();
  updatePreview();
  return true;
}

function removeHero(index) {
  if (!Number.isInteger(index) || index < 0 || index >= heroItems.length) return;
  heroItems.splice(index, 1);
  renderHeroList();
  updatePreview();
}

function updatePreview() {
  const preview = $("#banner-preview");
  const first = heroItems[0]?.preview || "";
  preview.classList.toggle("banner-empty", heroItems.length === 0);
  preview.style.backgroundImage = first ? `url("${first.replace(/"/g, "%22")}")` : "";
  preview.style.backgroundPosition = currentFocal();
  preview.style.setProperty("--banner-accent", accentValue());
  // 字体：预览里直接用第一句座右铭当样本，选什么字体当场看得见。
  const font = safeFont($("#banner-font").value);
  if (font) preview.style.setProperty("--banner-font", font);
  else preview.style.removeProperty("--banner-font");
  const label = preview.querySelector(".banner-preview-label");
  if (label) label.textContent = parseMottos($("#banner-mottos").value)[0] || "预览";
  const backdrop = backdropVars(Number($("#banner-blur").value), Number($("#banner-darkness").value));
  preview.style.setProperty("--banner-blur", backdrop.blur);
  preview.style.setProperty("--banner-bright", backdrop.bright);
  preview.style.setProperty("--banner-scrim-alpha", backdrop.scrim);
  const overlay = preview.querySelector(".banner-overlay");
  if (overlay) {
    overlay.style.opacity = $("#banner-overlay").value;
    overlay.style.backdropFilter = `blur(${backdrop.blur}) brightness(${backdrop.bright})`;
  }
}

function fillForm(banner) {
  $("#banner-mottos").value = mottoText(banner.mottos);
  heroItems = banner.heroes.map((value, index) => ({ value, preview: imageSrc(value, index) }));
  renderHeroList();
  $("#banner-overlay").value = String(banner.overlay);
  const numbers = (banner.focal || "50% 50%").match(/\d+(?:\.\d+)?/g) || ["50", "50"];
  $("#banner-focal-x").value = String(Math.round(Number(numbers[0]) || 50));
  $("#banner-focal-y").value = String(Math.round(Number(numbers[1]) || 50));
  $("#banner-accent").value = banner.accent || "#bff038";
  $("#banner-left-stat").value = banner.leftStat;
  centerStatValue = banner.centerStat;
  $("#banner-blur").value = String(banner.blur);
  $("#banner-darkness").value = String(banner.darkness);
  $("#banner-details").checked = banner.details;
  buildFontOptions($("#banner-font"), banner.font);
  document.querySelectorAll('input[name="banner-mode"]').forEach((node) => {
    node.checked = node.value === banner.mode;
  });
  setStatus("#banner-feedback", "");
  updatePreview();
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error("读取图片失败"));
    reader.readAsDataURL(file);
  });
}

async function uploadHeroFiles(files) {
  if (!files.length || uploading) return;
  const chosen = Array.from(files);
  const invalid = chosen.find((file) => !IMAGE_TYPE.test(file.type || "") && !IMAGE_SUFFIX.test(file.name || ""));
  if (invalid) {
    setStatus("#banner-feedback", `「${invalid.name || "文件"}」不是 PNG / JPG / WebP / GIF 图片。`, "error");
    return;
  }
  const tooLarge = chosen.find((file) => Number(file.size) > MAX_UPLOAD_BYTES);
  if (tooLarge) {
    setStatus("#banner-feedback", `「${tooLarge.name || "图片"}」超过 10MB 上限。`, "error");
    return;
  }
  if (heroItems.length + chosen.length > 12) {
    setStatus("#banner-feedback", "最多 12 张横幅图片。", "error");
    return;
  }
  uploading = true;
  const save = $("#banner-save");
  if (save) save.disabled = true;
  setStatus("#banner-feedback", `正在把 ${chosen.length} 张图片复制到 .torch-os/横幅背景…`);
  let added = 0;
  try {
    for (const file of chosen) {
      const preview = await readFileAsDataUrl(file);
      const form = new FormData();
      form.append("file", file);
      const uploaded = await api("/api/identity/banner/image",
        { method: "POST", body: form, timeoutMs: 120000 });
      if (!uploaded?.path) throw new Error("上传接口没有返回图片路径");
      if (addHero(uploaded.path, preview)) added += 1;
    }
    setStatus("#banner-feedback", `已添加 ${added} 张图片；保存横幅后生效。`, "success");
  } catch (error) {
    setStatus("#banner-feedback",
      `${added ? `已添加 ${added} 张；` : ""}上传失败：${error.message}`, "error");
    renderHeroList();
    updatePreview();
  } finally {
    uploading = false;
    if (save) save.disabled = false;
  }
}

function addHeroUrl() {
  const input = $("#banner-hero-url");
  const value = String(input?.value || "").trim();
  if (!value) return;
  if (!/^https:\/\/\S+$/i.test(value) || value.length > 500) {
    setStatus("#banner-feedback", "网络图片请填 https:// 开头的链接（最多 500 字）。", "error");
    return;
  }
  if (heroItems.some((item) => item.value === value)) {
    setStatus("#banner-feedback", "这张网络图片已经在列表里了。", "error");
    return;
  }
  addHero(value, value);
  input.value = "";
  setStatus("#banner-feedback", "已添加网络图片；保存横幅后生效。", "success");
}

export function initBannerEditor(onSaved) {
  const dialog = $("#banner-dialog");
  const open = async () => {
    if (dialog.open) return; // 双击与按钮同时触发时不重复打开
    // 打开时当场重取 身份（identity）.md 的当前版本：拿到准确 revision（防 409）与最新字段。
    try {
      const identity = await api("/api/identity");
      source = { revision: String(identity?.revision || ""), banner: normalizeBanner(identity) };
    } catch (error) {
      if (!source) return; // 连兜底都没有才放弃
    }
    fillForm(source.banner);
    dialog.showModal();
  };
  $("#workbench-banner-edit").addEventListener("dblclick", (event) => event.stopPropagation());
  $("#workbench-banner-edit").addEventListener("click", (event) => { event.stopPropagation(); open(); });
  $("#workbench-banner").addEventListener("dblclick", open);
  $("#banner-cancel").addEventListener("click", () => dialog.close());
  ["#banner-overlay", "#banner-focal-x", "#banner-focal-y", "#banner-mottos",
   "#banner-accent", "#banner-blur", "#banner-darkness", "#banner-left-stat",
   "#banner-font"].forEach((selector) => $(selector).addEventListener("input", updatePreview));
  $("#banner-details").addEventListener("change", updatePreview);

  $("#banner-hero-list").addEventListener("click", (event) => {
    const button = event.target.closest?.("[data-banner-hero-remove]");
    if (!button) return;
    removeHero(Number(button.dataset.bannerHeroRemove));
  });
  $("#banner-hero-choose").addEventListener("click", () => $("#banner-hero-file").click());
  $("#banner-hero-file").addEventListener("change", async () => {
    const input = $("#banner-hero-file");
    const files = Array.from(input.files || []);
    input.value = "";
    await uploadHeroFiles(files);
  });
  $("#banner-hero-add-url").addEventListener("click", addHeroUrl);
  $("#banner-hero-url").addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    addHeroUrl();
  });

  $("#banner-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!source || uploading) return;
    const button = $("#banner-save");
    button.disabled = true;
    setStatus("#banner-feedback", "正在写回 身份（identity）.md…");
    try {
      await api("/api/identity/banner", jsonBody("PUT", {
        mottos: parseMottos($("#banner-mottos").value),
        heroes: heroItems.map((item) => item.value),
        hero_overlay: Number($("#banner-overlay").value) || 0,
        hero_focal: currentFocal(),
        banner_mode: document.querySelector('input[name="banner-mode"]:checked')?.value || "quote",
        banner_accent: accentValue() === "#bff038" ? "" : accentValue(),
        banner_blur: Number($("#banner-blur").value) || 0,
        banner_darkness: Number($("#banner-darkness").value) || 0,
        banner_left_stat: $("#banner-left-stat").value,
        banner_center_stat: centerStatValue,
        banner_details: Boolean($("#banner-details").checked),
        banner_font: safeFont($("#banner-font").value),
        revision: source.revision,
      }));
      dialog.close();
      toast("横幅已保存（写回 身份（identity）.md）。");
      await onSaved?.();
    } catch (error) {
      setStatus("#banner-feedback", `${error.message}${error.status === 409 ? " 请关闭后重新打开编辑器。" : ""}`, "error");
    } finally {
      button.disabled = false;
    }
  });
}
