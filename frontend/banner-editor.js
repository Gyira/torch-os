// 横幅编辑器：✎ 按钮或双击横幅打开；改座右铭 / 图片列表 / 遮罩 / 焦点 / 模式 / 字体。
// 保存走 PUT /api/identity/banner——只动 身份（identity）.md 的横幅字段，正文与其它字段原样保留。
import { $, api, jsonBody, setStatus, toast } from "./core.js";
import { normalizeBanner, imageSrc, backdropVars, safeFont, firstFontName, BANNER_FONT_GROUPS } from "./banner.js";

let source = null; // 最近一次 payload 里的 { revision, banner }

export function setBannerSource(payload) {
  source = { revision: String(payload?.revision || ""), banner: normalizeBanner(payload) };
}

function lines(value) {
  return String(value || "").split("\n").map((line) => line.trim()).filter(Boolean);
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

function updatePreview() {
  const preview = $("#banner-preview");
  const heroes = lines($("#banner-heroes").value);
  preview.classList.toggle("banner-empty", heroes.length === 0);
  preview.style.backgroundImage = heroes.length
    ? `url("${imageSrc(heroes[0], 0).replace(/"/g, "%22")}")` : "";
  preview.style.backgroundPosition = currentFocal();
  preview.style.setProperty("--banner-accent", accentValue());
  // 字体：预览里直接用第一句座右铭当样本，选什么字体当场看得见。
  const font = safeFont($("#banner-font").value);
  if (font) preview.style.setProperty("--banner-font", font);
  else preview.style.removeProperty("--banner-font");
  const label = preview.querySelector(".banner-preview-label");
  if (label) label.textContent = lines($("#banner-mottos").value)[0] || "预览";
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
  $("#banner-mottos").value = banner.mottos.join("\n");
  $("#banner-heroes").value = banner.heroes.join("\n");
  $("#banner-overlay").value = String(banner.overlay);
  const numbers = (banner.focal || "50% 50%").match(/\d+(?:\.\d+)?/g) || ["50", "50"];
  $("#banner-focal-x").value = String(Math.round(Number(numbers[0]) || 50));
  $("#banner-focal-y").value = String(Math.round(Number(numbers[1]) || 50));
  $("#banner-accent").value = banner.accent || "#bff038";
  $("#banner-left-stat").value = banner.leftStat;
  $("#banner-center-stat").value = banner.centerStat;
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
  ["#banner-overlay", "#banner-focal-x", "#banner-focal-y", "#banner-heroes", "#banner-mottos",
   "#banner-accent", "#banner-blur", "#banner-darkness", "#banner-left-stat",
   "#banner-center-stat", "#banner-font"].forEach((selector) => $(selector).addEventListener("input", updatePreview));
  $("#banner-details").addEventListener("change", updatePreview);

  $("#banner-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!source) return;
    const button = $("#banner-save");
    button.disabled = true;
    setStatus("#banner-feedback", "正在写回 身份（identity）.md…");
    try {
      await api("/api/identity/banner", jsonBody("PUT", {
        mottos: lines($("#banner-mottos").value),
        heroes: lines($("#banner-heroes").value),
        hero_overlay: Number($("#banner-overlay").value) || 0,
        hero_focal: currentFocal(),
        banner_mode: document.querySelector('input[name="banner-mode"]:checked')?.value || "quote",
        banner_accent: accentValue() === "#bff038" ? "" : accentValue(),
        banner_blur: Number($("#banner-blur").value) || 0,
        banner_darkness: Number($("#banner-darkness").value) || 0,
        banner_left_stat: $("#banner-left-stat").value,
        banner_center_stat: $("#banner-center-stat").value,
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
