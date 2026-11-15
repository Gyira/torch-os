import { sourceUrl } from "./share.ts";
export const queueHeader = "# Torch queue v1\n\n```json\n";
export function queueEnvelope(value: unknown) { return queueHeader + JSON.stringify(value) + "\n```\n"; }
export function queueDecode(text: string): Record<string, unknown> {
  text = text.replace(/\r\n/g, "\n");
  if (!text.startsWith(queueHeader) || !text.endsWith("\n```\n") || new TextEncoder().encode(text).length > 800000) throw new Error("收件记录格式无效");
  const value = JSON.parse(text.slice(queueHeader.length, -5));
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("收件记录无效");
  return value;
}
export function queueRequest(workspace: string, device: string, action: "share_preview" | "share_commit" | "cancel", fields: Record<string, unknown>) {
  if (!workspace || !device) throw new Error("请先配对工作区再使用电脑回退队列");
  if (action === "share_preview") fields = { ...fields, source_url: sourceUrl(String(fields.source_url)) };
  return { ...fields, schema_version: 1, request_id: crypto.randomUUID(), workspace_id: workspace, device_id: device, created_at: new Date().toISOString(), action };
}
