// 金融の画面: 金利・中央銀行の国債・融資のグラフと、金融の命令（金融政策・中央銀行の独立・銀行の
// 政策）。持ち主には、銀行ごとの緊急の貸し出しと破綻処理の入力も出す（命令の列に足す）。

import { mountCharts } from "./common.js";
import { mountOrders } from "./orders.js";

mountCharts();
const orders = await mountOrders();
const notice = document.querySelector(".bank-notice");

document.addEventListener("submit", async (e) => {
  const form = e.target.closest("form[data-order]");
  if (!form || !orders) return;
  e.preventDefault();
  if (!form.reportValidity()) return;
  const data = new FormData(form);
  const where = { x: Number(form.dataset.x), y: Number(form.dataset.y) };
  const kind = form.dataset.order;
  const payload =
    kind === "emergency_loan"
      ? { ...where, amount: Math.round(Number(data.get("amount"))) }
      : { ...where, method: data.get("method") };
  await orders.add(kind, payload);
  if (notice) notice.textContent = orders.message;
});
