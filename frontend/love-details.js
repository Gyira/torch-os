import { escapeHtml } from "./core.js";

export function detailsMarkup(run) {
  const data = run.result?.structured || {};
  const items = Array.isArray(data.items) ? data.items : [];
  const sources = data.sources || {};
  const editable = run.status === "completed";
  const input = (key, value, index) => `<textarea data-detail-field="${key}" aria-label="第${index + 1}项 ${key}" rows="3" ${editable ? "" : "readonly"}>${escapeHtml(value || "")}</textarea>`;
  return `<section class="love-details" data-details-run="${Number(run.id)}" data-revision="${escapeHtml(run.revision || "")}">
    <h3>小细节记录 · ${items.length} 项${data.partial ? "（未完成）" : ""}</h3>
    <p class="field-help">同一件事合并记录，后续变化接着记。手动改过的条目会保留，之后更新不会改写。语音、图片和视频只整理已有文字。</p>
    ${editable ? `<div class="love-inline-actions"><button class="button primary" type="button" data-love-action="details-save">保存修改</button><a class="button secondary" href="/api/love/master/runs/${Number(run.id)}/details.xlsx" download>导出 Excel</a><span class="field-help">修改后先保存再导出。</span></div>` : ""}
    <div class="love-details-scroll"><table class="love-details-table"><thead><tr><th>最早引用日期</th><th>类别</th><th>说过的事</th><th>后续情况</th><th>接下来怎么做</th><th>聊天依据</th></tr></thead><tbody>
    ${items.map((item, index) => `<tr data-detail-topic="${escapeHtml(item.topic)}"><td>${escapeHtml(item.date || "")}${item.manual ? '<small>已手动修改</small>' : ""}</td><td>${input("category", item.category, index)}</td><td>${input("detail", item.detail, index)}</td><td>${input("followup", item.followup, index)}</td><td>${input("action", item.action, index)}</td><td><details><summary>${(item.evidence_ids || []).length} 条</summary>${(item.evidence_ids || []).map((id) => {
      const source = sources[id];
      return source ? `<p><small>${escapeHtml(source.day)} ${escapeHtml(source.who)} #${Number(id)}</small><br>${escapeHtml(source.content)}</p>` : `<p>#${Number(id)} 依据不可用</p>`;
    }).join("")}</details></td></tr>`).join("") || '<tr><td colspan="6">还没有提取到具体事项。</td></tr>'}
    </tbody></table></div>
    <h3>反思草稿</h3><textarea data-details-reflection aria-label="反思草稿" rows="8" ${editable ? "" : "readonly"}>${escapeHtml(data.reflection || "")}</textarea>
  </section>`;
}

export function detailsEditPayload(container) {
  return {
    revision: container.dataset.revision,
    items: [...container.querySelectorAll("[data-detail-topic]")].map((row) => ({
      topic: row.dataset.detailTopic,
      ...Object.fromEntries([...row.querySelectorAll("[data-detail-field]")].map((input) => [input.dataset.detailField, input.value])),
    })),
    reflection: container.querySelector("[data-details-reflection]").value,
  };
}
