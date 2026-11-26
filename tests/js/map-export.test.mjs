// node --test tests/js/map-export.test.mjs
// 地図の書き出し基盤（案30）: 島全体が収まる範囲の決め方と、国のカードの
// 置き場所・文字の折り返し。
import test from "node:test";
import assert from "node:assert/strict";
import {
  CARD,
  cardLayout,
  EXPORT_MARGIN,
  mapBounds,
  wrapLines,
} from "../../server/static/js/map/export.js";

const theme = { tile_width: 64, tile_height: 32, step_height: 8 };
const defs = { theme, terrain: { sea: { height: 0 }, mountain: { height: 3 } } };

test("範囲は全マスのひし形と余白を含む", () => {
  const map = { size: 2, terrain: ["sea", "sea", "sea", "sea"] };
  const b = mapBounds(map, defs);
  assert.equal(b.minX, -64 - EXPORT_MARGIN.side);
  assert.equal(b.width, 128 + EXPORT_MARGIN.side * 2);
  assert.equal(b.minY, -EXPORT_MARGIN.top);
  assert.equal(b.height, 64 + EXPORT_MARGIN.top + EXPORT_MARGIN.bottom);
});

test("高い地形は上の範囲を押し上げる", () => {
  const flat = mapBounds({ size: 2, terrain: ["sea", "sea", "sea", "sea"] }, defs);
  const high = mapBounds({ size: 2, terrain: ["mountain", "sea", "sea", "sea"] }, defs);
  assert.equal(high.minY, flat.minY - 24); // 高さ 3 × step_height 8
  assert.equal(high.height, flat.height + 24);
});

test("カードの置き場所はカードの中に収まる", () => {
  const L = cardLayout();
  assert.equal(L.map.x + L.map.w, CARD.width - CARD.pad);
  assert.ok(L.mapDate.y < CARD.height);
  assert.ok(L.foot.y < CARD.height);
});

test("文字は幅で折り返し、行数の上限を守る", () => {
  const ctx = { measureText: (s) => ({ width: [...s].length * 10 }) };
  assert.deepEqual(wrapLines(ctx, "あいうえお", 30), ["あいう", "えお"]);
  assert.deepEqual(wrapLines(ctx, "あいうえおかきくけこ", 30, 2), ["あいう", "えおか"]);
  assert.deepEqual(wrapLines(ctx, "ab\ncd", 100), ["ab", "cd"]);
});
