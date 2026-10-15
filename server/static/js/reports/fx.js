// 為替と国際収支の画面: レート・貿易収支・外貨の持ち高・為替の売買量・国際収支のグラフと、持ち主には
// 為替と資本取引の命令（介入・固定相場・資本取引の制限・外国の国債の売り買い。フェーズ3-3）。表の下の
// 小さな入力欄（form[data-order]）から、命令の列（右の欄。島の画面と同じ列）の後ろに足す。

import { mountCharts } from "./common.js";
import { mountOrders } from "./orders.js";

// 入力欄の名前ごとの値の形（ほかは文字）
const INT_FIELDS = new Set(["currency", "issuer", "amount"]);
const NUMBER_FIELDS = new Set(["rate"]);

mountCharts();
const orders = await mountOrders();

/** 入力欄の値から命令の中身を作る。「売る」を選んだ額はマイナスにする（0 はやめる）。 */
function payloadOf(form) {
  const payload = {};
  for (const [key, value] of new FormData(form).entries()) {
    if (value === "" || key === "side") continue;
    if (INT_FIELDS.has(key)) payload[key] = Math.round(Number(value));
    else if (NUMBER_FIELDS.has(key)) payload[key] = Number(value);
    else payload[key] = String(value).trim();
  }
  if ("amount" in payload && form.elements.side?.value === "sell") {
    payload.amount = -payload.amount || 0;
  }
  return payload;
}

document.addEventListener("submit", async (e) => {
  const form = e.target.closest("form[data-order]");
  if (!form || !orders) return;
  e.preventDefault();
  if (!form.reportValidity()) return;
  const notice = form.closest("section")?.querySelector(".fx-notice");
  if (form.dataset.confirm && !confirm(form.dataset.confirm)) return;
  await orders.add(form.dataset.order, payloadOf(form));
  if (notice) notice.textContent = orders.message;
});
