// node --test tests/js/map-coastal-tiers.test.mjs
// 海沿いの施設の規模段（グラフィック強化 案17・案A）: 向きは海の優先順で先に決め、
// 段絵はその向きの中でだけ探す。優先した向きの段絵が無く別の向きにだけあっても、
// 優先した向きの基本絵が返る（段絵の有無で向きが変わらない）ことを確かめる。
import test from "node:test";
import assert from "node:assert/strict";
import { drawIsland } from "../../server/static/js/map/renderer.js";

const theme = {
  tile_width: 64, tile_height: 32, step_height: 8,
  terrain: {
    plain: { top: "#8a8", left: "#686", right: "#464" },
    sea: { top: "#36c", left: "#25a", right: "#148" },
  },
  fallback: { top: "#888", left: "#666", right: "#444" },
  facilities: {}, facility_fallback: { color: "#999", height: 1 }, facility_unit: 8,
  construction: "#f80", capital_roof: "#c9a227", power_line: "#111", no_development: "#111",
  size_tiers: [2, 5, 8],
};
const defs = {
  terrain: { plain: { height: 0, land: true }, sea: { height: 0 } },
  housing: "housing", settlements: {},
  facilities: { port: { coastal: true, sprite: "port" } },
  theme,
};
const view = { scale: 1, offsetX: 0, offsetY: 0, dpr: 1 };

// 海の置き場: se = 施設の右下 (x+1, y)、sw = 左下 (x, y+1)。施設は (1, 1)。
const BOTH_SEAS = ["plain", "plain", "plain", "plain", "plain", "sea", "plain", "sea", "plain"];
const SW_ONLY = ["plain", "plain", "plain", "plain", "plain", "plain", "plain", "sea", "plain"];

function stub(name) {
  return { img: { name }, w: 64, h: 60, ax: 32, ay: 60 };
}

function spritesOf(...names) {
  return new Map(names.map((n) => [`facility/${n}`, stub(n)]));
}

/** 描いた物を記録するだけの canvas の文脈。 */
function recorder() {
  const calls = [];
  const ctx = new Proxy({ canvas: { width: 640, height: 480 }, calls }, {
    get(target, key) {
      if (key in target) return target[key];
      return (...args) => { calls.push([key, ...args]); };
    },
    set(target, key, value) { target[key] = value; return true; },
  });
  return ctx;
}

/** 施設のマスに描かれた絵の名前（地形の絵はスタブに入れていないので施設だけが残る）。 */
function drawnNames(ctx) {
  return ctx.calls.filter((c) => c[0] === "drawImage").map((c) => c[1].name);
}

function draw(terrain, size, sprites) {
  const ctx = recorder();
  const map = {
    size: 3, terrain, capital: null, capital_buildings: [],
    facilities: [{ x: 1, y: 1, kind: "port", built: true, size }],
  };
  drawIsland(ctx, map, defs, view, { sprites });
  return drawnNames(ctx);
}

test("段絵がある向きでは、規模の段の絵を描く", () => {
  const names = draw(BOTH_SEAS, 5, spritesOf("port", "port@sw", "port@s3", "port@sw@s3"));
  assert.deepEqual(names, ["port@s3"]);
});

test("優先した向きの段絵が無く別の向きにだけあっても、優先した向きの基本絵を描く", () => {
  const names = draw(BOTH_SEAS, 5, spritesOf("port", "port@sw", "port@sw@s3"));
  assert.deepEqual(names, ["port"]);
});

test("段絵がどこにも無ければ、優先した向きの基本絵を描く", () => {
  const names = draw(BOTH_SEAS, 5, spritesOf("port", "port@sw"));
  assert.deepEqual(names, ["port"]);
});

test("規模 1 では段絵を探さず基本絵を描く", () => {
  const names = draw(BOTH_SEAS, 1, spritesOf("port", "port@sw", "port@s2", "port@sw@s2"));
  assert.deepEqual(names, ["port"]);
});

test("海が左下だけなら、その向きの段絵を描く", () => {
  const names = draw(SW_ONLY, 2, spritesOf("port", "port@sw", "port@sw@s2"));
  assert.deepEqual(names, ["port@sw@s2"]);
});
