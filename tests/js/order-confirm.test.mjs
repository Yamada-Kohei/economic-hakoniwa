// node --test tests/js/order-confirm.test.mjs
// 命令の打ち間違いの確かめ（UI-75）: 命令の列（OrderQueue）の足し方が、サーバーの確かめの文で聞き、
// はいなら confirm を付けて送り直し、いいえなら並べない。
import test from "node:test";
import assert from "node:assert/strict";
import { OrderQueue } from "../../server/static/js/island/orders.js";

globalThis.document = { getElementById: () => null, querySelector: () => null, cookie: "" };

function server(asks) {
  const state = { orders: [], puts: [] };
  globalThis.fetch = async (url, init) => {
    const view = () => ({ admin_power: 3, orders: state.orders, recent: [], warnings: [] });
    if (init?.method !== "PUT") return { ok: true, status: 200, json: async () => view() };
    const body = JSON.parse(init.body);
    state.puts.push(body);
    if (asks.length && !body.confirm) {
      return { ok: false, status: 428, json: async () => ({ error: "確かめ", confirm: asks }) };
    }
    state.orders = body.orders.map((o) => ({ ...o, cost: 1, text: o.kind }));
    return { ok: true, status: 200, json: async () => view() };
  };
  return state;
}

function queue() {
  const el = { dataset: { url: "/orders" }, addEventListener() {}, innerHTML: "", querySelector: () => null };
  const defs = { orders: { demolish: { name: "取り壊し", admin_cost: 1 } }, goods: {} };
  return new OrderQueue(el, defs, null, { forms: [] });
}

test("確かめの文を見せ、いいえなら並べず、はいなら confirm を付けて同じ列を送り直す", async () => {
  const asks = [{ index: 0, kind: "demolish", text: "取り壊し (1, 2)", notes: ["(1, 2) の農場を取り壊します。戻せません。"] }];
  const state = server(asks);
  const oldConfirm = globalThis.confirm;
  const shown = [];
  try {
    const q = queue();
    await q.saving;
    globalThis.confirm = (text) => { shown.push(text); return false; };
    await q.add("demolish", { x: 1, y: 2 });
    assert.equal(state.puts.length, 1);
    assert.deepEqual(state.orders, []);
    assert.equal(q.message, "命令を追加しませんでした。");
    assert.deepEqual(q.items, []);  // 「保存中」の行を残さない
    assert.match(shown[0], /・取り壊し \(1, 2\)\n　\(1, 2\) の農場を取り壊します。戻せません。/);

    globalThis.confirm = () => true;
    await q.add("demolish", { x: 1, y: 2 });
    assert.deepEqual(state.puts.slice(1).map((b) => b.confirm), [undefined, true]);
    assert.deepEqual(state.orders.map((o) => o.kind), ["demolish"]);
    assert.equal(q.message, "「取り壊し」を命令に追加しました。");
  } finally {
    globalThis.confirm = oldConfirm;
  }
});

test("確かめの要らない命令は、聞かずに 1 回で並べる", async () => {
  const state = server([]);
  const oldConfirm = globalThis.confirm;
  try {
    globalThis.confirm = () => { throw new Error("聞かない"); };
    const q = queue();
    await q.saving;
    await q.add("rename_island", { name: "島" });
    assert.equal(state.puts.length, 1);
    assert.equal("confirm" in state.puts[0], false);
  } finally {
    globalThis.confirm = oldConfirm;
  }
});
