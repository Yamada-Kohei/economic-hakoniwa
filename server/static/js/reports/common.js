// 島の統計の画面（経済・市場・施設・財政・金融）で共通に使う道具: 数の書き方、期間の切り替え、
// 統計の系列の API（/api/islands/<id>/economy）を読んで複数のグラフをまとめて描くこと。

import { LineChart } from "../chart/line.js";
import { getJson } from "../island/api.js";

const compact = new Intl.NumberFormat("ja-JP", { notation: "compact", maximumFractionDigits: 1 });
const whole = new Intl.NumberFormat("ja-JP", { maximumFractionDigits: 0 });
const fine = new Intl.NumberFormat("ja-JP", { maximumFractionDigits: 2 });

/**
 * 統計の値を文字にする。unit はサーバーの統計の単位（money・%・rate・percent・index・ratio、
 * または数の後ろに付ける文字）。exact なら丸めずに（お金は1の位まで）書く。
 */
export function formatValue(value, unit, currency = "", exact = false) {
  if (value == null || !Number.isFinite(value)) return "-";
  const number = exact ? (Math.abs(value) >= 1000 ? whole : fine) : compact;
  switch (unit) {
    case "money":
      return `${value < 0 ? "-" : ""}${currency}${number.format(Math.abs(value))}`;
    case "%":
      return `${(value * 100).toFixed(exact ? 1 : 0)}%`;
    case "rate":
      return `${value.toFixed(2)}%`;
    case "percent":
      return `${value.toFixed(1)}%`;
    case "index":
      return value.toFixed(1);
    case "ratio":
      return `${value.toFixed(2)} 倍`;
    default:
      return `${number.format(value)}${unit || ""}`;
  }
}

/** 単位 unit のグラフ（目盛りは短く、吹き出しは細かく書く）。 */
export function makeChart(container, unit, currency, label) {
  return new LineChart(container, {
    label,
    format: (v) => formatValue(v, unit, currency),
    detail: (v) => formatValue(v, unit, currency, true),
    zero: unit === "money" || unit === "%" || unit === "percent",
    integer: unit === "money",
  });
}

/**
 * 期間の切り替えのボタン（data-range に ターン数 か all）。押すと onChange(ターン数 か null) を呼ぶ。
 * 選んだ期間は、同じ画面の別のボタンの組にもそろえる。
 */
export function attachRange(root, onChange) {
  root.addEventListener("click", (e) => {
    const button = e.target.closest("button[data-range]");
    if (!button) return;
    for (const b of root.querySelectorAll("button[data-range]")) {
      b.setAttribute("aria-pressed", String(b.dataset.range === button.dataset.range));
    }
    onChange(button.dataset.range === "all" ? null : Number(button.dataset.range));
  });
}

/** 期間（ターン数。null はすべて）と直近のターンから、API に渡す from。 */
export function rangeQuery(range, turn) {
  return range == null ? "from=0" : `from=${Math.max(0, turn - range + 1)}`;
}

/**
 * 統計のグラフの組: figure[data-names]（カンマ区切りの統計の名前。末尾の * はその頭の統計すべて）
 * ごとにグラフを作り、名前をまとめて1回の API で読む。
 * options: url（統計の系列の API）、turn（直近のターン）、currency（通貨記号）
 */
export class ChartGroup {
  constructor(figures, { url, turn, currency }) {
    this.url = url;
    this.turn = turn;
    this.currency = currency;
    this.range = 120;
    this.items = [];
    for (const figure of figures) this.add(figure);
  }

  /** グラフを1つ足す（読み込むのは load のとき）。 */
  add(figure) {
    const item = { figure, chart: null };
    this.items.push(item);
    return item;
  }

  names() {
    const names = new Set();
    for (const { figure } of this.items) {
      for (const n of (figure.dataset.names || "").split(",")) if (n) names.add(n);
    }
    return [...names];
  }

  async load(range = this.range) {
    this.range = range;
    const names = this.names();
    if (!names.length) return;
    let data;
    try {
      const q = `names=${encodeURIComponent(names.join(","))}&${rangeQuery(range, this.turn)}`;
      data = await getJson(`${this.url}?${q}`);
    } catch {
      for (const item of this.items) this.chartOf(item, "").fail();
      return;
    }
    for (const item of this.items) {
      if (item.figure.dataset.names) this.draw(item, data);
    }
  }

  chartOf(item, unit) {
    if (!item.chart || item.unit !== unit) {
      item.figure.querySelector(".line-chart")?.remove();
      const body = document.createElement("div");
      body.className = "line-chart";
      item.figure.append(body);
      const caption = item.figure.querySelector("figcaption")?.textContent.trim() || "";
      const label = item.figure.dataset.label || caption;
      item.chart = makeChart(body, unit, this.currency, label);
      item.unit = unit;
    }
    return item.chart;
  }

  draw(item, data) {
    const keys = [];
    for (const n of (item.figure.dataset.names || "").split(",")) {
      if (!n) continue;
      if (n.endsWith("*")) keys.push(...Object.keys(data.series).filter((k) => k.startsWith(n.slice(0, -1))).sort());
      else if (n in data.series) keys.push(n);
    }
    const unit = keys.length ? data.units[keys[0]] : "";
    const series = keys.map((k) => ({ key: k, label: data.labels[k] || k, values: data.series[k] }));
    // 系列ごとに、値のあるターンだけに詰める（全部の系列で値のないターンは描かない）
    const used = data.turns.map((_, i) => series.some((s) => s.values[i] != null));
    const turns = data.turns.filter((_, i) => used[i]);
    for (const s of series) s.values = s.values.filter((_, i) => used[i]);
    this.chartOf(item, unit).setData(turns, series);
  }
}

/** 画面の #charts（data-url・data-turn・data-currency）のグラフを作って読み込む。 */
export function mountCharts(root = document.getElementById("charts")) {
  if (!root) return null;
  const group = new ChartGroup(root.querySelectorAll("figure[data-names]"), {
    url: root.dataset.url,
    turn: Number(root.dataset.turn || 0),
    currency: root.dataset.currency || "",
  });
  group.load();
  const section = root.closest("section") || document;
  attachRange(section, (range) => group.load(range));
  return group;
}

/** 文字を HTML に入れても安全な形にする。 */
export { escapeHtml } from "../island/api.js";
