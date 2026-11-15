export type Capture = { title: string; source: string; markdown: string; contentHash: string; method: string; partial?: boolean };
import { hash } from "./protocol.ts";
export function sourceUrl(value: string) {
  const url = new URL(value.trim());
  if (url.protocol !== "https:" || url.username || url.password || url.port || !["chatgpt.com", "chat.openai.com", "chat.deepseek.com"].includes(url.hostname) || !/^\/share\/[A-Za-z0-9_-]{4,128}\/?$/.test(url.pathname)) throw new Error("只接受 ChatGPT / DeepSeek 公开分享链接");
  if (url.hostname === "chat.openai.com") url.hostname = "chatgpt.com";
  url.pathname = url.pathname.replace(/\/$/, "");
  url.search = ""; url.hash = ""; return url.toString();
}
export async function captureText(text: string, source = "", method = "paste", messages?: [string, string][]): Promise<Capture> {
  if (!text.trim() || new TextEncoder().encode(text).length > 180000) throw new Error("正文为空或过长，请缩小范围");
  const normalizedSource = source ? sourceUrl(source) : "";
  const provider = normalizedSource ? new URL(normalizedSource).hostname === "chat.deepseek.com" ? "deepseek" : "chatgpt" : "paste";
  const contentHash = await hash(new TextEncoder().encode(JSON.stringify(["torch-capture-v1", provider, normalizedSource, method, messages ?? [["assistant", text.trim()]]])));
  const title = text.replace(/[#>*`]/g, "").trim().split("\n")[0].slice(0, 70) || "AI 对话";
  return { title, source: normalizedSource, contentHash, method,
    markdown: `> 来源：${normalizedSource || "用户粘贴"}\n> 获取：${new Date().toISOString()}\n> 方式：${method}；模型：unknown\n${method === "paste" ? "> 用户提供正文，未与网页核验完整性。\n" : ""}\n${text}\n` };
}
export function selectMessages(messages: [string, string][], spec = "", answersOnly = false): [string, string][] {
  const total = messages.filter(m => m[0] === "user").length;
  const picked = new Set<number>();
  if (spec.trim()) {
    for (const chunk of spec.trim().split(/[,，\s]+/)) {
      const match = /^(\d+)(?:[-–~](\d+))?$/.exec(chunk);
      if (!match) throw new Error("轮次格式应为 1,3-5");
      const from = Number(match[1]), to = Number(match[2] ?? match[1]);
      if (from < 1 || to < from || to > total) throw new Error(`轮次超出范围（共 ${total} 轮）`);
      for (let n = from; n <= to; n++) picked.add(n);
    }
  }
  let round = 0;
  const chosen = messages.filter(m => {
    if (m[0] === "user") round++;
    return (!picked.size || picked.has(round)) && (!answersOnly || m[0] === "assistant");
  });
  if (!chosen.length) throw new Error("所选范围没有可归档正文");
  return chosen;
}
export async function capturePage(url: string, get: (url: string) => Promise<string>, options: { rounds?: string; answersOnly?: boolean } = {}): Promise<Capture> {
  const source = sourceUrl(url), parsed = new URL(source);
  if (parsed.hostname === "chat.deepseek.com") {
    const response = JSON.parse(await get("https://chat.deepseek.com/api/v0/share/content?share_id=" + parsed.pathname.split("/")[2]));
    const messages = response?.data?.biz_data?.messages;
    if (![0, "0"].includes(response.code) || !Array.isArray(messages)) throw new Error("DeepSeek 分享不可用");
    const selected: [string, string][] = [];
    for (const message of messages as { role: string; fragments: { type: string; content: string }[] }[]) {
      const type = message.role === "USER" ? "REQUEST" : message.role === "ASSISTANT" ? "RESPONSE" : "";
      if (!type) continue;
      if (!Array.isArray(message.fragments)) throw new Error("DeepSeek 消息结构变化，请粘贴正文");
      const body = (message.fragments ?? []).filter(f => f.type === type && typeof f.content === "string").map(f => f.content).join("\n\n").replace(/\r\n?/g, "\n").split("\n").map((s: string) => s.trimEnd()).join("\n").replace(/\n{3,}/g, "\n\n").trim();
      if (body) selected.push([message.role === "USER" ? "user" : "assistant", body]);
    }
    const chosen = selectMessages(selected, options.rounds, options.answersOnly);
    return captureText(chosen.map(m => `## ${m[0] === "user" ? "我问" : "回答"}\n\n${m[1]}`).join("\n\n"), source, "public_page", chosen);
  }
  const html = await get(source);
  const script = /<script\b[^>]*\bid=["']__NEXT_DATA__["'][^>]*>([\s\S]*?)<\/script>/i.exec(html);
  let data;
  try { if (script) data = JSON.parse(script[1])?.props?.pageProps?.serverResponse?.data; } catch { /* Anonymous JSON fallback below. */ }
  if (!data?.mapping) data = JSON.parse(await get("https://chatgpt.com/backend-api/share/" + parsed.pathname.split("/")[2]));
  if (!data || typeof data.mapping !== "object" || typeof data.current_node !== "string") throw new Error("对话结构变化，请粘贴正文");
  let current = data.current_node;
  const visited = new Set(), selected: [string, string][] = [], partials = new Set<[string, string]>();
  while (current) {
    if (visited.has(current) || !data.mapping[current] || visited.size > 2000) throw new Error("对话链不完整");
    visited.add(current); const node = data.mapping[current], m = node.message;
    if (node.parent != null && typeof node.parent !== "string") throw new Error("对话父节点无效，请粘贴正文");
    if (m?.author?.role === "assistant" && ["thoughts", "reasoning", "reasoning_recap", "model_editable_context"].includes(m.content?.content_type)) { current = node.parent; continue; }
    if (m && ["user", "assistant"].includes(m.author?.role) && [undefined, null, "final"].includes(m.channel) && !m.metadata?.is_visually_hidden_from_conversation) {
      if (!["text", "multimodal_text"].includes(m.content?.content_type) || !Array.isArray(m.content.parts) || ![undefined, null, "finished_successfully"].includes(m.status)) throw new Error("包含未支持或未完成的消息，请粘贴需要的正文");
      const body = m.content.parts.map((p: unknown) => typeof p === "string" ? p : "[附件未取回]").join("\n\n").trim();
      if (body) { const pair: [string, string] = [m.author.role, body]; selected.push(pair); if (m.content.parts.some((p: unknown) => typeof p !== "string")) partials.add(pair); }
    }
    current = node.parent;
  }
  const chosen = selectMessages(selected.reverse(), options.rounds, options.answersOnly);
  const captured = await captureText(chosen.map(m => `## ${m[0] === "user" ? "我问" : "回答"}\n\n${m[1]}`).join("\n\n"), source, "public_page", chosen);
  return { ...captured, partial: chosen.some(pair => partials.has(pair)), title: typeof data.title === "string" ? data.title.slice(0, 200) : captured.title };
}
