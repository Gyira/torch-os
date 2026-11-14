/** Read-only XPan adapter based on the reviewed upstream API usage; no deletions. */
export type JsonReply = { status: number; data: unknown };
export interface HttpJson { get(url: string): Promise<JsonReply>; }
export interface TokenProvider { token(forceRefresh?: boolean): Promise<string>; }
export type Entry = { path: string; fs_id: string | number; isdir: 0 | 1; size: number };

export class BaiduReader {
  http: HttpJson;
  tokens: TokenProvider;
  root: string;
  pageSize: number;
  constructor(http: HttpJson, tokens: TokenProvider, root: string, pageSize = 1000) {
    if (!root.startsWith("/apps/") || root.endsWith("/") || /[\\\x00-\x1f]/.test(root) ||
        root.split("/").slice(1).some(p => !p || p === "." || p === "..") || !Number.isInteger(pageSize) || pageSize < 1 || pageSize > 1000) {
      throw new Error("Invalid Baidu namespace");
    }
    this.http = http; this.tokens = tokens; this.root = root; this.pageSize = pageSize;
  }
  private async request(params: Record<string, string>): Promise<Record<string, unknown>> {
    for (let attempt = 0; attempt < 2; attempt++) {
      // Regenerate the request after token refresh; never retry the old URL/token.
      let token: string;
      try { token = await this.tokens.token(attempt > 0); }
      catch { throw new Error("Baidu authorization provider failed"); }
      if (!token) throw new Error("Baidu token missing");
      const url = new URL("https://pan.baidu.com/rest/2.0/xpan/file");
      url.search = new URLSearchParams({ ...params, access_token: token }).toString();
      let response: JsonReply;
      try { response = await this.http.get(url.toString()); }
      catch { throw new Error("Baidu network request failed"); } // don't echo token-bearing exceptions
      if (response.status !== 200) throw new Error(`Baidu HTTP ${response.status}`);
      const data = response.data as Record<string, unknown>;
      if (!data || typeof data !== "object" || Array.isArray(data) || !Number.isInteger(data.errno)) throw new Error("Invalid Baidu response");
      if (data.errno === -6 && attempt === 0) continue;
      if (data.errno !== 0) throw new Error(`Baidu errno ${data.errno}; listing not authoritative`);
      return data;
    }
    throw new Error("Baidu authorization failed");
  }
  async listDirectory(subdir: "commits" | "objects"): Promise<Entry[]> {
    if (subdir !== "commits" && subdir !== "objects") throw new Error("Unsupported namespace");
    const directory = `${this.root}/${subdir}`;
    const result: Entry[] = [];
    const seen = new Set<string>();
    for (let start = 0; start <= 10000; start += this.pageSize) {
      const data = await this.request({ method: "list", dir: directory, order: "name", desc: "0", start: String(start), limit: String(this.pageSize), folder: "0" });
      if (!Array.isArray(data.list) || data.list.length > this.pageSize) throw new Error("Invalid Baidu listing");
      for (const raw of data.list) {
        const entry = raw as Entry;
        if (!entry || typeof entry.path !== "string" || !entry.path.startsWith(directory + "/") ||
            entry.path.slice(directory.length + 1).includes("/") || entry.isdir !== 0 ||
            !["number", "string"].includes(typeof entry.fs_id) || !Number.isSafeInteger(entry.size) || entry.size < 0) {
          throw new Error("Unexpected entry in Baidu protocol namespace");
        }
        if (seen.has(entry.path)) throw new Error("Baidu pagination repeated entries; retry complete scan");
        seen.add(entry.path);
        result.push({ path: entry.path, fs_id: entry.fs_id, isdir: 0, size: entry.size });
        if (result.length > 10000) throw new Error("Baidu scan budget exceeded");
      }
      if (data.has_more !== undefined && data.has_more !== 0 && data.has_more !== 1) throw new Error("Invalid Baidu pagination marker");
      if (data.has_more === 0 || data.has_more === undefined && data.list.length < this.pageSize) return result;
      if (data.list.length === 0) throw new Error("Baidu pagination made no progress");
    }
    throw new Error("Baidu scan budget exceeded");
  }
}
