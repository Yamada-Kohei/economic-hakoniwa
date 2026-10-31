// 島の統計の画面（経済・市場・施設・財政・金融）で共通に使う道具: 数の書き方、期間の切り替え、
// 統計の系列の API（/api/islands/<id>/economy）を読んで複数のグラフをまとめて描くこと。

import { turnDate } from "../calendar.js";
import { LineChart } from "../chart/line.js";
import { PieChart } from "../chart/pie.js";
import { getJson } from "../island/api.js";

const compact = new Intl.NumberFormat("ja-JP", { notation: "compact", maximumFractionDigits: 1 });
const whole = new Intl.NumberFormat("ja-JP", { maximumFractionDigits: 0 });
const fine = new Intl.NumberFormat("ja-JP", { maximumFractionDigits: 2 });

/**
 * 統計の値を文字にする。unit はサーバーの統計の単位（money・money:<記号>（ほかの国の通貨の額）・
 * %・rate・percent・index・ratio・fx（為替レート）、または数の後ろに付ける文字）。exact なら丸めずに（お金は1の位まで）書く。
 */
export function formatValue(value, unit, currency = "", exact = false) {
  if (value == null || !Number.isFinite(value)) return "-";
  const number = exact ? (Math.abs(value) >= 1000 ? whole : fine) : compact;
  if (unit?.startsWith("money:")) {
    // ほかの国の通貨の額（為替の売買量など。money:<記号>。W-70）
    return `${value < 0 ? "-" : ""}${unit.slice(6)}${number.format(Math.abs(value))}`;
  }
  switch (unit) {
    case "money":
      return `${value < 0 ? "-" : ""}${currency}${number.format(Math.abs(value))}`;
    case "fx":
      return value.toFixed(exact ? 4 : 3);
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
    zero: unit === "money" || unit?.startsWith("money:") || unit === "%" || unit === "percent",
    integer: unit === "money" || unit?.startsWith("money:"),
  });
}

/** 単位 unit の円グラフ（値は細かく書く）。 */
export function makePie(container, unit, currency, label) {
  return new PieChart(container, { label, format: (v) => formatValue(v, unit, currency, true) });
}

/**
 * figure[data-slices]（[{ key, label, value }] の JSON を入れた script 要素の id）の円グラフを描く。
 * 今の値だけで、ターンを選ばない。
 */
export function mountStaticPies(root = document) {
  for (const figure of root.querySelectorAll("figure[data-slices]")) {
    const body = document.createElement("div");
    body.className = "pie-chart";
    figure.append(body);
    const label = figure.querySelector("figcaption")?.textContent.trim() || "";
    makePie(body, figure.dataset.unit || "", figure.dataset.currency || "", label).setData(JSON.parse(document.getElementById(figure.dataset.slices).textContent));
  }
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
 * ごとに折れ線を、figure[data-pie]（同じ書き方）ごとに円グラフを作り、名前をまとめて1回の API で読む。
 * 円グラフは、読んだ期間の中から選んだ1つのターン（初めは直近）の内訳を描き、つまみでターンを替える。
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
      for (const n of namesOf(figure)) names.add(n);
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
      for (const item of this.items) (item.figure.dataset.pie ? this.pieOf(item, "") : this.chartOf(item, "")).fail();
      return;
    }
    for (const item of this.items) {
      if (item.figure.dataset.pie) this.drawPie(item, data);
      else if (item.figure.dataset.names) this.draw(item, data);
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

  /** 円グラフ（と、ターンを選ぶつまみ）を作る。単位が変わったら作り直す。 */
  pieOf(item, unit) {
    if (!item.chart || item.unit !== unit) {
      item.figure.querySelector(".pie-chart")?.remove();
      const body = document.createElement("div");
      body.className = "pie-chart";
      const pick = document.createElement("label");
      pick.className = "pie-turn";
      const date = document.createElement("span");
      const slider = document.createElement("input");
      slider.type = "range";
      slider.min = "0";
      slider.step = "1";
      slider.setAttribute("aria-label", "表示するターン");
      pick.append(date, slider);
      body.append(pick);
      item.figure.append(body);
      const label = item.figure.querySelector("figcaption")?.textContent.trim() || "";
      item.chart = makePie(body, unit, this.currency, label);
      item.unit = unit;
      item.slider = slider;
      item.date = date;
      slider.addEventListener("input", () => this.showTurn(item, Number(slider.value)));
    }
    return item.chart;
  }

  drawPie(item, data) {
    const keys = keysOf(item.figure.dataset.pie, data);
    const unit = keys.length ? data.units[keys[0]] : "";
    this.pieOf(item, unit);
    // 値のあるターンだけを選べるようにする
    const used = data.turns.map((_, i) => keys.some((k) => data.series[k][i] != null));
    item.pie = {
      turns: data.turns.filter((_, i) => used[i]),
      series: keys.map((k) => ({ key: k, label: data.labels[k] || k, values: data.series[k].filter((_, i) => used[i]) })),
    };
    // 「収入・所得税」「収入・消費税」や「労働者階級の人数」「技術者の人数」のように頭か後ろが
    // そろっていれば、見出しで足りるので外す
    const many = item.pie.series.length > 1;
    const head = item.pie.series[0]?.label.match(/^[^・]+・/)?.[0];
    if (many && head && item.pie.series.every((s) => s.label.startsWith(head))) {
      for (const s of item.pie.series) s.label = s.label.slice(head.length);
    }
    const tail = item.pie.series[0]?.label.match(/の[^の]+$/)?.[0];
    if (many && tail && item.pie.series.every((s) => s.label.endsWith(tail) && s.label.length > tail.length)) {
      for (const s of item.pie.series) s.label = s.label.slice(0, -tail.length);
    }
    const last = Math.max(0, item.pie.turns.length - 1);
    item.slider.max = String(last);
    item.slider.value = String(last);
    item.slider.disabled = item.pie.turns.length < 2;
    this.showTurn(item, last);
  }

  showTurn(item, index) {
    const { turns, series } = item.pie;
    item.date.textContent = turns.length ? turnDate(turns[index]) : "";
    item.chart.setData(series.map((s) => ({ key: s.key, label: s.label, value: s.values[index] })));
  }

  draw(item, data) {
    const keys = keysOf(item.figure.dataset.names, data);
    // 系列の単位がそろわない（通貨の違う為替の額を1つのグラフに並べた）ときは、縦軸に記号を付けない
    const units = new Set(keys.map((k) => data.units[k]));
    const mixed = units.size > 1 && [...units].some((u) => u?.startsWith("money:"));
    const unit = mixed ? "" : keys.length ? data.units[keys[0]] : "";
    const series = keys.map((k) => ({ key: k, label: data.labels[k] || k, values: data.series[k] }));
    // 系列ごとに、値のあるターンだけに詰める（全部の系列で値のないターンは描かない）
    const used = data.turns.map((_, i) => series.some((s) => s.values[i] != null));
    const turns = data.turns.filter((_, i) => used[i]);
    for (const s of series) s.values = s.values.filter((_, i) => used[i]);
    this.chartOf(item, unit).setData(turns, series);
  }
}

/** グラフの figure が読む統計の名前（折れ線は data-names、円グラフは data-pie）。 */
function namesOf(figure) {
  return (figure.dataset.names || figure.dataset.pie || "").split(",").filter(Boolean);
}

/** 名前の並び（末尾の * はその頭の統計すべて）を、API が返した系列の名前に開く。 */
function keysOf(names, data) {
  const keys = [];
  for (const n of (names || "").split(",")) {
    if (!n) continue;
    if (n.endsWith("*")) keys.push(...Object.keys(data.series).filter((k) => k.startsWith(n.slice(0, -1))).sort());
    else if (n in data.series) keys.push(n);
  }
  return keys;
}

/** 画面の #charts（data-url・data-turn・data-currency）のグラフを作って読み込む。 */
export function mountCharts(root = document.getElementById("charts")) {
  if (!root) return null;
  const group = new ChartGroup(root.querySelectorAll("figure[data-names], figure[data-pie]"), {
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
