// 地名（案34）: ラベルを出す境目と、材料の並びの確かめ。
import test from "node:test";
import assert from "node:assert/strict";

import { GEO_LABEL_MIN_SCALE, geoLabelsShown, geoNameRows } from "../../server/static/js/map/geonames.js";

test("地名のラベルは拡大したときだけ", () => {
  assert.equal(GEO_LABEL_MIN_SCALE, 0.9);
  assert.equal(geoLabelsShown(0.9), true);
  assert.equal(geoLabelsShown(1.5), true);
  assert.equal(geoLabelsShown(0.89), false);
  assert.equal(geoLabelsShown(0.25), false);
  assert.equal(geoLabelsShown(undefined), false);
});

test("地名の行は、座標と名前がそろったものだけ残る", () => {
  const rows = geoNameRows([
    { x: 3, y: 4, name: "青山" },
    { x: 1.5, y: 2, name: "壊れた行" },
    { x: 5, y: 6, name: "" },
    null,
    { x: 7, y: 8 },
  ]);
  assert.deepEqual(rows, [{ x: 3, y: 4, name: "青山" }]);
  assert.deepEqual(geoNameRows(null), []);
  assert.deepEqual(geoNameRows(undefined), []);
});
