// node --test tests/js/map-ruler.test.mjs
// 地図の縁の目盛りと座標（UI-85）: 既定では出さず、ボタンで出したかどうかをブラウザに覚えること。
import test from "node:test";
import assert from "node:assert/strict";
import { RULER_KEY, readRulerShown, writeRulerShown } from "../../server/static/js/map/ruler.js";

function memory() {
  const items = new Map();
  return { getItem: (k) => items.get(k) ?? null, setItem: (k, v) => items.set(k, String(v)) };
}

test("覚えていなければ目盛りは出さない", () => {
  assert.equal(readRulerShown(memory()), false);
  assert.equal(readRulerShown(null), false);
});

test("出したことを覚え、消したことも覚える", () => {
  const storage = memory();
  writeRulerShown(storage, true);
  assert.equal(storage.getItem(RULER_KEY), "on");
  assert.equal(readRulerShown(storage), true);
  writeRulerShown(storage, false);
  assert.equal(readRulerShown(storage), false);
});

test("覚えられない窓でも止まらず、出さないままにする", () => {
  const broken = { getItem: () => { throw new Error("denied"); }, setItem: () => { throw new Error("denied"); } };
  assert.doesNotThrow(() => writeRulerShown(broken, true));
  assert.equal(readRulerShown(broken), false);
});
