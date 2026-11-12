// 施設の画面: 国全体の施設の統計のグラフと、自国の施設ごとの帳簿の推移（施設の帳簿の API）。

import { turnDate } from "../calendar.js";
import { getJson } from "../island/api.js";
import { attachRange, escapeHtml, formatValue, makeChart, mountCharts, mountStaticPies, rangeQuery } from "./common.js";
import { mountOrders } from "./orders.js";

mountCharts();
mountStaticPies();

const stateFilter = document.getElementById("facility-state");
if (stateFilter) {
  const rows = [...document.querySelectorAll("tr[data-facility-state]")];
  for (const state of new Set(rows.map((row) => row.dataset.facilityState))) stateFilter.add(new Option(state, state));
  const filter = () => {
    for (const row of rows) row.hidden = !!stateFilter.value && row.dataset.facilityState !== stateFilter.value;
    document.getElementById("facility-count").textContent = `${rows.filter((row) => !row.hidden).length} / ${rows.length} 件（損益の合計は全施設）`;
  };
  stateFilter.addEventListener("change", filter);
  filter();
}

/**
 * 施設のページ（UI-73）の打てる手の箱の入力欄から、命令の中身を作る。施設のマスは箱の data-x・data-y。
 * 数の欄（type=number・data-int）は数にし、data-int は整数に丸める。空欄は送らない（data-keep-empty は送る）。
 */
function facilityPayload(form, box) {
  const payload = { x: Number(box.dataset.x), y: Number(box.dataset.y) };
  for (const el of form.elements) {
    if (!el.name || ((el.type === "radio" || el.type === "checkbox") && !el.checked)) continue;
    const value = el.value.trim();
    if (value === "" && !("keepEmpty" in el.dataset)) continue;
    if ("int" in el.dataset) payload[el.name] = Math.round(Number(value));
    else if (el.type === "number") payload[el.name] = Number(value);
    else payload[el.name] = value;
  }
  return payload;
}

// 企業の表の国有化・民営化（回7）と、施設のページの打てる手（UI-73）: 押すと命令の列に足す
const firmOrders = await mountOrders();
document.addEventListener("submit", async (e) => {
  const form = e.target.closest("form.firm-action, form.facility-action");
  if (!form || !firmOrders) return;
  e.preventDefault();
  const box = form.closest("#actions");
  if (form.dataset.confirm && !confirm(form.dataset.confirm)) return;
  const payload = box ? facilityPayload(form, box) : { firm: Number(form.dataset.firm) };
  await firmOrders.add(form.dataset.order, payload);
  const notice = document.querySelector(".firm-notice, .facility-notice");
  if (notice) notice.textContent = firmOrders.message;
});

// 帳簿の推移のグラフに描く収支の項目（帳簿の額は支出も正の数）
const FLOW_ITEMS = ["sales", "purchases", "wages", "electricity", "freight", "depreciation", "profit"];
const TABLE_TURNS = 12; // 表に出すターン数

const detail = document.getElementById("book-detail");

if (detail) {
  const turn = Number(detail.dataset.turn || 0);
  const currency = detail.dataset.currency || "";
  const slot = (name) => detail.querySelector(`[data-slot=${name}]`);
  const state = { number: null, range: 120, request: 0 };
  const money = (v) => escapeHtml(formatValue(v, "money", currency, true));

  function freshChart(name, label) {
    const figure = detail.querySelector(`[data-chart=${name}]`);
    figure.querySelector(".line-chart")?.remove();
    const body = document.createElement("div");
    body.className = "line-chart";
    figure.append(body);
    return makeChart(body, "money", currency, label);
  }

  async function load() {
    const request = ++state.request;
    const url = `${detail.dataset.url.replace(/\/0\/book$/, `/${state.number}/book`)}?${rangeQuery(state.range, turn)}`;
    let data;
    try {
      data = await getJson(url);
    } catch {
      if (request === state.request) slot("title").textContent = "帳簿を読み込めませんでした";
      return;
    }
    if (request !== state.request) return;
    const kind = data.kind_name && data.kind_name !== data.name ? `（${data.kind_name}）` : "";
    slot("title").textContent = `${data.name}${kind}の帳簿`;
    // 表と同じく、支出は負の数で描く（損益と同じ向きにそろえる）
    const flows = FLOW_ITEMS.filter((k) => k in data.items).map((k) => ({
      key: k,
      label: data.labels[k],
      values: data.items[k].map((v) => data.signs[k] * v),
    }));
    freshChart("flows", "収支").setData(data.turns, flows);
    // ずっと 0 の線は描かない（国庫直轄の施設は預金を持たないなど）。どれもなければグラフごと隠す
    const balance = [
      { key: "deposit", label: "預金", values: data.deposit },
      { key: "debt", label: "借入", values: data.debt },
      { key: "inventory", label: "在庫の値打ち", values: data.inventory },
    ].filter((s) => s.values.some((v) => v));
    detail.querySelector("[data-chart=balance]").hidden = data.turns.length > 0 && !balance.length;
    freshChart("balance", "預金と借入").setData(data.turns, balance);
    slot("table").innerHTML = tableHtml(data);
  }

  /** 直近のターンの帳簿の表（新しいターンから）。支出は負の数で出す。 */
  function tableHtml(data) {
    if (!data.turns.length) return `<p class="muted">この期間の帳簿はありません。</p>`;
    const keys = Object.keys(data.labels);
    const head = keys.map((k) => `<th>${escapeHtml(data.labels[k])}</th>`).join("");
    // 預金と借入は、期間の中で 0 でないときだけ列を出す（国庫直轄の施設は預金を持たない）
    const extra = [
      ["預金", data.deposit],
      ["借入", data.debt],
    ].filter(([, values]) => values.some((v) => v));
    const rows = [];
    for (let i = data.turns.length - 1; i >= 0 && rows.length < TABLE_TURNS; i--) {
      const cells = keys.map((k) => `<td>${money(data.signs[k] * data.items[k][i])}</td>`).join("");
      const more = extra.map(([, values]) => `<td>${money(values[i])}</td>`).join("");
      rows.push(`<tr><td>${escapeHtml(turnDate(data.turns[i]))}</td>${cells}${more}</tr>`);
    }
    const moreHead = extra.map(([label]) => `<th>${label}</th>`).join("");
    return `<table class="goods book-table">
      <thead><tr><th>時期</th>${head}${moreHead}</tr></thead>
      <tbody>${rows.join("")}</tbody></table>`;
  }

  document.addEventListener("click", (e) => {
    const button = e.target.closest("button[data-book]");
    if (!button) return;
    state.number = Number(button.dataset.book);
    detail.hidden = false;
    load();
    detail.scrollIntoView({ behavior: "smooth", block: "start" });
  });

  // 施設のページ（UI-72）は、その施設の帳簿をはじめから出す
  if (detail.dataset.number) {
    state.number = Number(detail.dataset.number);
    load();
  }

  attachRange(detail, (range) => {
    state.range = range;
    if (state.number != null) load();
  });
}
