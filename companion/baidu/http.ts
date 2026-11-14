/** Bounded standard-fetch adapter. Obsidian requestUrl gets its own mobile adapter. */
import type { HttpJson, JsonReply } from "./reader.ts";

export class FetchJsonHttp implements HttpJson {
  fetcher: typeof fetch;
  maxBytes: number;
  constructor(fetcher: typeof fetch = fetch, maxBytes = 2 * 1048576) {
    if (!Number.isSafeInteger(maxBytes) || maxBytes < 1 || maxBytes > 2 * 1048576) throw new Error("Invalid response budget");
    this.fetcher = fetcher; this.maxBytes = maxBytes;
  }
  async get(url: string): Promise<JsonReply> {
    const target = new URL(url);
    if (target.origin !== "https://pan.baidu.com" || target.username || target.password ||
        target.pathname !== "/rest/2.0/xpan/file" || target.searchParams.get("method") !== "list") {
      throw new Error("HTTP target not allowed");
    }
    try {
      const response = await this.fetcher(target.toString(), { redirect: "error", signal: AbortSignal.timeout(20000), credentials: "omit" });
      if (response.status !== 200) return { status: response.status, data: null };
      if (!response.body) throw new Error("Missing response body");
      const stream = response.body.getReader();
      const chunks: Uint8Array[] = [];
      let size = 0;
      try {
        while (true) {
          const next = await stream.read();
          if (next.done) break;
          size += next.value.byteLength;
          if (size > this.maxBytes) throw new Error("Response exceeds budget");
          chunks.push(next.value);
        }
      } finally { await stream.cancel().catch(() => {}); stream.releaseLock(); }
      const bytes = new Uint8Array(size);
      let offset = 0;
      for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
      return { status: response.status, data: JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)) };
    } catch { throw new Error("Baidu HTTP request or response failed"); }
  }
}
