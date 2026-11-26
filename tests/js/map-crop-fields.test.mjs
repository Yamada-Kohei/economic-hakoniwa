// node --test tests/js/map-crop-fields.test.mjs
// 畑の色を作柄で変える（第4波19a）: 農場の収穫に掛かる倍率（crop_yield）で帯を決め、
// 新しい絵は描かず、元の絵に帯の色を重ねた写しで描くこと。材料のない畑・ふつうの
// 作柄は元の絵のまま。絵のない畑は色の箱を作柄の色に寄せること。
import test from "node:test";
import assert from "node:assert/strict";
import {
  CROP_POOR,
  CROP_RICH,
  CROP_TINTS,
  CROP_WITHERED,
  cropBand,
  cropBoxColor,
  cropSprite,
  drawIsland,
} from "../../server/static/js/map/renderer.js";

const theme = {
  tile_width: 64, tile_height: 32, step_height: 8,
  terrain: { plain: { top: "#8a8", left: "#686", right: "#464" } },
  fallback: { top: "#888", left: "#666", right: "#444" },
  facilities: { farm: { color: "#e3c565", height: 0.3 } },
  facility_fallback: { color: "#999", height: 1 }, facility_unit: 8,
  construction: "#f80", capital_roof: "#c9a227", power_line: "#111", no_development: "#111",
};
const defs = {
  terrain: { plain: { height: 0 } },
  housing: "housing", settlements: {},
  facilities: {
    farm: { category: "primary", sprite: "farm", recipes: { default: { inputs: {}, outputs: { grain: 20 } } } },
  },
  theme,
};
const view = { scale: 1, offsetX: 0, offsetY: 0, dpr: 1 };

// 色を効かせた写しを作るための document の代わり。作った canvas と操作を記録する。
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

/** 描いた物を記録する文脈（map-smoke-order と同じ形）。 */
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
    set(t, key, value) { calls.push(["set", key, value]); t[key] = value; return true; },
  });
}

function farmSprite() {
  const img = { naturalWidth: 64, naturalHeight: 33, name: "farm-img" };
  return { sprite: { img, w: 64, h: 33, ax: 32, ay: 33 }, img };
}

function mapWith(facilities) {
  return { size: 2, terrain: Array(4).fill("plain"), capital: null, facilities };
}

test("作柄の帯は倍率の境目で決まる", () => {
  assert.equal(cropBand(1.5), "rich");
  assert.equal(cropBand(CROP_RICH), "rich");
  assert.equal(cropBand(CROP_RICH - 0.01), null);
  assert.equal(cropBand(1.0), null);
  assert.equal(cropBand(CROP_POOR), null);
  assert.equal(cropBand(CROP_POOR - 0.01), "poor");
  assert.equal(cropBand(CROP_WITHERED), "poor");
  assert.equal(cropBand(CROP_WITHERED - 0.01), "withered");
  assert.equal(cropBand(0.4), "withered");
});

test("材料が無ければ帯は無い（色を変えない）", () => {
  assert.equal(cropBand(undefined), null);
  assert.equal(cropBand(null), null);
  assert.equal(cropBand("1.4"), null);
});

test("写しは元の絵に帯の色を source-atop で重ね、使い回す", () => {
  const { sprite, img } = farmSprite();
  const before = createdCanvases.length;
  const tinted = cropSprite(sprite, "rich");
  assert.notEqual(tinted, sprite);
  assert.equal(createdCanvases.length, before + 1);
  const canvas = createdCanvases[before];
  assert.equal(canvas.width, 64);
  assert.equal(canvas.height, 33);
  assert.deepEqual(canvas.ops[0], ["drawImage", img, 0, 0]);
  assert.deepEqual(canvas.ops[1], ["gco", "source-atop"]);
  assert.deepEqual(canvas.ops[2], ["fillStyle", CROP_TINTS.rich]);
  assert.equal(canvas.ops[3][0], "fillRect");
  assert.equal(tinted.img, canvas);
  assert.equal(tinted.w, sprite.w);
  assert.equal(tinted.ax, sprite.ax);
  // 同じ絵・同じ帯なら作り直さない。帯が違えば別の写し
  assert.equal(cropSprite(sprite, "rich"), tinted);
  assert.equal(createdCanvases.length, before + 1);
  assert.notEqual(cropSprite(sprite, "withered"), tinted);
  // 知らない帯は元の絵のまま
  assert.equal(cropSprite(sprite, "unknown"), sprite);
});

test("豊作の畑は色を効かせた写しで描き、ふつう・材料なしは元の絵で描く", () => {
  const { sprite, img } = farmSprite();
  const sprites = new Map([["facility/farm", sprite]]);
  const images = (facilities) => {
    const ctx = recorder();
    drawIsland(ctx, mapWith(facilities), defs, view, { sprites });
    return ctx.calls.filter((c) => c[0] === "drawImage").map((c) => c[1]);
  };
  const rich = images([{ x: 0, y: 0, kind: "farm", built: true, recipe: "default", crop_yield: 1.4 }]);
  assert.equal(rich.length, 1);
  assert.notEqual(rich[0], img); // 写し（canvas）が描かれる
  const normal = images([{ x: 0, y: 0, kind: "farm", built: true, recipe: "default", crop_yield: 1.0 }]);
  assert.deepEqual(normal, [img]);
  const noData = images([{ x: 0, y: 0, kind: "farm", built: true, recipe: "default" }]);
  assert.deepEqual(noData, [img]);
});

test("絵のない畑は、箱の色を作柄の色に寄せる", () => {
  assert.equal(cropBoxColor("#e3c565", null), "#e3c565");
  assert.equal(cropBoxColor("#e3c565", "unknown"), "#e3c565");
  const rich = cropBoxColor("#e3c565", "rich");
  const withered = cropBoxColor("#e3c565", "withered");
  assert.match(rich, /^#[0-9a-f]{6}$/);
  assert.match(withered, /^#[0-9a-f]{6}$/);
  // 豊作は緑み（g が r より大きい）、凶作は枯れ色（r が g より大きい）
  const rgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  const [rr, rg] = rgb(rich);
  const [wr, wg] = rgb(withered);
  assert.ok(rg > rr, `豊作は緑み: ${rich}`);
  assert.ok(wr > wg, `凶作は枯れ色: ${withered}`);
  // 箱で描くときも、上面の fill に作柄の色が使われる
  const ctx = recorder();
  drawIsland(ctx, mapWith([{ x: 0, y: 0, kind: "farm", built: true, recipe: "default", crop_yield: 0.5 }]), defs, view, { sprites: new Map() });
  const styles = ctx.calls.filter((c) => c[0] === "set" && c[1] === "fillStyle").map((c) => c[2]);
  assert.ok(styles.includes(withered), `箱の色に作柄が効く: ${styles.join(",")}`);
});
