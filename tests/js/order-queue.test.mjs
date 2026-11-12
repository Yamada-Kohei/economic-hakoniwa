// node --test tests/js/order-queue.test.mjs
// 命令の列（OrderQueue）: 保存は 1 件ずつ（UI-74）。足すのは POST .../orders/items、消すのは
// DELETE .../orders/items/<番号>、並べ替えは POST .../orders/sequence。確かめ（UI-75）はサーバーの
// 文で聞き、はいなら confirm を付けて送り直す。右の欄は入力欄と列の数だけ、命令のページは列の全部。
import test from "node:test";
import assert from "node:assert/strict";
import { OrderQueue, queueSummary } from "../../server/static/js/island/orders.js";

const bars = [{ textContent: "" }];
globalThis.document = {
  getElementById: () => null, querySelector: () => null, cookie: "",
  querySelectorAll: (s) => (s === "[data-orders-bar-text]" ? bars : []),
};

/** 1 件ずつの API をまねるサーバー。asks があれば、confirm のない足しを 428 で断る。 */
function server(asks = []) {
  const state = { orders: [], calls: [], next: 1 };
  const view = () => ({ admin_power: 2, orders: state.orders, recent: [{ turn: 3, status: "failed", text: "古い命令", reason: "わけ" }], warnings: [] });
  const ok = (data) => ({ ok: true, status: 200, json: async () => data });
  globalThis.fetch = async (url, init = {}) => {
    const body = init.body ? JSON.parse(init.body) : null;
    state.calls.push({ url, method: init.method || "GET", body });
    if (!init.method) return ok(view());
    if (init.method === "POST" && url.endsWith("/items")) {
      if (asks.length && !body.confirm) {
        return { ok: false, status: 428, json: async () => ({ error: "確かめ", confirm: asks }) };
      }
      const id = state.next++;
      state.orders.push({ id, kind: body.kind, payload: body.payload, cost: 1, text: body.kind });
      return ok({ ...view(), added: id, warning: "" });
    }
    if (init.method === "DELETE") {
      const id = Number(url.split("/").pop());
      if (!state.orders.some((o) => o.id === id)) {
        return { ok: false, status: 404, json: async () => ({ ...view(), error: "その命令はもうありません" }) };
      }
      state.orders = state.orders.filter((o) => o.id !== id);
      return ok(view());
    }
    if (url.endsWith("/sequence")) {
      state.orders = body.ids.map((id) => state.orders.find((o) => o.id === id));
      return ok(view());
    }
    throw new Error(`知らない呼び出し ${init.method} ${url}`);
  };
  return state;
}

function queue(options = { forms: [] }) {
  const el = { dataset: { url: "/api/islands/1/orders" }, addEventListener() {}, innerHTML: "", querySelector: () => null };
  const defs = { orders: { demolish: { name: "取り壊し", admin_cost: 1 }, rename_island: { name: "名前", admin_cost: 0 } }, goods: {} };
  return new OrderQueue(el, defs, null, options);
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

test("足すのは 1 件ずつの API。今の列を読み直して丸ごと送らないので、ほかの画面の命令を消さない", async () => {
  const state = server();
  const q = queue();
  await settle();
  state.orders.push({ id: 99, kind: "other_tab", payload: {}, cost: 1, text: "別の画面で足した" });
  await q.add("rename_island", { name: "島" });
  const writes = state.calls.filter((c) => c.method !== "GET");
  assert.deepEqual(writes, [{ url: "/api/islands/1/orders/items", method: "POST", body: { kind: "rename_island", payload: { name: "島" } } }]);
  assert.deepEqual(state.orders.map((o) => o.kind), ["other_tab", "rename_island"]);
  assert.equal(q.message, "「名前」を命令に追加しました。");
  // 右の欄は列を出さず、数の 1 行と命令のページへのリンクだけ。帯の数も合わせる
  assert.doesNotMatch(q.el.innerHTML, /order-list/);
  assert.match(q.el.innerHTML, /次のターンの命令 2 件（行政力 2／2）/);
  assert.match(q.el.innerHTML, /href="\/islands\/1\/orders\/"/);
  assert.equal(bars[0].textContent, "次のターンの命令 2 件（行政力 2／2）");
});

test("確かめの文を見せ、いいえなら並べず、はいなら confirm を付けて送り直す", async () => {
  const asks = [{ index: 0, kind: "demolish", text: "取り壊し (1, 2)", notes: ["(1, 2) の農場を取り壊します。戻せません。"] }];
  const state = server(asks);
  const oldConfirm = globalThis.confirm;
  const shown = [];
  try {
    const q = queue();
    await settle();
    globalThis.confirm = (text) => { shown.push(text); return false; };
    await q.add("demolish", { x: 1, y: 2 });
    assert.deepEqual(state.orders, []);
    assert.equal(q.message, "命令を追加しませんでした。");
    assert.match(shown[0], /・取り壊し \(1, 2\)\n　\(1, 2\) の農場を取り壊します。戻せません。/);
    globalThis.confirm = () => true;
    await q.add("demolish", { x: 1, y: 2 });
    const posts = state.calls.filter((c) => c.method === "POST").map((c) => c.body.confirm);
    assert.deepEqual(posts, [undefined, undefined, true]);
    assert.deepEqual(state.orders.map((o) => o.kind), ["demolish"]);
  } finally {
    globalThis.confirm = oldConfirm;
  }
});

test("命令のページは列の全部と結果を出し、消す・並べ替えるも 1 件ずつ送る", async () => {
  const state = server();
  state.orders = [1, 2, 3].map((id) => ({ id, kind: `k${id}`, payload: {}, cost: 1, text: `命令${id}` }));
  state.next = 4;
  const q = queue({ page: true, heading: "命令の列" });
  await settle();
  await q.saving;
  assert.equal(state.calls[0].url, "/api/islands/1/orders?check=1");
  assert.match(q.el.innerHTML, /命令3/);
  assert.match(q.el.innerHTML, /以下は行政力が不足するため/);
  assert.match(q.el.innerHTML, /古い命令: わけ/);
  await q.move(2, 1);
  assert.deepEqual(state.calls.at(-1).body, { ids: [1, 3, 2] });
  await q.remove(0);
  assert.equal(state.calls.at(-1).url, "/api/islands/1/orders/items/1");
  assert.deepEqual(state.orders.map((o) => o.id), [3, 2]);
  // ほかの画面で先に消されていたら、答えの今の列に合わせ、そのわけを出す
  state.orders = [state.orders[1]];
  await q.remove(0);
  assert.equal(q.message, "その命令はもうありません");
  assert.deepEqual(q.items.map((o) => o.id), [2]);
});

test("帯の文は、先頭の命令が行政力を超えてもそのターンに実行される数え方", () => {
  assert.equal(queueSummary([{ cost: 3 }, { cost: 1 }], 2), "次のターンの命令 2 件（行政力 4／2）・1 件は次のターン以降に回ります");
  assert.equal(queueSummary([], 5), "次のターンの命令 0 件（行政力 0／5）");
});
