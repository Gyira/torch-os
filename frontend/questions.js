// 主动提问（2026-10-27）：工作台上一天一个问题，答了就写进自我档案。
// 三个出口：记下来（写库）/ 跳过（7 天后再问）/ 别再问这个（永久）。
// 答完当天不自动出下一题；想继续就点「再问一个」（force=1，绕过一天一个的上限）。
import { api, jsonBody, toast } from "./core.js";
import { questionCardMarkup, questionDoneMarkup } from "./questions-markup.js";

export async function loadQuestionCard({ force = false } = {}) {
  const host = document.querySelector("[data-question-slot]");
  if (!host) return null;
  try {
    const payload = await api(`/api/questions/next${force ? "?force=1" : ""}`);
    const html = payload?.question
      ? questionCardMarkup(payload.question)
      : questionDoneMarkup({ exhausted: Boolean(payload?.exhausted) });
    host.innerHTML = html;
    host.hidden = !html;
    return payload;
  } catch {
    host.hidden = true;
    return null;
  }
}

export function initQuestions() {
  document.addEventListener("click", async (event) => {
    const button = event.target.closest?.("[data-question-action]");
    if (!button) return;
    const action = button.dataset.questionAction;

    if (action === "another") {
      button.disabled = true;
      await loadQuestionCard({ force: true });
      return;
    }

    const card = button.closest?.("[data-question-id]");
    if (!card) return;
    const id = card.dataset.questionId;

    if (action === "skip" || action === "decline") {
      button.disabled = true;
      try {
        await api("/api/questions/skip", jsonBody("POST", { id, forever: action === "decline" }));
        toast(action === "decline" ? "这个问题以后不问了。" : "换下一个。", "info");
        await loadQuestionCard();
      } catch (error) {
        toast(error.message, "error");
        button.disabled = false;
      }
      return;
    }

    const input = card.querySelector("[data-question-input]");
    const text = String(input?.value || "").trim();
    if (!text) {
      toast("写一句再记下来；不想答就点跳过。", "warning");
      input?.focus?.();
      return;
    }
    button.disabled = true;
    try {
      const result = await api("/api/questions/answer", jsonBody("POST", { id, text }));
      const where = result?.path ? `：${result.path}` : "";
      toast(`已记进自我档案${where}`, "success");
      await loadQuestionCard();
    } catch (error) {
      toast(error.message, "error");
      button.disabled = false;
    }
  });
}
