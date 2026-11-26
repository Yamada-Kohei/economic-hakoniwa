// node --test tests/js/map-capital-flag.test.mjs
// 首都マスの静止旗（グラフィック強化 案23の静止版）: 旗の大きさの物差しと、
// 首都の建物の上にだけ旗が描かれること、旗の読み込みを確かめる。
import test from "node:test";
import assert from "node:assert/strict";
import { capitalFlagSize, drawIsland } from "../../server/static/js/map/renderer.js";
import { loadFlag } from "../../server/static/js/map/sprites.js";

const theme = {
  tile_width: 64, tile_height: 32, step_height: 8,
  terrain: { plain: { top: "#8a8", left: "#686", right: "#464" } },
  fallback: { top: "#888", left: "#666", right: "#444" },
  facilities: {}, facility_fallback: { color: "#999", height: 1 }, facility_unit: 8,
  construction: "#f80", capital_roof: "#c9a227", power_line: "#111", no_development: "#111",
};
const defs = {
  terrain: { plain: { height: 0 } },
  housing: "housing", settlements: {},
  facilities: { government: { category: "admin", sprite: "government_office" } },
  theme,
};
const view = { scale: 1, offsetX: 0, offsetY: 0, dpr: 1 };
const FLAG = { name: "flag-image" };
const flag = { img: FLAG, w: 90, h: 60 }; // 仮の旗（SVG 90x60）と同じ縦横比
const BLDG = { name: "capital-image" };
const capitalSprite = { img: BLDG, w: 64, h: 73, ax: 32, ay: 73 };

function mapWith(facilities) {
  return {
    size: 2, terrain: ["plain", "plain", "plain", "plain"],
    capital: [0, 0], capital_buildings: [], facilities,
  };
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

function flagDraws(ctx) {
  return ctx.calls.filter((c) => c[0] === "drawImage" && c[1] === FLAG);
}

function close(actual, expected) {
  assert.ok(Math.abs(actual - expected) < 1e-9, `${actual} ≠ ${expected}`);
}

test("旗の大きさ: 幅はマスの 8 分の 1 で縦横比どおり、背の高い画像はマスの高さの 4 分の 1 までに収める", () => {
  const W = theme.tile_width / 8, H = theme.tile_height / 4;
  const a = capitalFlagSize({ w: 90, h: 60 }, theme);
  close(a.w, W);
  close(a.h, W * 60 / 90);
  const b = capitalFlagSize({ w: 30, h: 120 }, theme);
  close(b.h, H);
  close(b.w, H * 30 / 120);
  const c = capitalFlagSize({ w: 60, h: 60 }, theme);
  close(c.w, Math.min(W, H));
  close(c.h, Math.min(W, H));
});

test("首都の建物の絵のあとに、その上に旗を描く", () => {
  const ctx = recorder();
  const sprites = new Map([["capital/government_office", capitalSprite]]);
  const map = mapWith([{ x: 0, y: 0, kind: "government", built: true }]);
  drawIsland(ctx, map, defs, view, { sprites, flag });
  const draws = flagDraws(ctx);
  assert.equal(draws.length, 1);
  const [, , x, y, w, h] = draws[0];
  close(x, 0); // 首都のマスの真ん中
  close(w, theme.tile_width / 8);
  close(h, (theme.tile_width / 8) * 60 / 90);
  // 建物の絵（高さ 73）のてっぺんより上に旗が出る
  const bldgAt = ctx.calls.findIndex((c) => c[0] === "drawImage" && c[1] === BLDG);
  assert.ok(bldgAt >= 0);
  assert.ok(ctx.calls.indexOf(draws[0]) > bldgAt);
  assert.ok(y + h < -41 + 3, `旗の下端 ${y + h} が建物のてっぺん付近にない`);
});

test("建物の絵がないときは箱の屋根の上に旗を描く", () => {
  const ctx = recorder();
  const map = mapWith([{ x: 0, y: 0, kind: "government", built: true }]);
  drawIsland(ctx, map, defs, view, { sprites: new Map(), flag });
  assert.equal(flagDraws(ctx).length, 1);
});

test("首都のマスに建物がないときは地面に旗を立てる", () => {
  const ctx = recorder();
  drawIsland(ctx, mapWith([]), defs, view, { sprites: new Map(), flag });
  assert.equal(flagDraws(ctx).length, 1);
});

test("旗の画像がなければ描かず、工事中の首都にも立てない", () => {
  const noFlag = recorder();
  const sprites = new Map([["capital/government_office", capitalSprite]]);
  drawIsland(noFlag, mapWith([{ x: 0, y: 0, kind: "government", built: true }]), defs, view, { sprites });
  assert.equal(flagDraws(noFlag).length, 0);
  const building = recorder();
  drawIsland(building, mapWith([{ x: 0, y: 0, kind: "government", built: false }]), defs, view, { sprites: new Map(), flag });
  assert.equal(flagDraws(building).length, 0);
});

test("首都でない建物には旗を立てない", () => {
  const ctx = recorder();
  const map = mapWith([{ x: 1, y: 1, kind: "government", built: true }]);
  drawIsland(ctx, map, defs, view, { sprites: new Map(), flag });
  // (1, 1) の建物には立てず、建物のない首都 (0, 0) の地面にだけ立てる
  const draws = flagDraws(ctx);
  assert.equal(draws.length, 1);
  close(draws[0][2], 0);
});

let failLoad = false;
globalThis.Image = class {
  constructor() { this.naturalWidth = 90; this.naturalHeight = 60; }
  set src(value) {
    queueMicrotask(() => {
      if (failLoad) this.onerror && this.onerror();
      else this.onload && this.onload();
    });
  }
};

test("旗の読み込み: 読めたら大きさ付きで渡し、読めなければ null", async () => {
  failLoad = false;
  const seen = [];
  const loaded = await loadFlag("/islands/1/flag", (f) => seen.push(f));
  assert.equal(loaded.w, 90);
  assert.equal(loaded.h, 60);
  assert.deepEqual(seen, [loaded]);
  failLoad = true;
  assert.equal(await loadFlag("/islands/1/flag"), null);
});
