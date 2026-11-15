const encoder = new TextEncoder(), decoder = new TextDecoder("utf-8", { fatal: true });
const base64 = (bytes: Uint8Array) => btoa(Array.from(bytes, n => String.fromCharCode(n)).join(""));
async function key(password: string, salt: Uint8Array) {
  if (password.length < 12) throw new Error("配对密码至少 12 位");
  const material = await crypto.subtle.importKey("raw", encoder.encode(password), "PBKDF2", false, ["deriveKey"]);
  return crypto.subtle.deriveKey({ name: "PBKDF2", salt, iterations: 210000, hash: "SHA-256" }, material, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
}
export async function exportPair(value: unknown, password: string) {
  const salt = crypto.getRandomValues(new Uint8Array(16)), iv = crypto.getRandomValues(new Uint8Array(12));
  const encrypted = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv }, await key(password, salt), encoder.encode(JSON.stringify(value))));
  const result = new Uint8Array(29 + encrypted.length); result[0] = 1; result.set(salt, 1); result.set(iv, 17); result.set(encrypted, 29);
  return "TORCHPAIR1:" + base64(result);
}
export async function importPair(value: string, password: string): Promise<unknown> {
  if (!value.startsWith("TORCHPAIR1:") || value.length > 50000) throw new Error("配对码无效");
  try {
    const bytes = Uint8Array.from(atob(value.slice(11)), c => c.charCodeAt(0));
    if (bytes[0] !== 1) throw new Error();
    const raw = await crypto.subtle.decrypt({ name: "AES-GCM", iv: bytes.slice(17, 29) }, await key(password, bytes.slice(1, 17)), bytes.slice(29));
    return JSON.parse(decoder.decode(raw));
  } catch { throw new Error("配对密码错误或配对码损坏"); }
}
