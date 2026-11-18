import { api, toast } from "./core.js";

let initialized = false;
export async function loadModelSettings() {
  const form = document.getElementById("ai-model-form");
  if (!form) return;
  const field = name => form.elements.namedItem(name);
  const status = document.getElementById("ai-model-status");
  const save = form.querySelector("button[type=submit]");
  if (!initialized) {
    initialized = true;
    form.addEventListener("input", () => { form.dataset.dirty = "true"; });
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (save.disabled) return;
      const data = { provider: field("provider").value, model: field("model").value.trim(),
        reasoning_effort: field("reasoning_effort").value, max_output_tokens: Number(field("max_output_tokens").value),
        api_key: field("api_key").value.trim(), clear_key: field("clear_key").checked };
      save.disabled = true;
      form.querySelectorAll("input,select").forEach(node => { node.disabled = true; });
      status.textContent = "正在保存本机模型设置…";
      try {
        const saved = await api("/api/ai/models", { method: "PUT", body: JSON.stringify(data) });
        field("api_key").value = ""; field("clear_key").checked = false;
        form.dataset.dirty = "false";
        status.textContent = `${saved.label} · 已保存，下次请求生效。${saved.provider === "openai" && !saved.key_configured ? "尚未配置 OpenAI Key。" : "尚未发起付费调用。"}`;
        document.dispatchEvent(new CustomEvent("torch:ai-model-changed"));
        toast("模型设置已保存");
      } catch (error) { status.textContent = error.message; }
      finally { save.disabled = false; form.querySelectorAll("input,select").forEach(node => { node.disabled = false; }); }
    });
  }
  if (form.dataset.dirty === "true") return;
  save.disabled = true;
  try {
    const data = await api("/api/ai/models");
    save.disabled = !data.enabled;
    if (form.dataset.dirty === "true") return;
    for (const name of ["provider", "model", "reasoning_effort", "max_output_tokens"]) field(name).value = data[name];
    field("api_key").value = "";
    status.textContent = `${data.label} · OpenAI Key${data.key_configured ? "已配置" : "未配置"}${!data.enabled ? "；此测试环境不允许保存或调用模型" : ""}`;
    save.disabled = !data.enabled;
  } catch (error) { status.textContent = error.message; save.disabled = false; }
}
