// node --test tests/js/map-decline-art.test.mjs
// 衰退の第二段（案10）: 新しい絵は描かず、段階が下がった家の絵にだけ暗く・
// くすませる効果をかけること。印（変わったマス）を出さないときは元の絵のまま。
import test from "node:test";
import assert from "node:assert/strict";
import { DECLINE_BOX_SHADE, DECLINE_DARKEN, darkenedSprite, drawIsland, shade } from "../../server/static/js/map/renderer.js";

const theme = {
  tile_width: 64, tile_height: 32, step_height: 8,
  terrain: { plain: { top: "#88aa88", left: "#668866", right: "#446644" } },
  fallback: { top: "#888888", left: "#666666", right: "#444444" },
  facilities: {}, facility_fallback: { color: "#aabbcc", height: 1 }, facility_unit: 8,
  construction: "#ff8800", capital_roof: "#c9a227", power_line: "#111111", no_development: "#111111",
};
const defs = {
  terrain: { plain: { height: 0 } },
  housing: "housing", settlements: { village: { height: 1 } },
  facilities: {},
  theme,
};
const view = { scale: 1, offsetX: 0, offsetY: 0, dpr: 1 };

// 暗くした写しを作るための document の代わり。作った canvas と操作を記録する。
const createdCanvases = [];
globalThis.document = {
  createElement(tag) {
    assert.equal(tag, "canvas");
    const ops = [];
    const canvas = {
      width: 0, height: 0, ops,
      getContext(kind) {
        assert.equal(kind, "2d");
        return {
          drawImage: (...a) => ops.push(["drawImage", ...a]),
          fillRect: (...a) => ops.push(["fillRect", ...a]),
          set globalCompositeOperation(v) { ops.push(["gco", v]); },
          set fillStyle(v) { ops.push(["fillStyle", v]); },
        };
      },
    };
    createdCanvases.push(canvas);
    return canvas;
  },
};

/** 描いた物を記録する canvas の文脈。fill はその時点の fillStyle も残す。 */
function recorder() {
  const calls = [];
  const ctx = {
    canvas: { width: 640, height: 480 }, calls,
    setTransform() {}, clearRect() {}, save() {}, restore() {},
    beginPath() {}, moveTo() {}, lineTo() {}, closePath() {},
    fill() { calls.push(["fill", ctx.fillStyle]); },
    stroke() {}, fillRect() {}, arc() {}, ellipse() {}, fillText() {},
    setLineDash() {},
    drawImage(...a) { calls.push(["drawImage", ...a]); },
  };
  return ctx;
}

function mapWith(facilities) {
  return {
    size: 2, terrain: ["plain", "plain", "plain", "plain"],
    capital: [1, 1], capital_buildings: [], facilities,
  };
}

const HOUSE = { x: 0, y: 0, kind: "housing", built: true, level: "village" };

function houseSprite() {
  const img = { naturalWidth: 64, naturalHeight: 56, name: "house-img" };
  return { sprite: { img, w: 64, h: 56, ax: 32, ay: 56 }, img };
}

test("衰退のマスは、元の絵に暗色を重ねた写しで描く", () => {
  const { sprite, img } = houseSprite();
  const before = createdCanvases.length;
  const ctx = recorder();
  drawIsland(ctx, mapWith([HOUSE]), defs, view, {
    sprites: new Map([["housing/village", sprite]]),
    changeMarks: new Map([["0,0", "decline"]]),
  });
  assert.equal(createdCanvases.length, before + 1);
  const canvas = createdCanvases[before];
  // 写しの中身: 元の絵を描き、source-atop で暗色を全面に重ねる（透明な所は残す）
  assert.deepEqual(canvas.ops[0], ["drawImage", img, 0, 0]);
  assert.deepEqual(canvas.ops[1], ["gco", "source-atop"]);
  assert.deepEqual(canvas.ops[2], ["fillStyle", DECLINE_DARKEN]);
  assert.deepEqual(canvas.ops[3], ["fillRect", 0, 0, 64, 56]);
  // 地図には写しが描かれ、元の絵そのものは描かれない
  const drawn = ctx.calls.filter((c) => c[0] === "drawImage").map((c) => c[1]);
  assert.ok(drawn.includes(canvas));
  assert.ok(!drawn.includes(img));
});

test("変わったマスの印を出さないときは、衰退でも元の絵のまま", () => {
  const { sprite, img } = houseSprite();
  const before = createdCanvases.length;
  const ctx = recorder();
  drawIsland(ctx, mapWith([HOUSE]), defs, view, { sprites: new Map([["housing/village", sprite]]) });
  const drawn = ctx.calls.filter((c) => c[0] === "drawImage").map((c) => c[1]);
  assert.ok(drawn.includes(img));
  assert.equal(createdCanvases.length, before); // 写しは作らない
});

test("段階が上がった（stage）マスは暗くしない", () => {
  const { sprite, img } = houseSprite();
  const before = createdCanvases.length;
  const ctx = recorder();
  drawIsland(ctx, mapWith([HOUSE]), defs, view, {
    sprites: new Map([["housing/village", sprite]]),
    changeMarks: new Map([["0,0", "stage"]]),
  });
  const drawn = ctx.calls.filter((c) => c[0] === "drawImage").map((c) => c[1]);
  assert.ok(drawn.includes(img));
  assert.equal(createdCanvases.length, before);
});

test("暗くした写しは絵ごとに1回だけ作って使い回す", () => {
  const { sprite } = houseSprite();
  const before = createdCanvases.length;
  const a = darkenedSprite(sprite);
  const b = darkenedSprite(sprite);
  assert.equal(a, b);
  assert.equal(createdCanvases.length, before + 1);
  // 大きさと基準点は元の絵と同じ
  assert.equal(a.w, sprite.w);
  assert.equal(a.h, sprite.h);
  assert.equal(a.ax, sprite.ax);
  assert.equal(a.ay, sprite.ay);
});

test("絵のないマス（色の箱）では、衰退のマスの色が暗くなる", () => {
  const plain = recorder();
  drawIsland(plain, mapWith([HOUSE]), defs, view, {});
  const fillsA = plain.calls.filter((c) => c[0] === "fill").map((c) => c[1]);
  assert.ok(fillsA.includes("#aabbcc")); // 箱の上面は定義の色のまま

  const declined = recorder();
  drawIsland(declined, mapWith([HOUSE]), defs, view, {
    changeMarks: new Map([["0,0", "decline"]]),
  });
  const fillsB = declined.calls.filter((c) => c[0] === "fill").map((c) => c[1]);
  assert.ok(fillsB.includes(shade("#aabbcc", DECLINE_BOX_SHADE)));
  assert.ok(!fillsB.includes("#aabbcc"));
});
