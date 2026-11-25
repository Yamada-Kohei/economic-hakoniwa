// node --test tests/js/orders-bond-form.test.mjs
// 国債の命令の欄（set_bond_policy）: 自動の発行の期間の割合の決め方が、画面で分かること（国債 D）。
import test from "node:test";
import assert from "node:assert/strict";
import { OrderQueue } from "../../server/static/js/island/orders.js";

globalThis.document = {
  getElementById: () => null, querySelector: () => null, cookie: "", querySelectorAll: () => [],
};

function bondForm() {
  const q = Object.create(OrderQueue.prototype);
  q.defs = { goods: {} };
  q.policy = {
    has_economy: true,
    cb_independent: false,
    policy_rate: 2,
    cb_bond_trade: 0,
    cb_bond_trade_text: "買わない",
    cb_unlock_turn: null,
    cb_lock_turns: 12,
    bond_unsold: "cut",
    bond_unsold_choices: { underwrite: "中央銀行が引き受ける", cut: "支出を減らす" },
    bond_terms: {
      bond_1y: { name: "1年国債", turns: 12, share: 100 },
      bond_5y: { name: "5年国債", turns: 60, share: 0 },
      bond_10y: { name: "10年国債", turns: 120, share: 0 },
    },
    bonds: 0,
    default_turn: null,
    bank_entry: "",
    bank_entry_default: "closed",
    bank_entry_choices: { closed: "参入させない" },
    planned: false,
    deposit_insurance: -1,
    deposit_insurance_text: "無制限",
    store_application: null,
    large_store: "",
    large_store_choices: {},
    subsidies: [],
    subsidy_kinds: {},
  };
  return q.financeForms().set_bond_policy;
}

test("国債の期間の割合の欄に、既定（1年だけ）と決め方が書いてある", () => {
  const form = bondForm();
  assert.match(form, /何も決めていなければ、自動の発行は1年国債だけです/);
  assert.match(form, /ここに期間ごとの割合を入れてください/);
  // 期間の長さ（ターン）が欄に出る
  assert.match(form, /1年国債（12ターン）/);
  assert.match(form, /5年国債（60ターン）/);
  assert.match(form, /10年国債（120ターン）/);
});
