// node --test tests/js/map-history.test.mjs
// 地図の変遷（案29）の変化レンズ: 既定では出さず、切り替えたことをブラウザに覚えること。
// API の lens の並びを、マスの組に直すこと。
import test from "node:test";
import assert from "node:assert/strict";
import {
  ENGRAVING_COLORS,
  ENGRAVINGS_KEY,
  LENS_COLORS,
  LENS_KEY,
  engravingMarks,
  lensMarks,
  readEngravingsShown,
  readLensShown,
  writeEngravingsShown,
  writeLensShown,
} from "../../server/static/js/map/history.js";
import { monumentImageSize } from "../../server/static/js/map/renderer.js";

function memory() {
  const items = new Map();
  return { getItem: (k) => items.get(k) ?? null, setItem: (k, v) => items.set(k, String(v)) };
}

test("覚えていなければレンズは出さない", () => {
  assert.equal(readLensShown(memory()), false);
  assert.equal(readLensShown(null), false);
});

test("出したことを覚え、消したことも覚える", () => {
  const storage = memory();
  writeLensShown(storage, true);
  assert.equal(storage.getItem(LENS_KEY), "on");
  assert.equal(readLensShown(storage), true);
  writeLensShown(storage, false);
  assert.equal(readLensShown(storage), false);
});

test("覚えられない窓でも止まらず、出さないままにする", () => {
  const broken = { getItem: () => { throw new Error("denied"); }, setItem: () => { throw new Error("denied"); } };
  assert.doesNotThrow(() => writeLensShown(broken, true));
  assert.equal(readLensShown(broken), false);
});

test("lens の並びをマスの組に直す", () => {
  const marks = lensMarks({
    lens: [
      { x: 1, y: 2, kind: "new" },
      { x: 3, y: 4, kind: "down" },
      { x: 5, y: 6, kind: "changed" },
    ],
  });
  assert.equal(marks.get("1,2"), "new");
  assert.equal(marks.get("3,4"), "down");
  assert.equal(marks.get("5,6"), "changed");
  assert.deepEqual(Object.keys(LENS_COLORS).sort(), ["changed", "down", "new"]);
});

test("知らない種類や壊れた行は飛ばす", () => {
  const marks = lensMarks({
    lens: [
      { x: 0, y: 0, kind: "unknown" },
      { x: 1.5, y: 0, kind: "new" },
      { y: 1, kind: "new" },
      null,
      { x: 2, y: 2, kind: "new" },
    ],
  });
  assert.equal(marks.size, 1);
  assert.equal(marks.get("2,2"), "new");
  assert.equal(lensMarks({}).size, 0);
  assert.equal(lensMarks(null).size, 0);
});

test("刻印は覚えていなければ出さず、切り替えを覚える", () => {
  const storage = memory();
  assert.equal(readEngravingsShown(storage), false);
  writeEngravingsShown(storage, true);
  assert.equal(storage.getItem(ENGRAVINGS_KEY), "on");
  assert.equal(readEngravingsShown(storage), true);
  writeEngravingsShown(storage, false);
  assert.equal(readEngravingsShown(storage), false);
  assert.equal(readEngravingsShown(null), false);
});

test("刻印の並びをマスの組に直し、同じマスは新しい物を採る", () => {
  const marks = engravingMarks([
    { x: 1, y: 1, mark: "disaster" },
    { x: 2, y: 2, mark: "scar" },
    { x: 1, y: 1, mark: "scar" },
    { x: 3, y: 3, mark: "unknown" },
    { x: 4.5, y: 4, mark: "scar" },
    null,
  ]);
  assert.equal(marks.size, 2);
  assert.equal(marks.get("1,1"), "scar");
  assert.equal(marks.get("2,2"), "scar");
  assert.deepEqual(Object.keys(ENGRAVING_COLORS).sort(), ["disaster", "scar"]);
  assert.equal(engravingMarks(null).size, 0);
});

test("自作の記念碑の絵は、マスの幅にそろえ、背が高すぎれば収める", () => {
  const theme = { tile_width: 64, tile_height: 32 };
  assert.deepEqual(monumentImageSize({ w: 100, h: 50 }, theme), { w: 64, h: 32 });
  const tall = monumentImageSize({ w: 10, h: 100 }, theme);
  assert.equal(tall.h, 64);
  assert.ok(tall.w < 64);
});
