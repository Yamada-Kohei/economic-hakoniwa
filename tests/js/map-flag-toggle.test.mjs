// node --test tests/js/map-flag-toggle.test.mjs
// 首都の旗（案23）の表示の切り替え: 既定では出し、切り替えたことをブラウザに覚えること。
import test from "node:test";
import assert from "node:assert/strict";
import { FLAG_KEY, readFlagShown, writeFlagShown } from "../../server/static/js/map/flagtoggle.js";

function memory() {
  const items = new Map();
  return { getItem: (k) => items.get(k) ?? null, setItem: (k, v) => items.set(k, String(v)) };
}

test("覚えていなければ旗は出す", () => {
  assert.equal(readFlagShown(memory()), true);
  assert.equal(readFlagShown(null), true);
});

test("消したことを覚え、出したことも覚える", () => {
  const storage = memory();
  writeFlagShown(storage, false);
  assert.equal(storage.getItem(FLAG_KEY), "off");
  assert.equal(readFlagShown(storage), false);
  writeFlagShown(storage, true);
  assert.equal(readFlagShown(storage), true);
});

test("覚えられない窓でも止まらず、出したままにする", () => {
  const broken = {
    getItem: () => {
      throw new Error("denied");
    },
    setItem: () => {
      throw new Error("denied");
    },
  };
  assert.doesNotThrow(() => writeFlagShown(broken, false));
  assert.equal(readFlagShown(broken), true);
});
