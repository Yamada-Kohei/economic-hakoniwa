// node --test tests/js/map-smoke-order.test.mjs
// 煙の重なり順: 煙は施設と同じ奥行きの位置で描く（奥の工場の煙が、手前の
// 建物より先に描かれる＝手前の建物が煙を隠す）。島を描き終えてからまとめて
// 描くと、手前の建物の上に煙がかぶって見えた。
// Node の環境では粒の絵（キャンバス）が作れないため、従来のグラデーション
// 塗りの経路で描かれる。その経路でも描く順は同じ drawFacilitySmoke が担う。
import test from "node:test";
import assert from "node:assert/strict";
import { drawIsland } from "../../server/static/js/map/renderer.js";

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
  facilities: { smokefactory: { category: "industry", sprite: "smokefactory" } },
  theme,
};
const view = { scale: 1, offsetX: 0, offsetY: 0, dpr: 1 };
const BLDG = { name: "factory-image" };
const sprite = { img: BLDG, w: 64, h: 40, ax: 32, ay: 40 };

function mapWith(facilities) {
  return { size: 3, terrain: Array(9).fill("plain"), capital: null, facilities };
}

/** 描いた物を記録する文脈。グラデーションは中身の無い物で代用する。 */
function recorder() {
  const calls = [];
  const target = {
    canvas: { width: 640, height: 480 },
    calls,
    createRadialGradient: (...args) => {
      calls.push(["createRadialGradient", ...args]);
      return { addColorStop: () => {} };
    },
  };
  return new Proxy(target, {
    get(t, key) {
      if (key in t) return t[key];
      return (...args) => { calls.push([key, ...args]); };
    },
    set(t, key, value) { t[key] = value; return true; },
  });
}

test("奥の工場の煙は、手前の工場の絵より先に描かれる", () => {
  const ctx = recorder();
  const sprites = new Map([["facility/smokefactory", sprite]]);
  const map = mapWith([
    { x: 0, y: 0, kind: "smokefactory", built: true },
    { x: 1, y: 1, kind: "smokefactory", built: true },
  ]);
  drawIsland(ctx, map, defs, view, { sprites, smoke: true, animTime: 1.0 });
  const grads = ctx.calls.map((c, i) => [c, i]).filter(([c]) => c[0] === "createRadialGradient");
  const bldgs = ctx.calls.map((c, i) => [c, i]).filter(([c]) => c[0] === "drawImage" && c[1] === BLDG);
  assert.equal(bldgs.length, 2);
  assert.equal(grads.length, 12); // 煙源2件 × 粒6（詳しさ「近く」）
  const frontBldgAt = bldgs[1][1];
  const beforeFront = grads.filter(([, i]) => i < frontBldgAt).length;
  const afterFront = grads.filter(([, i]) => i > frontBldgAt).length;
  assert.equal(beforeFront, 6, "奥の工場の粒6つが手前の絵より先");
  assert.equal(afterFront, 6, "手前の工場の粒6つは自分の絵のあと");
});

test("煙を消すと粒は描かれない", () => {
  const ctx = recorder();
  const sprites = new Map([["facility/smokefactory", sprite]]);
  const map = mapWith([{ x: 0, y: 0, kind: "smokefactory", built: true }]);
  drawIsland(ctx, map, defs, view, { sprites, smoke: false, animTime: 1.0 });
  assert.equal(ctx.calls.filter((c) => c[0] === "createRadialGradient").length, 0);
});
