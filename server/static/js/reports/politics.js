// 政治と計画の画面（フェーズ3-4）: 推移のグラフと、持ち主には政治の命令（報道と集会・労働の動員・
// 議会の解散・国家元首の名前・憲法改正・経済体制の変更）と計画の命令（公定価格・公定価格の改定と
// 決まり・重点産業・値段の上限と下限）。表の下の小さな入力欄（form[data-order]）から、命令の列（右の欄。島の画面と同じ列）の
// 後ろに足す。

import { mountCharts } from "./common.js";
import { mountOrders } from "./orders.js";

// 入力欄の名前ごとの値の形（ほかは文字）
const INT_FIELDS = new Set(["price", "band"]);
const BOOL_FIELDS = new Set(["on"]);

mountCharts();
const orders = await mountOrders();

/**
 * 入力欄の値から命令の中身を作る。空の欄（「変えない」）は入れない。data-list の欄は並びにし、
 * 何も選ばなかったときは空の並びにする（重点産業をなくす）。
 */
function payloadOf(form) {
  const payload = {};
  const list = form.dataset.list;
  if (list) payload[list] = [];
  for (const [key, value] of new FormData(form).entries()) {
    if (key === list) payload[key].push(String(value));
    else if (value === "") continue;
    else if (INT_FIELDS.has(key)) payload[key] = Math.round(Number(value));
    else if (BOOL_FIELDS.has(key)) payload[key] = value === "true";
    else payload[key] = String(value).trim();
  }
  return payload;
}

document.addEventListener("submit", async (e) => {
  const form = e.target.closest("form[data-order]");
  if (!form || !orders) return;
  e.preventDefault();
  if (!form.reportValidity()) return;
  if (form.dataset.confirm && !confirm(form.dataset.confirm)) return;
  const notice = form.closest("section")?.querySelector(".order-notice");
  await orders.add(form.dataset.order, payloadOf(form));
  if (notice) notice.textContent = orders.message;
});
