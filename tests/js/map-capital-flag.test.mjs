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

/** 旗を描いた直後の竿の線（moveTo が下端、lineTo が上端）。 */
function poleAfterFlag(ctx) {
  const at = ctx.calls.findIndex((c) => c[0] === "drawImage" && c[1] === FLAG);
  const rest = ctx.calls.slice(at);
  return { move: rest.find((c) => c[0] === "moveTo"), line: rest.find((c) => c[0] === "lineTo") };
}

/** 旗を描くときの translate（竿の上の位置）。 */
function flagTranslate(ctx) {
  const at = ctx.calls.findIndex((c) => c[0] === "drawImage" && c[1] === FLAG);
  return ctx.calls.slice(0, at).filter((c) => c[0] === "translate").pop();
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
  const [, , , , w, h] = draws[0];
  close(w, theme.tile_width / 8);
  close(h, (theme.tile_width / 8) * 60 / 90);
  // 建物の絵よりあとに旗を描く
  const bldgAt = ctx.calls.findIndex((c) => c[0] === "drawImage" && c[1] === BLDG);
  assert.ok(bldgAt >= 0);
  assert.ok(ctx.calls.indexOf(draws[0]) > bldgAt);
  // 竿はマスの真ん中に、建物の絵（高さ 73）のてっぺんへ少し沈めた位置から立つ
  const base = theme.tile_height - 73 + 3;
  const { move, line } = poleAfterFlag(ctx);
  close(move[1], 0);
  close(move[2], base);
  close(line[1], 0);
  close(line[2], base - h - 3);
  // 旗は竿の上から、場面の傾き（マスの高さ÷幅）で描く。白い縁（fillRect）は置かない
  const tr = flagTranslate(ctx);
  close(tr[1], 0);
  close(tr[2], base - h - 3);
  const shear = ctx.calls.find((c) => c[0] === "transform");
  assert.deepEqual(shear.slice(1), [1, theme.tile_height / theme.tile_width, 0, 1, 0, 0]);
  assert.equal(ctx.calls.filter((c) => c[0] === "fillRect").length, 0);
});

test("首都の絵に flagAnchor があれば、絵の頂点に竿を立てる", () => {
  const ctx = recorder();
  const anchored = { ...capitalSprite, flagAnchor: { x: 27, y: 4 } };
  const sprites = new Map([["capital/government_office", anchored]]);
  const map = mapWith([{ x: 0, y: 0, kind: "government", built: true }]);
  drawIsland(ctx, map, defs, view, { sprites, flag });
  assert.equal(flagDraws(ctx).length, 1);
  // 竿の下端は、絵の基準点 (32, 73) から見た flagAnchor (27, 4) の位置
  const { move } = poleAfterFlag(ctx);
  close(move[1], 27 - 32);
  close(move[2], theme.tile_height - 73 + 4);
  close(flagTranslate(ctx)[1], 27 - 32);
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
  const { move } = poleAfterFlag(ctx);
  close(move[1], 0);
  close(move[2], theme.tile_height / 2);
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
