// 市場の画面: 選んだ資源の値段と量の推移（市場の API）と、直近のターンの約定・買えずに止まった施設
// （島の持ち主だけ）。表の資源名を押すと、画面を読み直さずに資源を切り替える。

import { turnDate } from "../calendar.js";
import { getJson } from "../island/api.js";
import { attachRange, escapeHtml, formatValue, makeChart, rangeQuery } from "./common.js";

const detail = document.getElementById("market-detail");

if (detail) {
  const turn = Number(detail.dataset.turn || 0);
  const currency = detail.dataset.currency || "";
  const mine = detail.dataset.mine === "1";
  const slot = (name) => detail.querySelector(`[data-slot=${name}]`);
  const state = { good: detail.dataset.good, range: 120, request: 0 };

  /** 資源ごとに単位が違うので、切り替えるたびにグラフを作り直す。 */
  function freshChart(name, unit, label) {
    const figure = detail.querySelector(`[data-chart=${name}]`);
    figure.querySelector(".line-chart")?.remove();
    const body = document.createElement("div");
    body.className = "line-chart";
    figure.append(body);
    return makeChart(body, unit, currency, label);
  }

  async function load() {
    const request = ++state.request;
    const url = `${detail.dataset.url.replace(/[^/]+$/, encodeURIComponent(state.good))}?${rangeQuery(state.range, turn)}`;
    let data;
    try {
      data = await getJson(url);
    } catch {
      if (request === state.request) slot("title").textContent = "読み込めませんでした";
      return;
    }
    if (request !== state.request) return; // 待つ間に別の資源が選ばれた
    const unit = data.unit ? ` ${data.unit}` : "";
    slot("title").textContent = `${data.name}の推移`;
    freshChart("price", "money", `${data.name}の値段`).setData(data.turns, [
      { key: "price", label: `値段（1${data.unit}）`, values: data.price },
    ]);
    freshChart("volume", unit, `${data.name}の量`).setData(data.turns, [
      { key: "volume", label: "売買量", values: data.volume },
      { key: "demand", label: "買い注文", values: data.demand },
      { key: "supply", label: "売り注文", values: data.supply },
    ]);
    const l = data.latest;
    slot("latest").innerHTML = l
      ? `<p>${escapeHtml(turnDate(l.turn))}: 値段 ${escapeHtml(formatValue(l.price, "money", currency, true))}・売買量 ${escapeHtml(formatValue(l.volume, unit, "", true))}（買い注文 ${escapeHtml(formatValue(l.demand, unit, "", true))}・売り注文 ${escapeHtml(formatValue(l.supply, unit, "", true))}）</p>`
      : `<p class="muted">この資源の市場の記録はありません。</p>`;
    if (mine) {
      slot("trades").innerHTML = tradesHtml(data, unit);
      slot("short").innerHTML = shortHtml(data.short || []);
    }
  }

  function tradesHtml(data, unit) {
    if (!data.trades?.length) return `<p class="muted">約定はありません。</p>`;
    const rows = data.trades
      .map((t) => `<tr><td>${escapeHtml(t.buyer_name)}</td><td>${escapeHtml(t.seller_name)}</td>
        <td>${escapeHtml(formatValue(t.quantity, unit, "", true))}</td>
        <td>${escapeHtml(formatValue(t.amount, "money", currency, true))}</td>
        <td>${escapeHtml(formatValue(t.price, "money", currency, true))}</td></tr>`)
      .join("");
    return `<table class="goods trades-table">
      <thead><tr><th>買い手</th><th>売り手</th><th>量</th><th>代金</th><th>1${escapeHtml(data.unit)}あたり</th></tr></thead>
      <tbody>${rows}</tbody></table>`;
  }

  function shortHtml(list) {
    if (!list.length) return `<p class="muted">ありません。</p>`;
    const base = detail.dataset.islandUrl;
    return `<ul class="plain-list">${list
      .map((f) => `<li><a href="${base}?x=${f.x}&amp;y=${f.y}">${escapeHtml(f.name)}</a> ・ ${escapeHtml(f.status_text)}</li>`)
      .join("")}</ul>`;
  }

  // 表の資源名で切り替える（URL の ?good= も変え、読み直しても同じ資源を出す）
  document.addEventListener("click", (e) => {
    const link = e.target.closest("a[data-good-link]");
    if (!link) return;
    e.preventDefault();
    state.good = link.dataset.goodLink;
    for (const row of document.querySelectorAll("tr[data-good]")) {
      row.classList.toggle("chosen", row.dataset.good === state.good);
    }
    history.replaceState(null, "", `?good=${encodeURIComponent(state.good)}`);
    load();
    detail.scrollIntoView({ behavior: "smooth", block: "start" });
  });

  attachRange(detail, (range) => {
    state.range = range;
    load();
  });
  load();
}
