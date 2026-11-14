// node --test tests/js/map-overlay-bars.test.mjs
// 重ね表示の棒（UI-79）: 重ね表示ごとの高さの物差し、奥から手前への描く順、棒と塗り分けの切り替えを覚えること。
import test from "node:test";
import assert from "node:assert/strict";
import {
  BARS, FILL, OVERLAY_MODE_KEY, barPixels, barsBackToFront, drawsBars, overlayLegend, readOverlayMode,
  solidColor, writeOverlayMode,
} from "../../server/static/js/map/overlays/bars.js";
import { overlayList } from "../../server/static/js/map/overlays/index.js";
import { drawIsland } from "../../server/static/js/map/renderer.js";

const defs = {
  infrastructure: { road: { name: "道路" }, grid: { name: "送電網", role: "power", grid: 0.5 } },
  terrain: { plain: { height: 1 }, sea: { height: 0 } },
  theme: { tile_width: 64, tile_height: 32, step_height: 8, terrain: {}, fallback: { top: "#888", left: "#666", right: "#444" } },
};
const byId = Object.fromEntries(overlayList(defs).map((o) => [o.id, o]));

function close(actual, expected) {
  assert.ok(Math.abs(actual - expected) < 1e-9, `${actual} ≠ ${expected}`);
}

test("人口の棒は平方根で、この島でいちばん多いマスが最も高い", () => {
  const map = { population: [0, 2500, 10000, 40000] };
  const h = (i) => byId.population.height(map, i);
  assert.equal(h(0), null);
  close(h(3), 1);
  close(h(2), 0.5); // 4 分の 1 の人口で半分の高さ
  close(h(1), 0.25);
  assert.match(byId.population.scale, /平方根/);
});

test("電力の棒は足りない分だけで、足りているマスと人のいないマスには立てない", () => {
  const map = {
    population: [100, 100, 100, 0, 100, 100, 100],
    power: [1000, 600, 0, 0, null, 996, 994],
    power_line: [0, 0, 0, 1, 0, 0, 0],
  };
  const h = (i) => byId.power.height(map, i);
  assert.equal(h(0), null);
  close(h(1), 0.4);
  close(h(2), 1);
  assert.equal(h(3), null); // 送電線だけのマス
  assert.equal(h(4), null); // 見えない（伏せた）マス
  assert.equal(h(5), null); // 窓に 100% と出るマス
  close(h(6), 0.01);
  assert.match(byId.power.scale, /足りない分/);
});

test("ほかの重ね表示も物差しを持ち、凡例に書く。量のない区域と工事中は塗り分けのまま", () => {
  const map = {
    size: 2, happiness: [50, null], pollution: [5, 30], freight: [2, 8], freight_max: 10,
    infra: { road: [25, 0] }, zone: ["industrial", null],
  };
  close(byId.happiness.height(map, 0), 0.5);
  assert.equal(byId.happiness.height(map, 1), null);
  close(byId.pollution.height(map, 0), 0.5);
  close(byId.pollution.height(map, 1), 1); // 10 以上は最も高い
  close(byId["infra:road"].height(map, 0), 0.25);
  assert.equal(byId["infra:road"].height(map, 1), null);
  close(byId.freight.height(map, 1), 1); // この島でいちばん高い率
  close(byId.freight.height(map, 0), 0.25);
  for (const o of Object.values(byId)) {
    if (o.height) assert.ok(o.scale, `${o.id} の物差しの文がない`);
  }
  assert.equal(byId.zone.height, undefined);
  assert.equal(byId.works.height, undefined);
  assert.equal(drawsBars(byId.zone, BARS), false);
  assert.equal(drawsBars(byId.population, BARS), true);
  assert.equal(drawsBars(byId.population, FILL), false);
});

test("凡例: 棒のときは高さの物差しを添え、塗り分けのときは添えない", () => {
  assert.match(overlayLegend(byId.population, BARS), /平方根/);
  assert.equal(overlayLegend(byId.population, FILL), "");
  assert.match(overlayLegend(byId.freight, BARS), /運賃の率です。.*棒の高さは運賃の率/);
  assert.match(overlayLegend(byId.zone, BARS), /塗り分け/);
  assert.equal(overlayLegend(null, BARS), "");
});

test("棒は奥から手前へ（x + y の小さい順に）並べ、値のないマスは飛ばす", () => {
  const n = 3;
  const map = { size: n, population: [9, 4, 1, 0, 9, 9, 9, 9, 9] };
  const bars = barsBackToFront(map, byId.population, defs);
  const sums = bars.map((b) => b.x + b.y);
  assert.deepEqual(sums, [...sums].sort((a, b) => a - b));
  assert.equal(bars.some((b) => b.i === 3), false);
  // (0, 1) は人がいないので飛ばし、x + y = 2 の並びへ進む
  assert.deepEqual(bars.slice(0, 3).map((b) => [b.x, b.y]), [[0, 0], [1, 0], [0, 2]]);
  for (const b of bars) assert.equal(b.i, b.y * n + b.x);
});

test("高さのピクセル: 0 と値なしは立てず、小さな値も見え、1 で頭打ち", () => {
  const theme = defs.theme;
  assert.equal(barPixels(0, theme), 0);
  assert.equal(barPixels(null, theme), 0);
  assert.equal(barPixels(0.001, theme), 2);
  assert.equal(barPixels(1, theme), barPixels(5, theme));
  assert.equal(barPixels(0.5, theme) * 2, barPixels(1, theme));
  assert.equal(solidColor("hsla(280, 85%, 50%, 0.4)"), "hsl(280, 85%, 50%)");
  assert.equal(solidColor("#aabbcc"), "#aabbcc");
});

test("表し方は既定が棒で、塗り分けを選ぶと覚え、覚えられない窓では棒に戻る", () => {
  const saved = new Map();
  const storage = { getItem: (k) => saved.get(k) ?? null, setItem: (k, v) => saved.set(k, v) };
  assert.equal(readOverlayMode(storage), BARS);
  writeOverlayMode(storage, FILL);
  assert.equal(saved.get(OVERLAY_MODE_KEY), FILL);
  assert.equal(readOverlayMode(storage), FILL);
  writeOverlayMode(storage, BARS);
  assert.equal(readOverlayMode(storage), BARS);
  saved.set(OVERLAY_MODE_KEY, "なにか");
  assert.equal(readOverlayMode(storage), BARS);
  const broken = { getItem() { throw new Error("denied"); }, setItem() { throw new Error("denied"); } };
  assert.equal(readOverlayMode(broken), BARS);
  assert.doesNotThrow(() => writeOverlayMode(broken, FILL));
  assert.equal(readOverlayMode(null), BARS);
});

/** 描いた物を記録するだけの canvas の文脈。 */
function recorder() {
  const log = [];
  const ctx = new Proxy({ canvas: { width: 100, height: 100 }, log }, {
    get(target, key) {
      if (key in target) return target[key];
      return () => {};
    },
    set(target, key, value) {
      if (key === "fillStyle") log.push(value);
      target[key] = value;
      return true;
    },
  });
  return ctx;
}

test("棒のときはマスを塗らずに棒を描き、塗り分けのときは今までどおり塗る", () => {
  const map = { size: 2, terrain: ["plain", "plain", "plain", "plain"], population: [0, 400, 100, 0], facilities: [] };
  const view = { scale: 1, offsetX: 0, offsetY: 0, dpr: 1 };
  const overlay = byId.population;
  const purple = (s) => typeof s === "string" && s.startsWith("hsl") && s.includes("280");
  const fillCtx = recorder();
  drawIsland(fillCtx, map, defs, view, { overlay, overlayMode: FILL });
  assert.ok(fillCtx.log.filter(purple).every((s) => s.startsWith("hsla")));
  assert.equal(fillCtx.log.filter(purple).length, 2);
  const barCtx = recorder();
  drawIsland(barCtx, map, defs, view, { overlay, overlayMode: BARS });
  const colors = barCtx.log.filter(purple);
  // 棒 1 本に側面と上面の色を使い、透明度は外した色（透け具合は描く側で決める）
  assert.ok(colors.length >= 4);
  assert.ok(colors.every((s) => s.startsWith("hsl(")));
});
