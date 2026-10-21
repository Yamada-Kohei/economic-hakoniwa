// node --test tests/js/military.test.mjs
// 座標・輸送の条件・命令列の保存を、ブラウザやゲームの数値に依存せず確かめる。
import test from "node:test";
import assert from "node:assert/strict";
import {
  payloadOf, landAt, loadableUnits, transports, armyOptions, transportForms,
  appendOrder, orderText, equipmentText, percent,
  fleetDutyForm, bindOrderForms,
} from "../../server/static/js/military/common.js";
import { MilitaryTile } from "../../server/static/js/island/military.js";
import { mountFleetCommands } from "../../server/static/js/world/military.js";
import { MissileTarget } from "../../server/static/js/island/missiles.js";
import { TilePanel } from "../../server/static/js/island/panel.js";
import { drawIsland, falloutOpacity } from "../../server/static/js/map/renderer.js";

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

test("補給・取り立て・務め・体制・核の指定を API の型で作り、0 と false を保つ", () => {
  assert.deepEqual(payloadOf([["reserve.food", "0"], ["reserve.ammunition", "1.5"], ["reserve.fuel", "2"]]),
    { reserve: { food: 0, ammunition: 1.5, fuel: 2 } });
  assert.deepEqual(payloadOf([["unit", "2"], ["enabled", "false"]]), { unit: 2, enabled: false });
  assert.deepEqual(payloadOf([["fleet", "11"], ["duty", "blockade"]]), { fleet: 11, duty: "blockade" });
  assert.deepEqual(payloadOf([["fleet", "10"], ["good", "food"], ["quantity", "0.5"]]),
    { fleet: 10, good: "food", quantity: 0.5 });
  assert.deepEqual(payloadOf([["base", "3"], ["island", "2"], ["x", "0"], ["y", "0"], ["count", "1"], ["nuclear", "true"]]),
    { base: 3, island: 2, x: 0, y: 0, count: 1, nuclear: true });
  assert.deepEqual(payloadOf([["politics", "democracy"], ["head_of_state", "republic"], ["economy", "market"]]),
    { politics: "democracy", head_of_state: "republic", economy: "market" });
  assert.throws(() => payloadOf([["enabled", "on"]]));
  assert.throws(() => payloadOf([["reserve.other", "1"]]));
  assert.match(fleetDutyForm({ id: 3, duty: "watch" }), /value="watch" selected/);
});

test("核の確認を断ったら保存せず、通常弾は核の指定を省き、核は true を送る", async () => {
  const oldFormData = globalThis.FormData;
  const oldConfirm = globalThis.confirm;
  let handler;
  let confirmations = 0;
  const sent = [];
  const checkbox = { checked: true };
  const button = { disabled: false };
  const notice = {};
  const form = {
    dataset: { order: "fire_missile" }, reportValidity: () => true,
    elements: { namedItem: () => checkbox },
    querySelector: (s) => s === ".order-notice" ? notice : button,
    closest() { return this; },
  };
  globalThis.FormData = class {
    *[Symbol.iterator]() {
      yield* [["base", "3"], ["island", "2"], ["cell", "0,0"], ["count", "1"]];
      if (checkbox.checked) yield ["nuclear", "true"];
    }
  };
  bindOrderForms({ addEventListener: (_, fn) => { handler = fn; } }, async (kind, payload) => {
    sent.push({ kind, payload }); return "保存済み";
  });
  const event = { target: form, preventDefault() {} };
  try {
    globalThis.confirm = () => { confirmations++; return false; };
    await handler(event);
    assert.equal(sent.length, 0);
    assert.equal(confirmations, 1);
    checkbox.checked = false;
    await handler(event);
    assert.equal(confirmations, 1);
    assert.equal("nuclear" in sent[0].payload, false);
    checkbox.checked = true;
    globalThis.confirm = () => { confirmations++; return true; };
    await handler(event);
    assert.equal(sent[1].payload.nuclear, true);
    assert.equal(notice.textContent, "保存済み");
    assert.equal(button.disabled, false);
  } finally {
    globalThis.FormData = oldFormData;
    globalThis.confirm = oldConfirm;
  }
});

test("地下建設の確認と送信に同じ hidden を使い、定義がない施設には付けない", () => {
  const oldFormData = globalThis.FormData;
  let kind = "missile_base";
  const panel = Object.create(TilePanel.prototype);
  panel.tile = { x: 0, y: 1 };
  panel.defs = { facilities: { missile_base: { hide: { turns: 8, materials_factor: 2 } }, ordinary: {} } };
  const form = { dataset: { form: "build" }, reportValidity: () => true };
  panel.el = { querySelector: () => form };
  const sent = [];
  panel.addOrder = (order, payload) => sent.push({ order, payload });
  globalThis.FormData = class {
    get(key) { return ({ facility: kind, hidden: "true", name: "", recipe: "", note: "" })[key]; }
  };
  try {
    assert.match(panel.hideField(kind), /name="hidden"/);
    assert.match(panel.hideField(kind), /hideTurns: 8/);
    assert.match(panel.hideField(kind), /hideMaterials: 2/);
    assert.equal(panel.buildPayload().hidden, true);
    panel.submit(form);
    assert.deepEqual(sent[0].payload, panel.buildPayload());
    kind = "ordinary";
    assert.equal(panel.hideField(kind), "");
    assert.equal("hidden" in panel.buildPayload(), false);
    panel.submit(form);
    assert.equal("hidden" in sent[1].payload, false);
    delete panel.defs.facilities.missile_base.hide;
    assert.match(panel.hideField("missile_base"), /hideUnavailable/);
    assert.doesNotMatch(panel.hideField("missile_base"), /checkbox/);
  } finally { globalThis.FormData = oldFormData; }
});

test("汚染は地形と建物の間、占領は建物の後。地下の印は持ち主だけ", () => {
  const calls = [];
  const stack = [];
  const ctx = new Proxy({
    canvas: { width: 64, height: 64 }, globalAlpha: 1,
    save() { stack.push(this.globalAlpha); }, restore() { this.globalAlpha = stack.pop(); },
    drawImage(img) { calls.push([img, this.globalAlpha]); },
    fillText(label) { calls.push([label, this.globalAlpha]); },
    measureText: () => ({ width: 18 }),
  }, { get: (target, key) => key in target ? target[key] : () => {} });
  const sprite = (id) => ({ img: id, w: 64, h: 32, ax: 32, ay: 32 });
  const sprites = new Map(["terrain/plain", "overlay/contaminated", "construction", "overlay/occupied"].map((id) => [id, sprite(id)]));
  const defs = { theme: { tile_width: 64, tile_height: 32, step_height: 1, terrain: { plain: {} } }, terrain: { plain: { height: 0 } } };
  const map = { size: 1, mine: true, terrain: ["plain"], fallout: [3], occupied: [2], facilities: [{ x: 0, y: 0, hidden: true, built: false }] };
  const view = { scale: 1, dpr: 1, offsetX: 0, offsetY: 0 };
  drawIsland(ctx, map, defs, view, { sprites, undergroundLabel: "地下" });
  assert.deepEqual(calls.map(([id]) => id), ["overlay/contaminated", "construction", "地下", "overlay/occupied"]);
  assert.ok(calls[0][1] < 1);
  assert.equal(calls[1][1], 1);
  assert.equal(falloutOpacity(0), 0);
  assert.ok(falloutOpacity(0.1) < falloutOpacity(3));
  assert.ok(falloutOpacity(3) < falloutOpacity(8));
  assert.ok(falloutOpacity(1000) < 1);
  calls.length = 0;
  map.mine = false;
  drawIsland(ctx, map, defs, view, { sprites, undergroundLabel: "地下" });
  assert.equal(calls.some(([id]) => id === "地下"), false);
  calls.length = 0;
  map.facilities = [];
  map.fallout = [0];
  map.occupied = [0];
  drawIsland(ctx, map, defs, view, { sprites });
  assert.deepEqual(calls.map(([id]) => id), ["terrain/plain"]);
});

test("ミサイルの地図選択は自国の基地だけを使い、確認後に自国の命令列へ保存する", async () => {
  const urls = [];
  const sent = [];
  const base = { facility: 5, kind: "missile_base", x: 1, y: 1, stock: 7, missile_base: 12, warheads: 1, warhead: 2 };
  const oldFetch = globalThis.fetch;
  const oldFormData = globalThis.FormData;
  globalThis.fetch = async (url, init) => {
    urls.push(url);
    if (init?.method === "PUT") sent.push(init.body);
    return { ok: true, json: async () => url === "/own-orders" ? { orders: [{ kind: "stop_unit", payload: { unit: 9 } }], warnings: [] }
      : url === "/own-military" ? { military: { bases: [base] } }
      : { islands: [{ id: 1, mine: true }, { id: 2, name: "相手", mine: false }] } };
  };
  const listeners = {};
  const button = { disabled: true };
  const target = {};
  const stock = {};
  const notice = {};
  const form = {
    dataset: { order: "fire_missile" }, reportValidity: () => true,
    elements: { x: { value: "" }, y: { value: "" }, namedItem: () => ({ checked: false }) },
    closest: (s) => s === "form[data-order]" ? form : null,
    querySelector: (s) => s === "[data-target-label]" ? target : s === ".order-notice" ? notice : button,
  };
  globalThis.FormData = class {
    *[Symbol.iterator]() { yield* [["base", "5"], ["island", "2"], ["x", form.elements.x.value], ["y", form.elements.y.value], ["count", "1"]]; }
  };
  const root = {
    dataset: { military: "/own-military", world: "/world", island: "2", orders: "/own-orders" },
    hidden: true, innerHTML: "",
    addEventListener(type, fn) { (listeners[type] ??= []).push(fn); },
    insertAdjacentHTML(_, html) { this.innerHTML += html; }, scrollIntoView() {},
    querySelector(s) { return s === "[data-base-stock]" ? stock : this.innerHTML.includes('data-order="fire_missile"') ? form : null; },
  };
  try {
    const picker = new MissileTarget(root, {});
    await picker.start(5, true);
    assert.deepEqual(urls, ["/own-military", "/world"]);
    assert.match(root.innerHTML, /name="island" value="2"/);
    assert.match(root.innerHTML, /name="nuclear" value="true"/);
    assert.match(stock.textContent, /7 \/ 12/);
    assert.equal(button.disabled, true);
    picker.select({ x: 0, y: 0 });
    assert.equal(form.elements.x.value, 0);
    assert.equal(form.elements.y.value, 0);
    assert.equal(button.disabled, false);
    assert.match(target.textContent, /\(0, 0\)/);
    assert.equal(sent.length, 0);
    await listeners.submit[0]({ target: form, preventDefault() {} });
    assert.deepEqual(urls.slice(-2), ["/own-orders", "/own-orders"]);
    assert.deepEqual(JSON.parse(sent[0]), { orders: [
      { kind: "stop_unit", payload: { unit: 9 } },
      { kind: "fire_missile", payload: { base: 5, island: 2, x: 0, y: 0, count: 1 } },
    ] });
    picker.select(null);
    assert.equal(button.disabled, true);
    assert.equal(form.elements.x.value, "");
    await picker.start(999, true);
    assert.equal(picker.base, null);
    assert.doesNotMatch(root.innerHTML, /data-order/);
    assert.match(root.innerHTML, /role="alert"/);
  } finally { globalThis.fetch = oldFetch; globalThis.FormData = oldFormData; }
});

test("核を持たない約束の提案は予告期間を条項に付け、空欄なら省く", async () => {
  const oldDocument = globalThis.document;
  const oldFormData = globalThis.FormData;
  globalThis.document = { getElementById: () => null, querySelectorAll: () => [], addEventListener() {} };
  let fields = [["partner", "2"], ["clause", "no_nuclear"], ["notice_turns", "6"]];
  globalThis.FormData = class { entries() { return fields.values(); } };
  try {
    const diplomacy = await import("../../server/static/js/reports/diplomacy.js?military-nuclear-test");
    const form = { dataset: { order: "propose_treaty" } };
    assert.deepEqual(diplomacy.payloadOf(form), { partner: 2, clauses: [{ type: "no_nuclear", notice_turns: 6 }] });
    fields[2] = ["notice_turns", ""];
    assert.deepEqual(diplomacy.payloadOf(form), { partner: 2, clauses: [{ type: "no_nuclear" }] });
  } finally {
    globalThis.document = oldDocument;
    globalThis.FormData = oldFormData;
  }
});

test("持ち主の戦況は外国の地図へ結び、補給・占領・捕虜・基地上限を表示する", async () => {
  const oldDocument = globalThis.document;
  const oldFetch = globalThis.fetch;
  const slots = Object.fromEntries(["units", "fleets", "bases", "reserve", "occupied", "occupying", "prisoners", "enemy"].map((id) => [id, { innerHTML: "" }]));
  const data = { ...military,
    units: [{ ...military.units[0], island: 2, abroad: true, role: "attack", fought: 2,
      stores: { food: 0.5 }, hungry: true, requisition: false, soldiers: 6, soldiers_full: 10 }],
    fleets: [{ ...military.fleets[0], duty: "watch", goods: { fuel: 1.5 } }],
    bases: [{ facility: 8, kind: "missile_base", x: 0, y: 1, stock: 7, missile_base: 12, warheads: 1, warhead: 2 },
      { facility: 9, kind: "air_defense", x: 1, y: 1, stock: 3, air_defense: 12 }],
    reserve: { food: 4.5 }, occupied: [{ x: 0, y: 0, by: 2, turn: 5 }],
    occupying: [{ island: 2, x: 1, y: 2, turn: 6 }], prisoners: { by_island: { 2: 6 } },
    enemy_units: [{ id: 40, owner: 2, island: 1, x: 2, y: 0, army: "<相手>", current: true, turn: 8 }],
  };
  const root = {
    dataset: { defs: "/defs", map: "/islands/1/", islandUrl: "/islands/0/" },
    addEventListener() {}, querySelectorAll: () => [], prepend() { assert.fail("描画に失敗"); },
    querySelector(s) { return slots[s.match(/data-table="([a-z]+)"/)?.[1]]; },
  };
  globalThis.document = { getElementById: (id) => ({ "military-page": root, "military-text": { dataset: labels },
    "military-data": { textContent: JSON.stringify(data) } })[id] || null };
  globalThis.fetch = async () => ({ ok: true, json: async () => ({}) });
  try {
    await import("../../server/static/js/reports/military.js?war-owner-test");
    for (const slot of Object.values(slots)) assert.match(slot.innerHTML, /class="table-scroll"/);
    assert.match(slots.units.innerHTML, /\/islands\/2\/\?x=0&amp;y=2/);
    assert.match(slots.units.innerHTML, /food: 0.5/);
    assert.match(slots.units.innerHTML, /attack/);
    assert.match(slots.fleets.innerHTML, /watch/);
    assert.match(slots.bases.innerHTML, /7 \/ 12/);
    assert.match(slots.bases.innerHTML, /1 \/ 2/);
    assert.match(slots.bases.innerHTML, /3 \/ 12/);
    assert.match(slots.reserve.innerHTML, /4.5/);
    assert.match(slots.occupied.innerHTML, /\/islands\/1\/\?x=0&amp;y=0/);
    assert.match(slots.occupying.innerHTML, /\/islands\/2\/\?x=1&amp;y=2/);
    assert.match(slots.prisoners.innerHTML, /<td>2<\/td><td>6<\/td>/);
    assert.match(slots.enemy.innerHTML, /&lt;相手&gt;/);
  } finally { globalThis.document = oldDocument; globalThis.fetch = oldFetch; }
});

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

test("乗船中の兵を島に出さず、外洋の輸送船団には上陸だけを出す", () => {
  assert.deepEqual(landAt(military, { x: 0, y: 2 }).map((u) => u.id), [1]);
  assert.deepEqual(loadableUnits(military).map((u) => u.id), [1, 2]);
  assert.deepEqual(transports(military).map((f) => f.id), [10, 11]);
  const html = transportForms(military, military.fleets[0]);
  assert.match(html, /data-order="load_unit"/);
  assert.match(html, /data-order="unload_unit"/);
  assert.match(html, /value="0,2"/);
  assert.match(html, /value="4,3"/);
  assert.doesNotMatch(html, /value="1,1"/);
  const abroad = transportForms(military, military.fleets[1]);
  assert.match(abroad, /data-order="unload_unit"/);
  assert.match(abroad, /name="island"/);
  assert.doesNotMatch(abroad, /data-order="load_unit"|data-order="load_supplies"/);
  assert.match(html, /data-order="load_supplies"/);
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
