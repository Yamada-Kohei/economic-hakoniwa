// 外交の画面（フェーズ4-1）: 持ち主には外交の命令（宣戦布告・条約の提案・署名・断り・取り下げ・
// 破棄）。画面の入力欄（form[data-order]）から、命令の列（右の欄。島の画面と同じ列）の
// 後ろに足す。

import { mountOrders } from "./orders.js";

// 入力の初期化と submit の登録を先に済ませ、読み込み中にも通常のフォーム送信をさせない。
let orders = null;
const ordersReady = mountOrders().then((queue) => { orders = queue; });

const INT_FIELDS = new Set([
  "target",
  "partner",
  "treaty",
  "notice_turns",
  "withdraw_turns",
  "payer",
  "currency",
  "amount",
  "due_turns",
  "bloc_id",
  "contract_seller",
  "contract_price",
  "contract_currency",
  "contract_turns",
  "peg_pegger",
  "aid_payer",
  "aid_amount",
  "aid_currency",
  "aid_period",
  "aid_count",
  "refugee_host",
]);
const FLOAT_FIELDS = new Set(["contract_quantity", "peg_rate"]);
const TERMINABLE = new Set([
  "ceasefire",
  "non_aggression",
  "alliance",
  "bloc",
  "peg",
  "swap",
  "free_movement",
  "refugees",
]);

/** 入力欄の値から命令の中身を作る。空の欄は入れない。 */
export function payloadOf(form) {
  const payload = {};
  const clauseTypes = [];
  const rest = {};
  for (const [key, value] of new FormData(form).entries()) {
    if (key === "clause") clauseTypes.push(String(value));
    else if (value === "") continue;
    else if (INT_FIELDS.has(key)) rest[key] = Math.round(Number(value));
    else if (FLOAT_FIELDS.has(key)) rest[key] = Number(value);
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
        if (rest.currency) clause.reparations.currency = rest.currency;
      }
    }
    if (type === "bloc") {
      if (rest.bloc_id) clause.bloc = rest.bloc_id;
      if (rest.bloc_name) clause.name = rest.bloc_name;
    }
    if (type === "contract") {
      const fields = {
        seller: rest.contract_seller,
        good: rest.contract_good,
        quantity: rest.contract_quantity,
        price: rest.contract_price,
        currency: rest.contract_currency,
        turns: rest.contract_turns,
      };
      for (const [key, value] of Object.entries(fields)) if (value !== undefined) clause[key] = value;
    }
    if (type === "peg") {
      if (rest.peg_pegger) clause.pegger = rest.peg_pegger;
      if (rest.peg_rate) clause.rate = rest.peg_rate;
    }
    if (type === "aid") {
      const fields = {
        payer: rest.aid_payer,
        amount: rest.aid_amount,
        currency: rest.aid_currency,
        period: rest.aid_period,
        count: rest.aid_count,
      };
      for (const [key, value] of Object.entries(fields)) if (value !== undefined) clause[key] = value;
    }
    if (type === "refugees" && rest.refugee_host) clause.host = rest.refugee_host;
    return clause;
  });
  return payload;
}

/** 選んだ条項に使う条件だけを有効にし、賠償の不足や条項の矛盾を入力時に知らせる。 */
export function syncProposal(form) {
  const clauses = [...form.querySelectorAll('[name="clause"]')];
  const chosen = clauses.filter((c) => c.checked).map((c) => c.value);
  const peace = chosen.includes("peace");
  const ceasefire = chosen.includes("ceasefire");
  const notice = form.querySelector("[data-notice-fields]");
  const peaceFields = form.querySelector("[data-peace-fields]");
  const reparationFields = form.querySelector("[data-reparation-fields]");
  notice.disabled = !chosen.some((type) => TERMINABLE.has(type));
  peaceFields.disabled = !peace;
  const reparations = peace && form.querySelector("[data-reparations-toggle]").checked;
  reparationFields.disabled = !reparations;
  for (const name of ["payer", "amount", "due_turns"]) form.elements[name].required = reparations;

  const partner = form.elements.partner;
  for (const option of form.querySelectorAll("[data-partner-option]")) {
    // 相手を変えても、その相手を払う国・通貨とする選択を引き継ぐ。
    const selected = option.selected;
    option.value = partner.value;
    option.textContent = partner.value ? partner.selectedOptions[0].textContent : "相手の国（先に相手を選択）";
    option.disabled = !partner.value;
    if (selected) option.selected = true;
  }

  let error = "";
  if (!chosen.length) error = "条項を1つ以上選んでください。";
  else if (peace && ceasefire) error = "停戦と講和は一緒に選べません。";
  else if (ceasefire && chosen.some((type) => TERMINABLE.has(type) && type !== "ceasefire")) {
    error = "停戦に不可侵・同盟は添えられません。講和の取り決めに添えてください。";
  }
  clauses[0]?.setCustomValidity(error);
}

for (const form of document.querySelectorAll('form[data-order="propose_treaty"]')) {
  syncProposal(form);
  form.addEventListener("change", () => syncProposal(form));
}

document.addEventListener("submit", async (e) => {
  const form = e.target.closest("form[data-order]");
  if (!form) return;
  e.preventDefault();
  if (form.dataset.order === "propose_treaty") syncProposal(form);
  if (!form.reportValidity()) return;
  if (form.dataset.confirm && !confirm(form.dataset.confirm)) return;
  const notice = form.closest(".diplomacy-card, details, section")?.querySelector(".order-notice");
  const button = e.submitter;
  if (button) button.disabled = true;
  try {
    await ordersReady;
    if (!orders) {
      if (notice) notice.textContent = "命令の欄を読み込めませんでした。画面を開き直してください。";
      return;
    }
    await orders.add(form.dataset.order, payloadOf(form));
    if (notice) notice.textContent = orders.message;
  } catch {
    if (notice) notice.textContent = "命令を追加できませんでした。通信を確かめてください。";
  } finally {
    if (button) button.disabled = false;
  }
});
