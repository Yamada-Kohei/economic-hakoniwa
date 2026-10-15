// 貿易の画面: 輸出・輸入と船のグラフと、貿易の命令（貿易圏を作る・入る・抜ける・設定・代表を譲る・
// 引き継ぐ、二国間契約の申し込み・受ける・やめる、フェーズ3-3 の関税・輸出補助・輸入の割り当て・
// 禁輸・援助）。表の中の小さな入力欄（form[data-order]）から、命令の列（右の欄。島の画面と同じ列）の
// 後ろに足す。

import { mountCharts } from "./common.js";
import { mountOrders } from "./orders.js";

// 入力欄の名前ごとの値の形（ほかは文字）
const INT_FIELDS = new Set([
  "bloc", "currency", "approve", "to", "contract", "partner", "price", "turns", "amount", "fixed_price",
]);
const NUMBER_FIELDS = new Set(["quantity", "rate"]);
const FLAG_FIELDS = new Set(["open", "no_tariff", "lift"]); // "true" / "false"

mountCharts();
const orders = await mountOrders();

/** 入力欄の値から命令の中身を作る。空の欄は入れない。作れなければ null と、そのわけ。 */
function payloadOf(kind, form) {
  const payload = {};
  for (const [key, value] of new FormData(form).entries()) {
    if (value === "") continue;
    if (INT_FIELDS.has(key)) payload[key] = Math.round(Number(value));
    else if (NUMBER_FIELDS.has(key)) payload[key] = Number(value);
    else if (FLAG_FIELDS.has(key)) payload[key] = value === "true";
    else if (key !== "currency_of") payload[key] = String(value).trim();
  }
  if (["propose_contract", "send_aid"].includes(kind) && form.elements.currency_of?.value === "partner") {
    payload.currency = payload.partner; // 契約と援助の通貨は、どちらかの国の通貨（省くとこちらの通貨）
  }
  if (kind === "set_embargo" && !payload.lift) {
    delete payload.lift; // 省くと始める
  }
  if (kind === "bloc_settings" && ("fixed_good" in payload) !== ("fixed_price" in payload)) {
    return [null, "固定価格は、資源と値段の両方を入れてください。"];
  }
  if (kind === "propose_contract" && payload.side !== "buy") {
    delete payload.delivery; // 届いた物の行き先は、買う側が決める
  }
  if (kind === "bloc_settings" && Object.keys(payload).length < 2) {
    return [null, "変える項目を選んでください。"];
  }
  if (kind === "create_bloc" && !payload.name) {
    return [null, "名前を入れてください。"];
  }
  return [payload, ""];
}

document.addEventListener("submit", async (e) => {
  const form = e.target.closest("form[data-order]");
  if (!form || !orders) return;
  e.preventDefault();
  if (!form.reportValidity()) return;
  const kind = form.dataset.order;
  const notice = form.closest("section")?.querySelector(".trade-notice");
  const [payload, problem] = payloadOf(kind, form);
  if (!payload) {
    if (notice) notice.textContent = problem;
    return;
  }
  if (form.dataset.confirm && !confirm(form.dataset.confirm)) return;
  await orders.add(kind, payload);
  if (notice) notice.textContent = orders.message;
});
