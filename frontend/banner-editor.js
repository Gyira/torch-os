import { $, api, escapeHtml, jsonBody, setStatus, toast } from "./core.js";
import {
  normalizeBanner, imageSrc, applyBanner, imageCrop, paintCrop, safeFont, firstFontName, BANNER_FONT_GROUPS,
  parseMottos, mottoText, DEFAULT_HEAT_COLOR,
} from "./banner.js";

const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
const IMAGE_SUFFIX = /\.(?:png|jpe?g|webp|gif)$/i;
const IMAGE_TYPE = /^image\/(?:png|jpeg|webp|gif)$/i;

let heroItems = [];
let selected = 0;
let historyPaths = [];
let historyError = "";
let draftRevision = "";
let previewStats = null;
let uploading = false;
let opening = false;
let session = 0;
// 中间主位已从界面移除（2026-10-31 用户要求热力图铺满）；保存时保留旧值，
// 避免一次普通保存把历史设置冲掉。
let centerStatValue = "streak";

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

function accentValue() {
  const value = String($("#banner-accent").value || "").toLowerCase();
  return /^#[0-9a-f]{6}$/.test(value) ? value : "#bff038";
}

/** 热力格颜色输入：更新五档预览与预设选中状态；返回合法的 #rrggbb。 */
function updateHeatColor(value) {
  const text = String(value || "").trim().toLowerCase();
  const color = /^#[0-9a-f]{6}$/.test(text) ? text : DEFAULT_HEAT_COLOR;
  const preview = $("#banner-heat-preview");
  if (preview) preview.style.setProperty("--heat-color", color);
  document.querySelectorAll("[data-heat-preset]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.heatPreset.toLowerCase() === color);
  });
  return color;
}

function tile(path, src, index, history = false) {
  const name = escapeHtml(path.split("/").pop() || path);
  const inUse = history && heroItems.some(item => item.value === path);
  return `<figure><button type="button" class="banner-tile" data-index="${index}" title="${name}"
    ${history ? (inUse ? "disabled" : "") : `aria-pressed="${index === selected}"`}>
    <img src="${escapeHtml(src)}" alt="" loading="lazy"><span>${inUse ? "已在轮换 · " : ""}${name}</span></button>
    ${history ? "" : `<button class="banner-tile-remove" type="button" data-remove="${index}" aria-label="移除 ${name}">×</button>`}</figure>`;
}

function renderHeroList() {
  $("#banner-hero-list").innerHTML = heroItems.map((item, i) => tile(item.value, item.preview, i)).join("")
    || '<p class="field-help">导入图片，或从历史图片中选择。</p>';
  $("#banner-history").innerHTML = historyPaths.map((path, i) => tile(path, imageSrc(path), i, true)).join("")
    || `<p class="field-help">${escapeHtml(historyError || "还没有导入过图片。")}</p>`;
}

function addHero(value, preview) {
  if (heroItems.length >= 12) {
    setStatus("#banner-feedback", "最多 12 张横幅图片。", "error");
    return false;
  }
  heroItems.push({ value, preview, crop: { x: 50, y: 50, zoom: 1 } });
  selected = heroItems.length - 1;
  renderHeroList();
  updatePreview();
  return true;
}

function draftPayload() {
  return {
    heroes: heroItems.map(item => item.value),
    hero_crops: Object.fromEntries(heroItems.map(item => [item.value, item.crop])),
    hero_focal: heroItems.length ? `${heroItems[0].crop.x}% ${heroItems[0].crop.y}%` : "",
    mottos: parseMottos($("#banner-mottos").value),
    hero_overlay: Number($("#banner-overlay").value),
    banner_mode: document.querySelector('input[name="banner-mode"]:checked')?.value || "quote",
    banner_accent: accentValue() === "#bff038" ? "" : accentValue(),
    banner_blur: Number($("#banner-blur").value),
    banner_darkness: Number($("#banner-darkness").value),
    banner_left_stat: $("#banner-left-stat").value,
    banner_center_stat: centerStatValue,
    banner_heat_color: $("#banner-heat-color").value,
    banner_details: $("#banner-details").checked,
    banner_font: safeFont($("#banner-font").value),
    revision: draftRevision,
  };
}

function updateCrop() {
  const crop = heroItems[selected]?.crop;
  $("#banner-zoom").textContent = `${Math.round((crop?.zoom || 1) * 100)}%`;
  $("#banner-minus").disabled = !crop || crop.zoom <= 1;
  $("#banner-plus").disabled = !crop || crop.zoom >= 5;
  $("#banner-reset").disabled = !crop;
  $("#banner-resize").hidden = !crop;
  if (crop) paintCrop($("#banner-preview .banner-bg"), crop);
}

function updatePreview() {
  if (!$("#banner-dialog").open) return;
  const selectedItem = heroItems[selected];
  const preview = $("#banner-preview");
  const stage = $("#banner-stage");
  const width = $("#workbench-banner").offsetWidth || stage.clientWidth;
  preview.style.width = `${width}px`;
  preview.style.zoom = String(stage.clientWidth / width);
  applyBanner(preview, draftPayload(), previewStats, selected, selectedItem?.preview);
  updateCrop();
  const img = preview.querySelector(".banner-bg");
  const status = $("#banner-image-status");
  status.hidden = !!selectedItem && (!img.complete || img.naturalWidth > 0);
  status.textContent = selectedItem ? "无法读取图片，请重新导入" : "尚未选择图片";
}

function bindCropControls() {
  const stage = $("#banner-stage");
  const img = $("#banner-preview .banner-bg");
  let drag;
  const limit = (value, min, max) => Math.round(Math.min(max, Math.max(min, value)) * 100) / 100;
  function zoom(value) {
    const crop = heroItems[selected]?.crop;
    if (crop) crop.zoom = limit(value, 1, 5);
    updateCrop();
  }
  stage.addEventListener("pointerdown", event => {
    const crop = heroItems[selected]?.crop;
    if (!crop || !img.naturalWidth || event.button !== 0 || !event.isPrimary) return;
    const bounds = stage.getBoundingClientRect();
    const scale = Math.max(bounds.width / img.naturalWidth, bounds.height / img.naturalHeight) * crop.zoom;
    drag = { id: event.pointerId, x: event.clientX, y: event.clientY, crop: { ...crop },
      resize: event.target.id === "banner-resize", width: bounds.width, height: bounds.height,
      overflowX: Math.max(0, scale * img.naturalWidth - bounds.width),
      overflowY: Math.max(0, scale * img.naturalHeight - bounds.height) };
    stage.setPointerCapture(event.pointerId);
    stage.focus({ preventScroll: true });
  });
  stage.addEventListener("pointermove", event => {
    if (drag?.id !== event.pointerId) return;
    const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
    if (drag.resize) {
      zoom(drag.crop.zoom * (1 + 2 * (dx * drag.width + dy * drag.height) / (drag.width ** 2 + drag.height ** 2)));
      return;
    }
    const crop = heroItems[selected].crop;
    if (drag.overflowX > 0.5) crop.x = limit(drag.crop.x - dx * 100 / drag.overflowX, 0, 100);
    if (drag.overflowY > 0.5) crop.y = limit(drag.crop.y - dy * 100 / drag.overflowY, 0, 100);
    updateCrop();
  });
  stage.addEventListener("lostpointercapture", () => { drag = null; });
  stage.addEventListener("wheel", event => {
    if (!heroItems[selected] || !event.deltaY) return;
    event.preventDefault();
    zoom(heroItems[selected].crop.zoom * (event.deltaY < 0 ? 1.1 : 1 / 1.1));
  }, { passive: false });
  $("#banner-plus").onclick = () => zoom(heroItems[selected].crop.zoom + 0.25);
  $("#banner-minus").onclick = () => zoom(heroItems[selected].crop.zoom - 0.25);
  $("#banner-reset").onclick = () => {
    heroItems[selected].crop = { x: 50, y: 50, zoom: 1 };
    updateCrop();
  };
  stage.addEventListener("keydown", event => {
    const crop = heroItems[selected]?.crop;
    if (!crop) return;
    const delta = { ArrowLeft: [2, 0], ArrowRight: [-2, 0], ArrowUp: [0, 2], ArrowDown: [0, -2] }[event.key];
    if (delta) {
      crop.x = limit(crop.x + delta[0], 0, 100);
      crop.y = limit(crop.y + delta[1], 0, 100);
      updateCrop();
    } else if (["+", "=", "-"].includes(event.key)) zoom(crop.zoom + (event.key === "-" ? -0.25 : 0.25));
    else return;
    event.preventDefault();
  });
  img.addEventListener("load", () => { $("#banner-image-status").hidden = true; });
  img.addEventListener("error", () => { $("#banner-image-status").hidden = false; });
  new ResizeObserver(updatePreview).observe($("#banner-stage").parentElement);
}

function fillForm(banner) {
  $("#banner-mottos").value = mottoText(banner.mottos);
  selected = 0;
  heroItems = banner.heroes.map((value, index) => ({ value, preview: imageSrc(value, index), crop: imageCrop(banner, value) }));
  renderHeroList();
  $("#banner-overlay").value = String(banner.overlay);
  $("#banner-accent").value = banner.accent || "#bff038";
  $("#banner-left-stat").value = banner.leftStat;
  centerStatValue = banner.centerStat;
  $("#banner-heat-color").value = banner.heatColor;
  updateHeatColor(banner.heatColor);
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
  setStatus("#banner-feedback", `正在把 ${chosen.length} 张图片复制到个人数据库…`);
  let added = 0;
  try {
    for (const file of chosen) {
      const form = new FormData();
      form.append("file", file);
      const uploaded = await api("/api/identity/banner/image",
        { method: "POST", body: form, timeoutMs: 120000 });
      if (!uploaded?.path) throw new Error("上传接口没有返回图片路径");
      historyPaths.unshift(uploaded.path);
      if (addHero(uploaded.path, imageSrc(uploaded.path))) added += 1;
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
  if (!addHero(value, value)) return;
  input.value = "";
  setStatus("#banner-feedback", "已添加网络图片；保存横幅后生效。", "success");
}

export function initBannerEditor(onSaved) {
  const dialog = $("#banner-dialog");
  const open = async () => {
    if (dialog.open || opening || uploading) return;
    opening = true;
    try {
      const identity = await api("/api/identity");
      draftRevision = identity.revision || "";
      historyPaths = [];
      historyError = "正在读取历史图片…";
      previewStats = null;
      fillForm(normalizeBanner(identity));
      dialog.showModal();
      updatePreview();
      const currentSession = ++session;
      api("/api/identity/banner/images").then(({ paths }) => {
        if (currentSession !== session) return;
        historyPaths = [...new Set([...historyPaths, ...paths])];
        historyError = "";
        renderHeroList();
      }).catch(error => {
        if (currentSession !== session) return;
        historyError = `历史图片读取失败：${error.message}`;
        renderHeroList();
      });
      api("/api/banner/stats").then(stats => {
        if (currentSession !== session) return;
        previewStats = stats;
        updatePreview();
      }).catch(() => {});
    } catch (error) {
      toast(`读取横幅失败：${error.message}`, "error");
    } finally {
      opening = false;
    }
  };
  dialog.addEventListener("close", () => { session += 1; });
  bindCropControls();
  $("#workbench-banner-edit").addEventListener("dblclick", (event) => event.stopPropagation());
  $("#workbench-banner-edit").addEventListener("click", (event) => { event.stopPropagation(); open(); });
  // Banner surface opens the data board; editing has its own explicit button.
  $("#banner-cancel").addEventListener("click", () => dialog.close());
  $("#banner-form").addEventListener("input", updatePreview);
  $("#banner-heat-color").addEventListener("input", (event) => updateHeatColor(event.target.value));
  $("#banner-heat-presets").addEventListener("click", (event) => {
    const button = event.target.closest?.("[data-heat-preset]");
    if (!button) return;
    $("#banner-heat-color").value = button.dataset.heatPreset;
    updateHeatColor(button.dataset.heatPreset);
    updatePreview();
  });
  $("#banner-heat-reset").addEventListener("click", () => {
    $("#banner-heat-color").value = DEFAULT_HEAT_COLOR;
    updateHeatColor(DEFAULT_HEAT_COLOR);
    updatePreview();
  });

  $("#banner-hero-list").addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.hasAttribute("data-remove")) {
      const index = Number(button.dataset.remove);
      heroItems.splice(index, 1);
      if (index < selected || selected >= heroItems.length) selected = Math.max(0, selected - 1);
    } else selected = Number(button.dataset.index);
    renderHeroList();
    updatePreview();
  });
  $("#banner-history").addEventListener("click", event => {
    const button = event.target.closest("[data-index]");
    if (!button) return;
    const path = historyPaths[Number(button.dataset.index)];
    addHero(path, imageSrc(path));
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
    if (uploading) return;
    const button = $("#banner-save");
    button.disabled = true;
    setStatus("#banner-feedback", "正在写回 身份（identity）.md…");
    try {
      await api("/api/identity/banner", jsonBody("PUT", draftPayload()));
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
