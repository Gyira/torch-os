// 数字分身：姓名 / 座右铭 / 三观。数据写在 Vault 的 01-Identity/身份（identity）.md，
// 这里只是它的一个表单；冲突时要求先重新读取，不覆盖别处的修改。
import { $, api, jsonBody, setStatus, toast } from "./core.js";

let revision = "";
let loaded = false;

function fields() {
  return {
    name: $("#identity-name").value.trim(),
    motto: $("#identity-motto").value.trim(),
    worldview: $("#identity-worldview").value.trim(),
    lifeview: $("#identity-lifeview").value.trim(),
    values: $("#identity-values").value.trim(),
    notes: $("#identity-notes").value.trim(),
  };
}

function fill(payload) {
  revision = payload.revision || "";
  $("#identity-name").value = payload.name || "";
  $("#identity-motto").value = payload.motto || "";
  $("#identity-worldview").value = payload.worldview || "";
  $("#identity-lifeview").value = payload.lifeview || "";
  $("#identity-values").value = payload.values || "";
  $("#identity-notes").value = payload.notes || "";
  const disabled = payload.writable === false;
  $("#identity-fields").disabled = disabled;
  $("#identity-save").disabled = disabled;
  loaded = true;
}

export async function loadIdentity() {
  try {
    const payload = await api("/api/identity");
    fill(payload);
    setStatus("#identity-status", payload.exists ? "已读取本地档案。" : "还没有档案：填几项保存即可。", "success");
  } catch (error) {
    setStatus("#identity-status", `读取失败：${error.message}`, "error");
  }
}

async function save(event) {
  event.preventDefault();
  if (!loaded) return;
  const values = fields();
  if (!Object.values(values).some(Boolean)) {
    setStatus("#identity-feedback", "至少写一项再保存。", "error");
    return;
  }
  const button = $("#identity-save");
  button.disabled = true;
  try {
    const payload = await api("/api/identity", jsonBody("PUT", { ...values, revision }));
    fill(payload);
    toast("档案已保存到 Vault。");
    setStatus("#identity-feedback", "已保存；工作台的问候语与座右铭会立刻跟着变。", "success");
  } catch (error) {
    setStatus("#identity-feedback", error.message, "error");
    if (/重新读取/.test(error.message)) await loadIdentity();
  } finally {
    button.disabled = false;
  }
}

export function initIdentity() {
  $("#identity-form").addEventListener("submit", save);
  $("#identity-reload").addEventListener("click", () => loadIdentity());
}
