// node --test tests/js/world-routes.test.mjs
// 世界の航路の読み替え（グラフィック強化 案4・第3波）: 線種の決め、実線の太さ、
// 主な品目の色、破線の間隔（最短の航路でも隙間が見えること。掲示板 #5825・#5827）。
import test from "node:test";
import assert from "node:assert/strict";
import {
  BLOCKED_COLOR,
  BLOCKED_DASH,
  ROUTE_DASH,
  goodColors,
  pairLineKind,
  routeWidth,
} from "../../server/static/js/world/trade.js";

const near = (a, b) => Math.abs(a - b) < 1e-9;

test("線種: 禁輸・戦争が最優先、額があれば実線、無ければ破線", () => {
  assert.equal(pairLineKind({ amount: 10 }, "war"), "blocked");
  assert.equal(pairLineKind({ amount: 10 }, "embargo"), "blocked");
  assert.equal(pairLineKind(null, "war"), "blocked");
  assert.equal(pairLineKind({ amount: 10 }, null), "solid");
  assert.equal(pairLineKind({ amount: 0 }, null), "solid");
  assert.equal(pairLineKind({ amount: null }, null), "dashed");
  assert.equal(pairLineKind(null, null), null);
});

test("太さ: 最大の額で上限、額なし・最大なしは細い方", () => {
  assert.ok(near(routeWidth(100, 100), 0.22));
  assert.ok(near(routeWidth(50, 100), 0.13));
  assert.ok(near(routeWidth(0, 100), 0.04));
  assert.equal(routeWidth(null, 100), 0.04);
  assert.equal(routeWidth(10, 0), 0.04);
});

test("品目の色: 地図に出る品目どうしで重ならず、区画の地の色にはならない", () => {
  const pairs = [
    { ab: { good: { good: "grain", name: "穀物", index: 0 } }, ba: { good: { good: "steel", name: "鋼鉄", index: 12 } } },
    { ab: { good: { good: "coal", name: "石炭", index: 5 } }, ba: null },
    { ab: { good: { good: "grain", name: "穀物", index: 0 } }, ba: null }, // 同じ品目は同じ色
  ];
  const colors = goodColors(pairs);
  assert.equal(colors.size, 3);
  assert.equal(new Set(colors.values()).size, 3); // 3 品目とも別の色
  for (const color of colors.values()) assert.notEqual(color, "#2f6db5");
  assert.deepEqual([...goodColors(pairs).entries()], [...colors.entries()]); // 決定的
});

test("破線: 最短の航路（長さ1）でも隙間が1つ以上入る間隔で、点線とは別", () => {
  const [dash, gap] = ROUTE_DASH;
  assert.ok(dash > 0 && gap > 0);
  assert.ok(dash + gap < 1);
  assert.notDeepEqual(ROUTE_DASH, BLOCKED_DASH);
  assert.equal(BLOCKED_COLOR, "#ff5252");
});
