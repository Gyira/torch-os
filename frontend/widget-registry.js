// 工作台小组件清单（2026-10-29，v1.0 P1）。
// 设置页的开关、工作台的显隐与默认分栏都以这里为准；新增组件时同时补：
// 1) index.html 里的 data-widget 节点；2) workbench.js 的初始化/加载；3) 这里的一条清单。
import { WIDGET_LAYOUT_DEFAULT } from "./widget-order.js";

export const WIDGET_MANIFEST = Object.freeze([
  {
    key: "weather", label: "天气与时钟", column: "left",
    description: "当前城市天气、体感与时钟；数据来自 Open-Meteo，城市可设置。",
  },
  {
    key: "countdown", label: "倒计时", column: "left",
    description: "目标日期/时间倒计时，支持提醒与背景；配置存在 .torch-os。",
  },
  {
    key: "anniversary", label: "纪念日", column: "left",
    description: "从某天起已经过去多久，支持公历/农历与每年提醒。",
  },
  {
    key: "duty", label: "值班", column: "left",
    description: "今天与未来几天的上班/休息和值乘车次，来自恋爱系统的排班表。",
  },
  {
    key: "almanac", label: "运势黄历", column: "right",
    description: "干支、农历、个人运势与宜忌；点击查看完整黄历。",
  },
  {
    key: "train-tracker", label: "列车追踪", column: "right",
    description: "关注车次的运行状态、当前站与区间进度。",
  },
  {
    key: "deepseek", label: "DeepSeek 余额", column: "right",
    description: "官方余额接口的本地代理；只显示余额，不显示 Key。",
  },
]);

export const WIDGET_KEYS = Object.freeze(WIDGET_MANIFEST.map((item) => item.key));

export function widgetMeta(key) {
  return WIDGET_MANIFEST.find((item) => item.key === key) || null;
}

export function widgetLabel(key) {
  return widgetMeta(key)?.label || String(key || "");
}

/** HTML 里的默认落位优先，清单只作说明与设置页展示。 */
export function defaultWidgetColumn(key) {
  if (WIDGET_LAYOUT_DEFAULT.left.includes(key)) return "left";
  if (WIDGET_LAYOUT_DEFAULT.right.includes(key)) return "right";
  return widgetMeta(key)?.column === "left" ? "left" : "right";
}
