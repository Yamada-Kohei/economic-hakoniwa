// 外交の画面（フェーズ4-1）: 持ち主には外交の命令（宣戦布告・条約の提案・署名・断り・取り下げ・
// 破棄）。表の中の小さな入力欄（form[data-order]）から、命令の列（右の欄。島の画面と同じ列）の
// 後ろに足す。

import { mountOrders } from "./orders.js";

const orders = await mountOrders();

const INT_FIELDS = new Set([
  "target",
  "partner",
  "treaty",
  "notice_turns",
  "withdraw_turns",
  "payer",
  "amount",
  "due_turns",
]);
const TERMINABLE = new Set(["ceasefire", "non_aggression", "alliance"]);

/** 入力欄の値から命令の中身を作る。空の欄は入れない。 */
function payloadOf(form) {
  const payload = {};
  const clauseTypes = [];
  const rest = {};
  for (const [key, value] of new FormData(form).entries()) {
    if (key === "clause") clauseTypes.push(String(value));
    else if (value === "") continue;
    else if (INT_FIELDS.has(key)) rest[key] = Math.round(Number(value));
    else rest[key] = String(value).trim();
  }
  if (form.dataset.order !== "propose_treaty") return rest;
  for (const key of ["partner", "title", "text"]) if (key in rest) payload[key] = rest[key];
  payload.clauses = clauseTypes.map((type) => {
    const clause = { type };
    if (TERMINABLE.has(type) && rest.notice_turns) clause.notice_turns = rest.notice_turns;
    if (type === "peace") {
      if ("withdraw_turns" in rest) clause.withdraw_turns = rest.withdraw_turns;
      if (rest.keep_prisoners === "false") clause.prisoners = false;
      if (rest.payer && rest.amount && rest.due_turns) {
        clause.reparations = {
          payer: rest.payer,
          amount: rest.amount,
          due_turns: rest.due_turns,
        };
      }
    }
    return clause;
  });
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
