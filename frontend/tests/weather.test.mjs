// 天气卡：5 天缩略条、点选切换、8 项日级数据与转义。
import test from "node:test";
import assert from "node:assert/strict";
import {
  clockPieces, startClock, clockHtml, weatherHtml, forecastDays, clampSelection, cityOptions,
} from "../weather.js";

const PAYLOAD = {
  city: "南昌", source: "env", note: "", cities: ["赣州", "南昌", "上海", "北京"],
  temperature: 17.3, apparent_temperature: 16.1, relative_humidity: 66,
  wind_speed: 10.1, weather_code: 0, weather: "晴", icon: "☀️",
  observed_at: "2026-10-06T23:15", cached: false,
  forecast: [
    {
      day: "2026-10-06", weekday: "周二", weather: "晴", icon: "☀️",
      high: 26.4, low: 17.1, apparent_high: 28.0, apparent_low: 16.2,
      humidity: 60, precipitation: 10, wind_direction: "东北风", wind_speed: 12.4,
      pressure: 1021, sunrise: "06:12", sunset: "17:52",
    },
    {
      day: "2026-10-07", weekday: "周三", weather: "小雨", icon: "🌧️",
      high: 21.2, low: 16.5, apparent_high: 22.1, apparent_low: 15.0,
      humidity: 85, precipitation: 80, wind_direction: "南风", wind_speed: 20.1,
      pressure: 1015, sunrise: "06:13", sunset: "17:51",
    },
    {
      day: "2026-10-08", weekday: "周四", weather: "晴", icon: "☀️",
      high: 25.0, low: 18.0, apparent_high: 26.2, apparent_low: 17.4,
      humidity: 55, precipitation: 0, wind_direction: "北风", wind_speed: 8.0,
      pressure: 1018, sunrise: "06:14", sunset: "17:50",
    },
  ],
};

test("forecastDays trims to five days and labels the first one 今天", () => {
  const days = forecastDays(PAYLOAD);
  assert.equal(days.length, 3);
  assert.equal(days[0].label, "今天");
  assert.equal(days[1].label, "周三");
  assert.equal(days[0].date, "10-06");
  assert.equal(days[1].precipitation, 80);
  assert.equal(days[1].windDirection, "南风");

  const six = forecastDays({ forecast: [...PAYLOAD.forecast, { day: "2026-10-09" },
    { day: "2026-10-10" }, { day: "2026-10-11" }] });
  assert.equal(six.length, 5); // 最多 5 天
  assert.deepEqual(forecastDays(null), []);
  assert.deepEqual(forecastDays({}), []);
});

test("clampSelection keeps a stale index inside the list", () => {
  assert.equal(clampSelection(0, 5), 0);
  assert.equal(clampSelection(3, 5), 3);
  assert.equal(clampSelection(9, 5), 4);
  assert.equal(clampSelection(-2, 5), 0);
  assert.equal(clampSelection(undefined, 5), 0);
  assert.equal(clampSelection("2", 5), 2);
  assert.equal(clampSelection(3, 0), 0); // 没有预报数据也不炸
});

test("the top half shows today's live reading and all eight daily items", () => {
  const html = weatherHtml(PAYLOAD, { selected: 0 });
  assert.ok(html.includes("17.3°"));            // 今天用实况温度
  assert.ok(html.includes("南昌") && html.includes("晴"));
  assert.ok(html.includes("实况 23:15"));
  assert.ok(html.includes("高 26° · 低 17°"));
  for (const label of ["体感", "湿度", "降水可能性", "风向", "风速", "气压", "日出", "日落"]) {
    assert.ok(html.includes(`<small>${label}</small>`), label);
  }
  assert.ok(html.includes("28° / 16°"));        // 体感最高/最低
  assert.ok(html.includes("东北风") && html.includes("12.4 km/h"));
  assert.ok(html.includes("1021 hPa") && html.includes("06:12") && html.includes("17:52"));
  assert.ok(html.includes('<span class="weather-week-name">今天</span>'));
  assert.ok(html.includes('data-weather-day="2"'));  // 5 天缩略条按天出按钮
});

test("picking another day swaps the top half and marks the tile", () => {
  const html = weatherHtml(PAYLOAD, { selected: 1 });
  assert.ok(html.includes("21°"));              // 周三当天最高温当主数字（21.2 → 21°）
  assert.ok(html.includes("周三") && html.includes("小雨"));
  assert.ok(html.includes("高 21° · 低 17°"));
  assert.ok(html.includes("80%") && html.includes("南风") && html.includes("20.1 km/h"));
  assert.equal(html.includes("实况 23:15"), false); // 实况只在今天那格
  assert.ok(html.includes('data-weather-day="1" aria-pressed="true"'));
  assert.ok(html.includes('data-weather-day="0" aria-pressed="false"'));
});

test("the five-day strip renders five buttons with compact ranges", () => {
  const html = weatherHtml(PAYLOAD, { selected: 0 });
  assert.equal((html.match(/data-weather-day="/g) || []).length, 3);
  assert.ok(html.includes("26°/17°") && html.includes("21°/17°")); // 紧凑写法，窄卡片放得下
  assert.ok(html.includes('aria-label="查看 周三 10-07 的天气：小雨"'));
});

test("the city is a dropdown when the backend ships a list", () => {
  const html = weatherHtml(PAYLOAD, { selected: 0 });
  assert.ok(html.includes('<select class="weather-city-select" aria-label="选择城市">'));
  assert.ok(html.includes('<option value="上海">上海</option>'));
  assert.ok(html.includes('<option value="南昌" selected>南昌</option>'));
  // 换了城市、后端不再给清单（老后端）：退回纯文字，不画空下拉
  const old = weatherHtml({ ...PAYLOAD, cities: [] }, { selected: 0 });
  assert.equal(old.includes("weather-city-select"), false);
  assert.ok(old.includes('class="weather-city-name">南昌<'));
});

test("cityOptions keeps the current city even when it is off the list", () => {
  assert.deepEqual(cityOptions(PAYLOAD), ["赣州", "南昌", "上海", "北京"]);
  assert.deepEqual(cityOptions({ ...PAYLOAD, city: "火星" }), ["火星", "赣州", "南昌", "上海", "北京"]);
  assert.deepEqual(cityOptions({ city: "南昌" }), []);
  assert.deepEqual(cityOptions(null), []);
  // 城市名里的引号/尖括号要被转义（它会进 option 的 value 属性）
  const tricky = weatherHtml({ ...PAYLOAD, city: '"><script>x</script>', cities: ['"><script>x</script>'] },
    { selected: 0 });
  assert.equal(tricky.includes("<script>"), false);
});

test("weather html escapes text and degrades without a forecast", () => {
  const hostile = {
    ...PAYLOAD, city: "<b>南昌</b>", weather: "<script>x</script>", icon: "<img src=x>",
    forecast: [{ ...PAYLOAD.forecast[0], weather: "<i>雨</i>", wind_direction: "<b>北</b>" }],
  };
  const html = weatherHtml(hostile, { selected: 0 });
  assert.equal(html.includes("<script>"), false);
  assert.equal(html.includes("<img src=x>"), false);
  assert.equal(html.includes("<i>雨</i>"), false);
  assert.ok(html.includes("&lt;b&gt;北&lt;/b&gt;"));

  // 没有 forecast（老后端/降级）：只画实况，不画缩略条
  const bare = weatherHtml({ ...PAYLOAD, forecast: [] }, { selected: 2 });
  assert.ok(bare.includes("17.3°") && bare.includes("南昌"));
  assert.equal(bare.includes("weather-week"), false);
  assert.equal(bare.includes("降水可能性"), false);

  // 拿不到数据时的空态
  const empty = weatherHtml(null, { selected: 0 });
  assert.ok(empty.includes("天气暂时拿不到"));
  assert.equal(empty.includes("weather-week"), false);

  // 缺字段的那一天：— 占位，不出现 NaN
  const partial = weatherHtml({ ...PAYLOAD, forecast: [{ day: "2026-10-06", weekday: "周二" }] },
    { selected: 0 });
  assert.equal(partial.includes("NaN"), false);
  assert.ok(partial.includes("—"));
});

test("clock pieces and the ticking clock stay shanghai-bound", () => {
  const pieces = clockPieces(Date.parse("2026-10-06T15:15:07Z"));
  assert.deepEqual(pieces, { time: "23:15:07", date: "2026-10-06", weekday: "周二" });
  assert.ok(clockHtml(pieces).includes("23:15:07"));

  const ticks = [];
  const scheduled = [];
  startClock((value) => ticks.push(value.time), (fn, ms) => scheduled.push(ms),
    () => Date.parse("2026-10-06T15:15:07.250Z"));
  assert.deepEqual(ticks, ["23:15:07"]);
  assert.deepEqual(scheduled, [750]); // 对齐到下一个整秒
});
