// node --test tests/js/map-wave4.test.mjs
// 第4波の残り（#5803 の順）: 21 時代の街並み・13 暮らしの気配・14 繁栄・
// 18 季節・15 お金の出来事・26 戦闘の跡。どれも数から決める物差し（wave4.js）と、
// 新しい絵は描かず元の絵への効果＋印で描くこと（renderer.js）を確かめる。
import test from "node:test";
import assert from "node:assert/strict";
import { drawIsland, eraBoxColor, markFade } from "../../server/static/js/map/renderer.js";
import {
  eraTint,
  lifeDots,
  lifeLaundry,
  marksByCell,
  monthOfTurn,
  prosperityBand,
  readBattlesShown,
  readLifeShown,
  readSeasonShown,
  seasonOf,
  seasonTint,
  writeLifeShown,
} from "../../server/static/js/map/wave4.js";

test("時代の色: 住宅の段階ごとに色があり、知らない段階は元の絵のまま", () => {
  for (const level of ["village", "town", "city", "metropolis", "high_rise", "skyscraper"]) {
    assert.match(eraTint(level), /^rgba\(/);
  }
  assert.equal(eraTint("castle"), null);
  assert.equal(eraTint(undefined), null);
});

test("絵のない住宅の時代の色: 目標色へ寄り、知らない段階は元のまま", () => {
  assert.notEqual(eraBoxColor("#ffffff", "village"), "#ffffff");
  assert.equal(eraBoxColor("#ffffff", "castle"), "#ffffff");
  assert.equal(eraBoxColor("red", "village"), "red");
});

test("暮らしの気配: 人影の数は人口の満ち具合で決まる", () => {
  assert.equal(lifeDots(0, 2400), 0);
  assert.equal(lifeDots(100, 2400), 1);
  assert.equal(lifeDots(1000, 2400), 2);
  assert.equal(lifeDots(2000, 2400), 3);
  assert.equal(lifeDots(100, 0), 0);
  assert.equal(lifeDots(100, undefined), 0);
  assert.equal(lifeLaundry("city"), true);
  assert.equal(lifeLaundry("skyscraper"), true);
  assert.equal(lifeLaundry("town"), false);
});

test("繁栄: 幸福度の帯。材料が無ければ飾らない", () => {
  assert.equal(prosperityBand(null), null);
  assert.equal(prosperityBand(69), null);
  assert.equal(prosperityBand(70), "warm");
  assert.equal(prosperityBand(84), "warm");
  assert.equal(prosperityBand(85), "rich");
});

test("季節: 月はターンから、季節は月と気候帯から決まる", () => {
  assert.equal(monthOfTurn(1), 1);
  assert.equal(monthOfTurn(12), 12);
  assert.equal(monthOfTurn(13), 1);
  assert.equal(monthOfTurn(178), 10);
  assert.equal(seasonOf(1, "temperate"), "snow");
  assert.equal(seasonOf(4, "temperate"), "spring");
  assert.equal(seasonOf(7, "temperate"), "summer");
  assert.equal(seasonOf(10, "temperate"), "autumn");
  assert.equal(seasonOf(11, "cold"), "snow");
  assert.equal(seasonOf(3, "cold"), "snow");
  assert.equal(seasonOf(6, "tropical"), "rainy");
  assert.equal(seasonOf(1, "tropical"), "dry");
  assert.equal(seasonOf(6, "nowhere"), null);
});

test("季節の色: 雪は陸に、海の氷は寒冷の雪だけ", () => {
  assert.ok(seasonTint("snow", true, "temperate"));
  assert.equal(seasonTint("snow", false, "temperate"), null);
  assert.ok(seasonTint("snow", false, "cold"));
  assert.equal(seasonTint("summer", false, "temperate"), null);
  assert.equal(seasonTint(null, true, "temperate"), null);
});

test("切り替えの既定: 季節は出す、暮らしと戦闘の跡は出さない。覚えた値は優先", () => {
  const store = new Map();
  const storage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, v),
  };
  assert.equal(readSeasonShown(storage), true);
  assert.equal(readLifeShown(storage), false);
  assert.equal(readBattlesShown(storage), false);
  writeLifeShown(storage, true);
  assert.equal(readLifeShown(storage), true);
  writeLifeShown(storage, false);
  assert.equal(readLifeShown(storage), false);
});

test("印の材料: 並びをマスの Map にし、古い出来事ほど薄れる", () => {
  const marks = marksByCell([
    { x: 1, y: 2, kind: "hit", age: 0 },
    { x: 1, y: 2, kind: "intercept", age: 1 },
  ]);
  assert.equal(marks.size, 1);
  assert.deepEqual(marks.get("1,2"), { kind: "intercept", age: 1 });
  assert.equal(markFade(0), 1);
  assert.ok(markFade(2) < markFade(1));
  assert.ok(markFade(99) >= 0.3);
});

// --- 描画の煙テスト（落ちないこと・印が描かれること） -----------------------

const theme = {
  tile_width: 64, tile_height: 32, step_height: 8,
  terrain: { plain: { top: "#8a8", left: "#686", right: "#464" }, sea: { top: "#48c", left: "#26a", right: "#048" } },
  fallback: { top: "#888", left: "#666", right: "#444" },
  facilities: { housing: { color: "#caa", height: 0.3 } },
  facility_fallback: { color: "#999", height: 1 }, facility_unit: 8,
  construction: "#f80", capital_roof: "#c9a227", power_line: "#111", no_development: "#111",
};
const defs = {
  terrain: { plain: { height: 0, land: true }, sea: { height: 0, land: false } },
  housing: "housing",
  settlements: { village: { capacity: 2400 }, city: { capacity: 15000 } },
  facilities: { housing: { category: "housing" } },
  theme,
};
const view = { scale: 1, offsetX: 0, offsetY: 0, dpr: 1 };

globalThis.document = {
  createElement() {
    return {
      width: 0, height: 0,
      getContext: () => ({ drawImage() {}, fillRect() {} }),
    };
  },
};

function recorder() {
  const calls = [];
  const target = {
    canvas: { width: 640, height: 480 },
    calls,
    createRadialGradient: () => ({ addColorStop: () => {} }),
  };
  return new Proxy(target, {
    get(t, key) {
      if (key in t) return t[key];
      return (...args) => { calls.push([key, ...args]); };
    },
    set(t, key, value) { calls.push(["set", key, value]); t[key] = value; return true; },
  });
}

function wave4Map() {
  const size = 3;
  const cells = size * size;
  return {
    size, turn: 178, climate: "temperate", capital: null, regime: "democracy",
    terrain: Array(cells).fill("plain"),
    population: Array.from({ length: cells }, (_, i) => (i === 4 ? 12000 : 0)),
    happiness: Array.from({ length: cells }, (_, i) => (i === 4 ? 90 : 40)),
    zone: Array(cells).fill("none"), power_line: Array(cells).fill(false),
    trench: Array(cells).fill(false), occupied: Array(cells).fill(false),
    fallout: Array(cells).fill(0), pollution: Array(cells).fill(0),
    power: Array(cells).fill(0), infra: {}, facilities: [
      { x: 1, y: 1, kind: "housing", level: "city", built: true, size: 1, grade: 0, status: "active" },
    ],
    units: [],
  };
}

test("第4波の表示を全部入れても地図が描ける（住宅の印・戦闘の跡・お金の出来事）", () => {
  const ctx = recorder();
  const options = {
    sprites: new Map(), season: true, life: true, prosperity: true,
    battleMarks: new Map([["1,1", { kind: "hit", age: 0 }]]),
    moneyMarks: new Map([["1,1", { kind: "bank", age: 1 }], ["0,0", { kind: "cloud", age: 0 }]]),
  };
  drawIsland(ctx, wave4Map(), defs, view, options);
  const kinds = new Set(ctx.calls.map((c) => c[0]));
  assert.ok(kinds.has("arc")); // 人影・花の点
  assert.ok(kinds.has("ellipse")); // 首都の上の雲
  assert.ok(kinds.has("fill")); // 季節の色・焦げた地面
});

test("切り替えを全部切れば、第4波の印は描かれない", () => {
  const ctx = recorder();
  drawIsland(ctx, wave4Map(), defs, view, { sprites: new Map() });
  const kinds = new Set(ctx.calls.map((c) => c[0]));
  assert.ok(!kinds.has("ellipse"));
});
