// 天气小组件：紧凑卡片、详情浮窗、常用城市与转义。
import test from "node:test";
import assert from "node:assert/strict";
import {
  WEATHER_AUTO_KEY, addFavorite, cityKey, clampSelection, clockDateLabel,
  clockHtml, clockPieces, findFavorite, forecastDays, normalizeCity, normalizeFavorites,
  removeFavorite, startClock, weatherAriaLabel, weatherCardHtml, weatherDetailHtml,
  weatherSearchResultsHtml,
} from "../weather.js";

const PAYLOAD = {
  city: "南昌", source: "env", note: "", cities: ["赣州", "南昌", "上海", "北京"],
  latitude: 28.68, longitude: 115.86,
  temperature: 17.3, apparent_temperature: 16.1, relative_humidity: 66,
  wind_speed: 10.1, weather_code: 0, weather: "晴", icon: "☀️",
  observed_at: "2026-10-06T23:15", cached: false,
  fetched_at: "2026-10-06T23:15:00",
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

const FAVORITES = [
  { name: "南昌" },
  { name: "上海", latitude: 31.23, longitude: 121.47 },
];

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

test("the compact card only shows time, date, city and today's weather", () => {
  const html = weatherCardHtml(PAYLOAD);
  assert.ok(html.includes("17°"));                       // 实况温度取整
  assert.ok(html.includes("南昌") && html.includes("晴"));
  assert.ok(html.includes("最高 26° · 最低 17°"));       // 今日高/低温
  for (const hidden of ["体感", "湿度", "降水可能性", "weather-week", "weather-city-select", "五日"]) {
    assert.equal(html.includes(hidden), false, `${hidden} 应该在详情浮窗里，不在卡片上`);
  }
  // 没有数据时的空态
  assert.ok(weatherCardHtml(null).includes("天气暂时拿不到"));
  assert.ok(weatherCardHtml({ ...PAYLOAD, forecast: [] }).includes("今日高低温暂缺"));
});

test("the card aria label reads out city, weather and the daily range", () => {
  const label = weatherAriaLabel(PAYLOAD);
  assert.ok(label.includes("南昌") && label.includes("晴") && label.includes("17°"));
  assert.ok(label.includes("最高 26°") && label.includes("最低 17°"));
  assert.ok(label.includes("点击查看详情"));
  assert.ok(weatherAriaLabel(null).includes("天气暂时拿不到"));
});

test("the detail dialog contains the 5-day strip, city management and every metric", () => {
  const html = weatherDetailHtml(PAYLOAD, {
    selected: 0, favorites: FAVORITES, selectedKey: "南昌",
  });
  assert.ok(html.includes("weather-city-search"));         // 搜索添加
  assert.ok(html.includes("data-weather-city-remove=\"南昌\"")); // 删除常用城市
  assert.ok(html.includes(`data-weather-city="${cityKey(FAVORITES[1])}"`));
  assert.ok(html.includes("data-weather-day=\"2\""));      // 5 日缩略条
  for (const label of ["体感", "湿度", "降水可能性", "风向", "风速", "气压", "日出", "日落"]) {
    assert.ok(html.includes(`<span>${label}</span>`), label);
  }
  assert.ok(html.includes("东北风") && html.includes("12.4 km/h"));
  assert.ok(html.includes("1021 hPa") && html.includes("06:12") && html.includes("17:52"));
  assert.ok(html.includes("更新于 2026-10-06 23:15"));
});

test("picking another day swaps the hero and metrics in the dialog", () => {
  const html = weatherDetailHtml(PAYLOAD, {
    selected: 1, favorites: FAVORITES, selectedKey: "南昌",
  });
  assert.ok(html.includes("21°"));                 // 周三最高温当主数字
  assert.ok(html.includes("小雨") && html.includes("周三"));
  assert.ok(html.includes("80%") && html.includes("南风") && html.includes("20.1 km/h"));
  assert.ok(html.includes('data-weather-day="1" aria-pressed="true"'));
  assert.ok(html.includes('data-weather-day="0" aria-pressed="false"'));
  assert.equal(html.includes("实况 23:15"), false); // 实况只在今天那格
});

test("the current location row appears when no saved city is selected", () => {
  const html = weatherDetailHtml(PAYLOAD, {
    selected: 0, favorites: FAVORITES, selectedKey: WEATHER_AUTO_KEY,
  });
  assert.ok(html.includes("当前定位"));
  assert.ok(html.includes(`data-weather-city="${WEATHER_AUTO_KEY}"`));
  assert.ok(html.includes(`data-weather-city="${cityKey(FAVORITES[1])}"`));
  assert.ok(html.includes("data-weather-city-save-current")); // 当前定位可直接加入常用
});

test("the dialog handles loading, failure and notes without crashing", () => {
  assert.ok(weatherDetailHtml(null, { loading: true }).includes("正在读取天气…"));
  const failed = weatherDetailHtml(null, { notice: "无法连接天气服务" });
  assert.ok(failed.includes("天气暂时拿不到"));
  assert.ok(failed.includes("无法连接天气服务"));
  assert.ok(failed.includes("data-weather-retry"));
  // 天气取不到时仍能管理和搜索常用城市
  assert.ok(failed.includes("data-weather-city-search"));

  const cached = weatherDetailHtml({ ...PAYLOAD, cached: true, source: "ip" }, {
    favorites: FAVORITES, selectedKey: "南昌", notice: "刷新失败，显示上次数据",
  });
  assert.ok(cached.includes("缓存") && cached.includes("IP 定位"));
  assert.ok(cached.includes("刷新失败，显示上次数据"));
});

test("favorites normalize, dedupe and keep same-name cities apart by coordinates", () => {
  assert.deepEqual(normalizeCity(" 南昌 "), {
    name: "南昌", latitude: null, longitude: null, admin1: "", country: "",
  });
  assert.equal(normalizeCity(""), null);
  assert.equal(normalizeCity({ name: "" }), null);
  const withBadCoords = normalizeCity({ name: "火星", latitude: 999, longitude: 0 });
  assert.equal(withBadCoords.latitude, null); // 越界坐标丢掉，城市名保留

  const list = normalizeFavorites(["南昌", { name: "南昌" }, "上海", null]);
  assert.deepEqual(list.map((city) => city.name), ["南昌", "上海"]);

  const springfieldA = normalizeCity({ name: "Springfield", latitude: 39.8, longitude: -89.6 });
  const springfieldB = normalizeCity({ name: "Springfield", latitude: 42.1, longitude: -72.6 });
  assert.notEqual(cityKey(springfieldA), cityKey(springfieldB));

  const added = addFavorite(list, springfieldA);
  assert.equal(added.length, 3);
  assert.equal(addFavorite(added, springfieldA).length, 3); // 重复添加不增加
  assert.equal(findFavorite(added, cityKey(springfieldA)).name, "Springfield");
  assert.deepEqual(removeFavorite(added, "上海").map((city) => city.name), ["南昌", "Springfield"]);
});

test("city search results mark already-added cities and escape hostile names", () => {
  const state = {
    query: "南",
    results: [{ name: "南昌" }, { name: "南京", admin1: "江苏省", country: "中国" }],
    loading: false, error: "",
  };
  const html = weatherSearchResultsHtml(state, FAVORITES);
  assert.ok(html.includes("data-weather-city-add=\"0\" disabled")); // 南昌已添加
  assert.ok(html.includes("data-weather-city-add=\"1\""));           // 南京可添加
  assert.ok(html.includes("江苏省 · 中国"));

  const hostile = weatherSearchResultsHtml({
    query: "x", results: [{ name: "<img src=x>", admin1: "<b>省</b>" }], loading: false, error: "",
  }, []);
  assert.equal(hostile.includes("<img src=x>"), false);
  assert.equal(hostile.includes("<b>省</b>"), false);

  assert.ok(weatherSearchResultsHtml({ query: "", results: [] }, []).includes("weather-search-status"));
  assert.ok(weatherSearchResultsHtml({ query: "zz", results: [], error: "在线搜索不可用" })
    .includes("在线搜索不可用"));
});

test("weather html escapes text and degrades without a forecast", () => {
  const hostile = {
    ...PAYLOAD, city: "<b>南昌</b>", weather: "<script>x</script>", icon: "<img src=x>",
    forecast: [{ ...PAYLOAD.forecast[0], weather: "<i>雨</i>", wind_direction: "<b>北</b>" }],
  };
  const card = weatherCardHtml(hostile);
  assert.equal(card.includes("<script>"), false);
  assert.equal(card.includes("<img src=x>"), false);

  const detail = weatherDetailHtml(hostile, { favorites: [], selectedKey: "" });
  assert.equal(detail.includes("<script>"), false);
  assert.equal(detail.includes("<img src=x>"), false);
  assert.equal(detail.includes("<i>雨</i>"), false);
  assert.ok(detail.includes("&lt;b&gt;北&lt;/b&gt;"));

  // 没有 forecast（老后端/降级）：详情只画实况，不画缩略条
  const bare = weatherDetailHtml({ ...PAYLOAD, forecast: [] }, { favorites: [], selectedKey: "" });
  assert.ok(bare.includes("17.3°") && bare.includes("南昌"));
  assert.equal(bare.includes("weather-detail-days"), false);
  assert.ok(bare.includes("这次没有拿到五日预报"));

  // 缺字段的那一天：— 占位，不出现 NaN
  const partial = weatherDetailHtml(
    { ...PAYLOAD, forecast: [{ day: "2026-10-06", weekday: "周二" }] },
    { favorites: [], selectedKey: "" },
  );
  assert.equal(partial.includes("NaN"), false);
  assert.ok(partial.includes("—"));
});

test("clock pieces format as 年月日 + 星期 and the ticking clock stays shanghai-bound", () => {
  const pieces = clockPieces(Date.parse("2026-10-06T15:15:07Z"));
  assert.deepEqual(pieces, { time: "23:15:07", date: "2026-10-06", weekday: "周二" });
  assert.equal(clockDateLabel(pieces), "2026年10月6日 周二");
  const html = clockHtml(pieces);
  assert.ok(html.includes("23:15:07") && html.includes("2026年10月6日 周二"));

  const ticks = [];
  const scheduled = [];
  startClock((value) => ticks.push(value.time), (fn, ms) => scheduled.push(ms),
    () => Date.parse("2026-10-06T15:15:07.250Z"));
  assert.deepEqual(ticks, ["23:15:07"]);
  assert.deepEqual(scheduled, [750]); // 对齐到下一个整秒
});
