export class RetryGate {
  failures = 0; next = 0;
  allowed(now = Date.now()) { return now >= this.next; }
  success() { this.failures = 0; this.next = 0; }
  failure(now = Date.now(), random = Math.random()) {
    this.failures++;
    const delay = Math.min(300000, 5000 * 2 ** Math.min(this.failures - 1, 6));
    this.next = now + Math.round(delay * (0.8 + Math.max(0, Math.min(1, random)) * 0.4));
  }
}
export async function bounded<T>(promise: PromiseLike<T>, milliseconds = 30000): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try { return await Promise.race([Promise.resolve(promise), new Promise<T>((_, reject) => { timer = setTimeout(() => reject(new Error("网络请求超时；本地记录保留，可稍后重试")), milliseconds); })]); }
  finally { if (timer) clearTimeout(timer); }
}
