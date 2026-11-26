// node --test tests/js/map-changes.test.mjs
// 前のターンから変わったマスの印（案3 の静止版＋案10 の衰退の印）: 既定では出さず、切り替えたことを
// ブラウザに覚えること。API の changed の並びを、マスの組に直すこと。
import test from "node:test";
import assert from "node:assert/strict";
import {
  CHANGE_COLORS,
  CHANGES_KEY,
  changeMarks,
  readChangesShown,
  writeChangesShown,
} from "../../server/static/js/map/changes.js";

function memory() {
  const items = new Map();
  return { getItem: (k) => items.get(k) ?? null, setItem: (k, v) => items.set(k, String(v)) };
}

test("覚えていなければ印は出さない", () => {
  assert.equal(readChangesShown(memory()), false);
  assert.equal(readChangesShown(null), false);
});

test("出したことを覚え、消したことも覚える", () => {
  const storage = memory();
  writeChangesShown(storage, true);
  assert.equal(storage.getItem(CHANGES_KEY), "on");
  assert.equal(readChangesShown(storage), true);
  writeChangesShown(storage, false);
  assert.equal(readChangesShown(storage), false);
});

test("覚えられない窓でも止まらず、出さないままにする", () => {
  const broken = { getItem: () => { throw new Error("denied"); }, setItem: () => { throw new Error("denied"); } };
  assert.doesNotThrow(() => writeChangesShown(broken, true));
  assert.equal(readChangesShown(broken), false);
});

test("changed の並びをマスの組に直す", () => {
  const marks = changeMarks({
    changed: [
      { x: 1, y: 2, kind: "built" },
      { x: 3, y: 4, kind: "removed" },
      { x: 5, y: 6, kind: "stage" },
      { x: 7, y: 8, kind: "decline" },
    ],
  });
  assert.equal(marks.get("1,2"), "built");
  assert.equal(marks.get("3,4"), "removed");
  assert.equal(marks.get("5,6"), "stage");
  assert.equal(marks.get("7,8"), "decline");
  assert.equal(marks.size, 4);
});

test("知らない種類や壊れた行は飛ばす", () => {
  const marks = changeMarks({
    changed: [
      { x: 1, y: 2, kind: "glow" },
      { x: "3", y: 4, kind: "built" },
      null,
      { x: 7, y: 8, kind: "built" },
    ],
  });
  assert.deepEqual([...marks.entries()], [["7,8", "built"]]);
  assert.equal(changeMarks({}).size, 0);
  assert.equal(changeMarks(null).size, 0);
});

test("種類の色は4種そろっている", () => {
  assert.deepEqual(Object.keys(CHANGE_COLORS).sort(), ["built", "decline", "removed", "stage"]);
});
