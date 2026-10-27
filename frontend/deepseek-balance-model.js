// DeepSeek 余额小组件的纯格式化/本机预警逻辑（不碰 DOM，方便单测）。
export const DEEPSEEK_TOP_UP_URL = "https://platform.deepseek.com/top_up";
export const DEEPSEEK_ALERT_KEY = "torch-os:deepseek-balance-alert";

/** 金额按当前币种显示两位小数；取不到数字就显示破折号，不臆造 0。 */
export function formatBalanceAmount(value, currency = "CNY") {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  const code = String(currency || "CNY").toUpperCase();
  try {
    return new Intl.NumberFormat("zh-CN", {
      style: "currency", currency: code, currencyDisplay: "narrowSymbol",
      minimumFractionDigits: 2, maximumFractionDigits: 2,
    }).format(number);
  } catch {
    return `${code} ${number.toFixed(2)}`;
  }
}

/** 只显示北京时间 HH:MM；接口时间不可读就留空。 */
export function formatBalanceTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  try {
    return new Intl.DateTimeFormat("zh-CN", {
      hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: "Asia/Shanghai",
    }).format(date);
  } catch {
    return "";
  }
}

/** 0 = 关闭预警；其他值最多两位小数，并限制在一个合理上限内。 */
export function normalizeAlertThreshold(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return 0;
  return Math.min(1_000_000_000, Math.round(number * 100) / 100);
}

export function readAlertThreshold(storage = globalThis.localStorage) {
  try {
    return normalizeAlertThreshold(storage?.getItem(DEEPSEEK_ALERT_KEY) || 0);
  } catch {
    return 0;
  }
}

export function writeAlertThreshold(value, storage = globalThis.localStorage) {
  const threshold = normalizeAlertThreshold(value);
  try {
    if (threshold > 0) storage?.setItem(DEEPSEEK_ALERT_KEY, String(threshold));
    else storage?.removeItem(DEEPSEEK_ALERT_KEY);
  } catch { /* 隐私模式：预警只在本次页面内生效 */ }
  return threshold;
}

export function isLowBalance(total, threshold) {
  const limit = normalizeAlertThreshold(threshold);
  const number = Number(total);
  return limit > 0 && Number.isFinite(number) && number <= limit;
}

const SOURCE_LABELS = {
  saved: "本机保存的 Key",
  env: "环境变量 DEEPSEEK_API_KEY",
  dsh: "DSH 凭据",
};

const SOURCE_SHORT = {
  saved: "本机",
  env: "环境变量",
  dsh: "DSH",
};

export function balanceSourceLabel(source) {
  return SOURCE_LABELS[source] || "未配置";
}

export function balanceSourceShort(source) {
  return SOURCE_SHORT[source] || "未配置";
}
