// node --test tests/js/map-history.test.mjs
// 地図の変遷（案29）の変化レンズ: 既定では出さず、切り替えたことをブラウザに覚えること。
// API の lens の並びを、マスの組に直すこと。
import test from "node:test";
import assert from "node:assert/strict";
import {
  LENS_COLORS,
  LENS_KEY,
  lensMarks,
  readLensShown,
  writeLensShown,
} from "../../server/static/js/map/history.js";

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
