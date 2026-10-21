// node --test tests/js/military.test.mjs
// 座標・輸送の条件・命令列の保存を、ブラウザやゲームの数値に依存せず確かめる。
import test from "node:test";
import assert from "node:assert/strict";
import {
  payloadOf, landAt, loadableUnits, transports, armyOptions, transportForms,
  appendOrder, orderText, equipmentText, percent,
} from "../../server/static/js/military/common.js";
import { MilitaryTile } from "../../server/static/js/island/military.js";
import { mountFleetCommands } from "../../server/static/js/world/military.js";

const labels = new Proxy({}, { get: (_, key) => String(key) });
globalThis.document = { getElementById: () => ({ dataset: labels }), querySelector: () => null, cookie: "" };

const military = {
  bases: [
    { naval_base: true, x: 0, y: 2 },
    { naval_base: true, x: 4, y: 3 },
    { barracks: 20, x: 1, y: 1 },
  ],
  units: [
    { id: 1, army: "land_a", name: "陸 A", x: 0, y: 2, aboard: null, status: "ready" },
    { id: 2, army: "land_a", x: 4, y: 3, aboard: null, status: "forming" },
    { id: 3, army: "land_a", x: 0, y: 2, aboard: 10 },
    { id: 4, army: "land_a", x: 0, y: 2, aboard: 11 },
    { id: 5, army: "land_a", x: 1, y: 1, aboard: null },
  ],
  fleets: [
    { id: 10, docked: true, capacity: 12, cargo: [3] },
    { id: 11, docked: false, capacity: 12, cargo: [4] },
    { id: 12, docked: true, capacity: 0, cargo: [] },
  ],
};

test("座標の 0 と賃金の小数を保ち、選択欄の補助項目は送らない", () => {
  assert.deepEqual(payloadOf([["army", "new_kind"], ["cell", "0,3"], ["ships", "2"]]),
    { army: "new_kind", x: 0, y: 3, ships: 2 });
  assert.deepEqual(payloadOf([["annual", "0"], ["personnel_share", ""], ["volunteer_wage", "105.5"]]),
    { annual: 0, volunteer_wage: 105.5 });
  assert.deepEqual(payloadOf([["fleet", "10"], ["unit", "3"], ["cell", "4,3"]]),
    { fleet: 10, unit: 3, x: 4, y: 3 });
  assert.throws(() => payloadOf([["cell", "-1,2"]]));
  assert.throws(() => payloadOf([["cell", "0,no"]]));
  assert.throws(() => payloadOf([["count", "no"]]));
});

test("軍港と母港の輸送船団だけを選び、乗船中の兵を島に出さない", () => {
  assert.deepEqual(landAt(military, { x: 0, y: 2 }).map((u) => u.id), [1]);
  assert.deepEqual(loadableUnits(military).map((u) => u.id), [1, 2]);
  assert.deepEqual(transports(military).map((f) => f.id), [10]);
  const html = transportForms(military, military.fleets[0]);
  assert.match(html, /data-order="load_unit"/);
  assert.match(html, /data-order="unload_unit"/);
  assert.match(html, /value="0,2"/);
  assert.match(html, /value="4,3"/);
  assert.doesNotMatch(html, /value="1,1"/);
  assert.equal(transportForms(military, military.fleets[1]), "");
  assert.equal(transportForms(military, military.fleets[2]), "");
});

test("編成候補は定義の陸・海で分け、表示名のない種類はキーを使う", () => {
  const defs = { army: { new_land: { arm: "land" }, new_ship: { arm: "sea", name: "<新艦>" } } };
  assert.equal(armyOptions(defs, "land"), '<option value="new_land">new_land</option>');
  assert.equal(armyOptions(defs, "sea"), '<option value="new_ship">&lt;新艦&gt;</option>');
  assert.equal(armyOptions({}, "land"), "");
});

test("命令と装備と割合を元の値から表示する", () => {
  assert.equal(orderText({ type: "sail", x: 0, y: 4, route: [1, 2] }), "sail → (0, 4)");
  assert.equal(orderText({ type: "dig", left: 0 }), "dig（remaining: 0）");
  assert.equal(orderText(null), "none");
  assert.equal(equipmentText({ equipment: {}, equipment_full: { arms: 12 } }, {}), "arms: 0 / 12");
  assert.equal(percent(0.615), "62%");
});

test("島の移動先選択はそろった未乗船部隊だけ。マスを押すと一度だけ命令に追加", () => {
  const added = [];
  const panel = {
    el: { addEventListener() {} }, tools: { addEventListener() {} },
    marked: new Set(), onMarkedChange() {}, render() {},
    endMarking() { this.military.picking = null; this.marked.clear(); },
    addOrder(kind, payload) { added.push({ kind, payload }); },
  };
  panel.military = new MilitaryTile(panel);
  panel.military.data = military;
  panel.military.start(2);
  assert.equal(panel.military.picking, null);
  panel.military.start(3);
  assert.equal(panel.military.picking, null);
  panel.military.start(1);
  assert.equal(panel.military.picking, 1);
  assert.deepEqual([...panel.marked], ["0,2"]);
  panel.military.choose({ x: 0, y: 5 });
  panel.military.choose({ x: 1, y: 5 });
  assert.deepEqual(added, [{ kind: "move_unit", payload: { unit: 1, x: 0, y: 5 } }]);
  assert.equal(panel.military.picking, null);
  assert.equal(panel.marked.size, 0);
});

test("世界マップの追加は最新の列を読み、既存命令をそのまま残して警告も返す", async () => {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    const data = init.method === "PUT"
      ? { warnings: [{ index: 2, text: "燃料が足りません" }] }
      : { orders: [
        { kind: "stop_unit", payload: { unit: 9 }, text: "旧画面になかった命令" },
        { kind: "recruit", payload: { count: 7 }, cost: 1 },
      ] };
    return { ok: true, json: async () => data };
  };
  const message = await appendOrder("/orders", "sail_fleet", { fleet: 10, x: 0, y: 4 });
  assert.equal(calls.length, 2);
  assert.deepEqual(JSON.parse(calls[1].init.body), { orders: [
    { kind: "stop_unit", payload: { unit: 9 } },
    { kind: "recruit", payload: { count: 7 } },
    { kind: "sail_fleet", payload: { fleet: 10, x: 0, y: 4 } },
  ] });
  assert.match(message, /燃料が足りません/);
});

test("読み直しが失敗したら PUT しない。拒否された命令を成功と表示しない", async () => {
  let count = 0;
  globalThis.fetch = async () => { count++; return { ok: false, status: 503 }; };
  await assert.rejects(appendOrder("/orders", "sail_fleet", { fleet: 10, x: 0, y: 0 }));
  assert.equal(count, 1);
  globalThis.fetch = async (url, init) => ({
    ok: init.method !== "PUT", status: init.method === "PUT" ? 400 : 200,
    json: async () => init.method === "PUT" ? { errors: [{ text: "艦隊がありません" }] } : { orders: [] },
  });
  assert.equal(await appendOrder("/orders", "sail_fleet", { fleet: 10, x: 0, y: 0 }), "艦隊がありません");
});

test("世界マップで選ぶ間だけ島への遷移を止め、海の座標を送り、Esc で取り消せる", async () => {
  const element = () => ({
    dataset: {}, listeners: {}, attrs: new Map(), hidden: false,
    addEventListener(type, handler) { this.listeners[type] = handler; },
    setAttribute(key, value) { this.attrs.set(key, value); },
    removeAttribute(key) { this.attrs.delete(key); },
    focus() {},
  });
  const select = { ...element(), value: "10" };
  const sail = element();
  const detail = element();
  const transport = element();
  const controls = { ...element(), querySelector: (s) => ({
    "[data-fleet]": select, "[data-sail]": sail,
    "[data-fleet-details]": detail, "[data-transport]": transport,
  })[s] };
  const picking = element();
  const cancel = element();
  const notice = element();
  const label = element();
  const root = { ...element(), dataset: { military: "/military", orders: "/orders" },
    querySelector: (s) => ({
      "[data-fleet-controls]": controls, "[data-fleet-notice]": notice,
      "[data-fleet-picking]": picking, "[data-fleet-cancel]": cancel,
      "[data-fleet-picking-label]": label,
    })[s],
  };
  const sea = { ...element(), dataset: { x: "0", y: "3" }, closest() { return this; } };
  const island = { ...element(), dataset: { x: "1", y: "2" }, closest() { return this; } };
  const classes = new Set();
  const grid = {
    ...element(), classList: { add: (v) => classes.add(v), remove: (v) => classes.delete(v) },
    querySelectorAll: () => [sea], querySelector: () => island,
    contains: (node) => node === sea || node === island,
  };
  const keys = {};
  const oldDocument = globalThis.document;
  globalThis.document = {
    getElementById: (id) => id === "world-military" ? root : { dataset: labels },
    addEventListener: (key, handler) => { keys[key] = handler; },
    querySelector: () => null, cookie: "",
  };
  const sent = [];
  globalThis.fetch = async (url, init) => {
    const data = url === "/military" ? { military }
      : init.method === "PUT" ? { warnings: [] } : { orders: [] };
    if (init.method === "PUT") sent.push(JSON.parse(init.body));
    return { ok: true, json: async () => data };
  };
  try {
    const fleet = { ...military.fleets[0], army: "sea_a", ships: 1, x: 1, y: 2,
      soldiers: 3, soldiers_full: 3, status: "ready", steps_left: 0, order: null };
    await mountFleetCommands(grid, Promise.resolve({ army: {} }), Promise.resolve({ mine: [fleet] }));
    assert.equal(sail.disabled, false);
    const event = (target) => ({ target, prevented: false,
      preventDefault() { this.prevented = true; }, stopPropagation() {} });
    const normal = event(island);
    await grid.listeners.click(normal);
    assert.equal(normal.prevented, false);
    sail.listeners.click();
    assert.equal(classes.has("fleet-picking"), true);
    assert.equal(sea.attrs.get("role"), "button");
    keys.keydown({ key: "Escape" });
    assert.equal(classes.has("fleet-picking"), false);
    assert.equal(picking.hidden, true);
    assert.equal(sea.attrs.get("aria-hidden"), "true");
    assert.equal(sent.length, 0);
    sail.listeners.click();
    const destination = event(sea);
    await grid.listeners.click(destination);
    assert.equal(destination.prevented, true);
    assert.deepEqual(sent, [{ orders: [{ kind: "sail_fleet", payload: { fleet: 10, x: 0, y: 3 } }] }]);
    assert.equal(classes.has("fleet-picking"), false);
    sail.listeners.click();
    cancel.listeners.click();
    const restored = event(island);
    await grid.listeners.click(restored);
    assert.equal(restored.prevented, false);
  } finally {
    globalThis.document = oldDocument;
  }
});

test("他国の軍画面は渡された艦隊だけを表示し、軍の非公開 API を読みに行かない", async () => {
  const oldDocument = globalThis.document;
  const seen = { innerHTML: "" };
  const publicData = { soldiers: 5, budget: { personnel: 1, procurement: 2 }, fleets: [
    { id: 21, army: "sea_b", name: "見えた艦", x: 3, y: 0, turn: 8, current: false },
  ] };
  const root = {
    dataset: { defs: "/defs", map: "/island/2/" },
    addEventListener() {}, querySelectorAll: () => [], prepend() { assert.fail("描画が失敗した"); },
    querySelector: (s) => s === '[data-table="seen"]' ? seen : null,
  };
  globalThis.document = { getElementById: (id) => ({
    "military-page": root, "military-text": { dataset: labels },
    "military-data": { textContent: JSON.stringify(publicData) },
  })[id] || null };
  const urls = [];
  globalThis.fetch = async (url) => {
    urls.push(url);
    return { ok: true, json: async () => ({ army: {} }) };
  };
  try {
    await import("../../server/static/js/reports/military.js?public-test");
    assert.deepEqual(urls, ["/defs"]);
    assert.match(seen.innerHTML, /見えた艦/);
    assert.match(seen.innerHTML, /previous/);
    assert.match(seen.innerHTML, /\(3, 0\)/);
  } finally {
    globalThis.document = oldDocument;
  }
});
