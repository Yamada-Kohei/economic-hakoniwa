// node --test tests/js/*.test.mjs
// 反乱軍の閲覧範囲と描画、施設の定義から表示する効果と既存の命令を確かめる。
import test from "node:test";
import assert from "node:assert/strict";
import { drawIsland } from "../../server/static/js/map/renderer.js";
import { TilePanel } from "../../server/static/js/island/panel.js";
import { recipeLabel } from "../../server/static/js/island/recipes.js";

const recipeText = {
  labor: "人手 {count} 割", education: "育ち +{count}%", method: "生産方式",
  change: "生産方式の切り替え", current: "現在", next: "次のターンに「{name}」に切り替えます。",
  electronicsHint: "毎ターン電子機器を使い、手元にないまま続くと基本の作り方に戻ります。",
  ownershipHint: "国営は命令に追加、民営の工場は企業が採算を見て替え、画面からは替えられません。",
  transition: "切り替えたターンは稼働率が半分になります。", add: "命令に追加", none: "なし",
};
const militaryText = { rebelHeld: "反乱軍に占領されています。", occupied: "占領した国の番号" };
globalThis.document = { getElementById: (id) => ({ dataset: id === "recipe-text" ? recipeText : militaryText }) };

function panelWithRecipes() {
  const panel = Object.create(TilePanel.prototype);
  panel.tile = { x: 0, y: 1 };
  panel.resources = new Set();
  panel.defs = { facilities: {
    factory: { recipes: {
      basic: { name: "基本", inputs: {}, outputs: {} },
      automated: { name: "自動化<工場>", labor: 0.65, workers: 65, inputs: { electronics: 1 }, outputs: {} },
    } },
    school: { recipes: {
      basic: { name: "基本", inputs: {}, outputs: {} },
      digital: { name: "電子教材", education_scale: 1.2, inputs: { electronics: 1 }, outputs: {} },
    } },
  } };
  return panel;
}

test("作り方の効果は定義の値を使い、省略された人手は基本のままにする", () => {
  assert.equal(recipeLabel("basic", { name: "基本" }), "基本");
  assert.equal(recipeLabel("automated", { name: "自動化", labor: 0.6 }), "自動化・人手 6 割");
  assert.equal(recipeLabel("automated", { name: "自動化", labor: 0.65 }), "自動化・人手 6.5 割");
  assert.equal(recipeLabel("digital", { name: "電子教材", education_scale: 1.2 }), "電子教材・育ち +20%");
  assert.equal(recipeLabel("digital", { name: "電子教材", education_scale: 1.35 }), "電子教材・育ち +35%");
  assert.equal(recipeLabel("basic", undefined, "基本"), "基本");
});

test("建設と国営の切り替え候補に効果と電子機器の注意を出し、名前をエスケープする", () => {
  const panel = panelWithRecipes();
  const build = panel.recipeSelect("factory");
  assert.match(build, /自動化&lt;工場&gt;・人手 6.5 割/);
  assert.match(build, /基本の作り方に戻ります/);
  assert.ok(build.indexOf("毎ターン") < build.indexOf("<select"));
  const factory = { kind: "factory", recipe_name: "基本", recipe_choices: [
    { recipe: "automated", name: "自動化<工場>", inputs: [{ name: "電子機器", amount: 1 }], outputs: [] },
  ] };
  const change = panel.actionsHtml({ facility: factory });
  assert.match(change, /value="automated">自動化&lt;工場&gt;・人手 6.5 割（電子機器 1 → なし）/);
  assert.match(change, /民営の工場は企業が採算を見て/);
  assert.ok(change.indexOf("毎ターン") < change.indexOf('name="recipe"'));
  assert.match(panel.recipeSelect("school"), /電子教材・育ち \+20%/);
  const school = panel.actionsHtml({ facility: { kind: "school", recipe_choices: [
    { recipe: "digital", name: "電子教材", inputs: [], outputs: [] },
  ] } });
  assert.match(school, /value="digital">電子教材・育ち \+20%/);
});

test("民営には作り方の切り替えを出さず、国営は従来の change_recipe を送る", () => {
  const panel = panelWithRecipes();
  const privateFacility = { kind: "factory", owner: "private", recipe: "automated", recipe_name: "自動化",
    name: "工場", built: true, stock: [] };
  assert.match(panel.facilityHtml(privateFacility), /画面からは替えられません/);
  assert.doesNotMatch(panel.actionsHtml({ facility: privateFacility }), /data-form="recipe"/);
  const oldFormData = globalThis.FormData;
  const sent = [];
  panel.addOrder = (kind, payload) => sent.push({ kind, payload });
  globalThis.FormData = class { get(key) { return key === "recipe" ? "automated" : ""; } };
  try {
    panel.submit({ dataset: { form: "recipe" }, reportValidity: () => true });
    assert.deepEqual(sent, [{ kind: "change_recipe", payload: { x: 0, y: 1, recipe: "automated" } }]);
  } finally { globalThis.FormData = oldFormData; }
});

test("反乱軍を部隊と同じ基準点で描き、兵の数を添え、他国には描かない", () => {
  const images = [];
  const texts = [];
  const fills = [];
  const ctx = new Proxy({
    canvas: { width: 128, height: 64 },
    drawImage: (...args) => images.push(args),
    fillText: (text) => texts.push(text),
    fill() { fills.push(this.fillStyle); },
    measureText: () => ({ width: 18 }),
  }, { get: (target, key) => key in target ? target[key] : () => {} });
  const sprite = (img) => ({ img, w: 28, h: 32, ax: 14, ay: 28 });
  const sprites = new Map(["unit/infantry", "unit/rebels", "overlay/occupied"].map((id) => [id, sprite(id)]));
  const defs = { theme: { tile_width: 64, tile_height: 32, step_height: 1, terrain: { plain: {} } },
    terrain: { plain: { height: 0 } } };
  const map = { size: 1, mine: true, terrain: ["plain"], occupied: [-1],
    civil_war: { groups: [{ x: 0, y: 0, soldiers: 42 }] } };
  const view = { scale: 1, dpr: 1, offsetX: 0, offsetY: 0 };
  drawIsland(ctx, map, defs, view, { sprites });
  const rebel = images.find(([id]) => id === "unit/rebels");
  assert.ok(rebel);
  assert.deepEqual(texts, ["42"]);
  assert.ok(fills.includes("rgba(190, 35, 55, 0.35)"));
  assert.equal(images.some(([id]) => id === "overlay/occupied"), false);
  images.length = 0;
  map.civil_war.groups = [];
  map.units = [{ x: 0, y: 0, army: "infantry" }];
  drawIsland(ctx, map, defs, view, { sprites });
  assert.deepEqual(images[0].slice(1), rebel.slice(1));
  images.length = texts.length = 0;
  map.mine = false;
  map.units = [];
  map.civil_war.groups = [{ x: 0, y: 0, soldiers: 42 }];
  map.occupied = [2];
  drawIsland(ctx, map, defs, view, { sprites });
  assert.deepEqual(images.map(([id]) => id), ["overlay/occupied"]);
  assert.deepEqual(texts, []);
  images.length = 0;
  map.occupied = [-1];
  drawIsland(ctx, map, defs, view, { sprites });
  assert.equal(images.length, 0);
  assert.deepEqual(texts, []);
});

test("マスの欄は反乱軍の占領と外国の占領を区別する", () => {
  const panel = Object.create(TilePanel.prototype);
  panel.tile = { x: 0, y: 0 };
  panel.info = {};
  panel.el = {};
  panel.tileHtml = () => "";
  panel.map = { size: 1, occupied: [-1] };
  panel.render();
  assert.match(panel.el.innerHTML, /反乱軍に占領されています/);
  assert.doesNotMatch(panel.el.innerHTML, /-1/);
  panel.map.occupied = [5];
  panel.render();
  assert.match(panel.el.innerHTML, /占領した国の番号: 5/);
});
