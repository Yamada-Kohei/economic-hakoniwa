// node --test tests/js/map-roads.test.mjs
// 道路・鉄道の線（案2）: 切り替えの覚え方と、線の引き方決め（roads.js）。
// 描き方の決めは掲示板 #5688・#5690: つながりの記録が無いデータなので、
// 隣に同じ種類の整備があるときだけそちらへ線をつなぎ、無いマスは短い線だけ
// （stub）。斜めはつながない。引き方は座標と整備度だけで決まる（乱数なし）。
import test from "node:test";
import assert from "node:assert/strict";
import {
  RAIL_KEY,
  ROAD_KEY,
  readRailShown,
  readRoadShown,
  transportSegments,
  writeRailShown,
  writeRoadShown,
} from "../../server/static/js/map/roads.js";

function memory() {
  const items = new Map();
  return { getItem: (k) => items.get(k) ?? null, setItem: (k, v) => items.set(k, String(v)) };
}

test("覚えていなければ道路も鉄道も出す", () => {
  assert.equal(readRoadShown(memory()), true);
  assert.equal(readRailShown(memory()), true);
  assert.equal(readRoadShown(null), true);
  assert.equal(readRailShown(null), true);
});

test("道路と鉄道は別々に覚える", () => {
  const storage = memory();
  writeRoadShown(storage, false);
  assert.equal(storage.getItem(ROAD_KEY), "off");
  assert.equal(readRoadShown(storage), false);
  assert.equal(readRailShown(storage), true);
  writeRailShown(storage, false);
  assert.equal(storage.getItem(RAIL_KEY), "off");
  assert.equal(readRailShown(storage), false);
  writeRoadShown(storage, true);
  assert.equal(readRoadShown(storage), true);
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
  assert.doesNotThrow(() => writeRoadShown(broken, false));
  assert.doesNotThrow(() => writeRailShown(broken, false));
  assert.equal(readRoadShown(broken), true);
  assert.equal(readRailShown(broken), true);
});

/** size × size の地図に、種類ごとの整備度（{ "x,y": 度 }）を置いた map を作る。 */
function mapWith(size, infra) {
  const map = { size, infra: {} };
  for (const [kind, cells] of Object.entries(infra)) {
    const levels = Array(size * size).fill(0);
    for (const [key, level] of Object.entries(cells)) {
      const [x, y] = key.split(",").map(Number);
      levels[y * size + x] = level;
    }
    map.infra[kind] = levels;
  }
  return map;
}

test("整備の無い地図と、データの無い種類では何も返らない", () => {
  assert.equal(transportSegments(mapWith(4, {}), "road").size, 0);
  assert.equal(transportSegments(mapWith(4, { road: {} }), "road").size, 0);
  assert.equal(transportSegments({ size: 4 }, "road").size, 0);
});

test("隣り合うマスどうしは互いへ線をつなぎ、整備度をそのまま持つ", () => {
  const map = mapWith(5, { road: { "1,1": 80, "2,1": 40 } });
  const segs = transportSegments(map, "road");
  assert.equal(segs.size, 2);
  assert.deepEqual(segs.get(1 * 5 + 1), { level: 80, links: [[1, 0]], stub: false });
  assert.deepEqual(segs.get(1 * 5 + 2), { level: 40, links: [[-1, 0]], stub: false });
});

test("つなぐ隣が無いマスは短い線だけ（stub）になる", () => {
  const map = mapWith(5, { road: { "2,2": 100 } });
  const segs = transportSegments(map, "road");
  assert.deepEqual(segs.get(2 * 5 + 2), { level: 100, links: [], stub: true });
});

test("斜めのマスとはつながない", () => {
  const map = mapWith(5, { road: { "1,1": 50, "2,2": 50 } });
  const segs = transportSegments(map, "road");
  assert.equal(segs.get(1 * 5 + 1).stub, true);
  assert.equal(segs.get(2 * 5 + 2).stub, true);
});

test("違う種類の整備とはつながない", () => {
  const map = mapWith(5, { road: { "1,1": 50 }, rail: { "2,1": 50 } });
  assert.equal(transportSegments(map, "road").get(1 * 5 + 1).stub, true);
  assert.equal(transportSegments(map, "rail").get(1 * 5 + 2).stub, true);
});

test("整備度 0 のマスは入らず、地図の端でも隣を見誤らない", () => {
  const map = mapWith(3, { road: { "0,0": 0, "2,0": 30 } });
  const segs = transportSegments(map, "road");
  assert.equal(segs.has(0), false);
  assert.deepEqual(segs.get(2), { level: 30, links: [], stub: true });
});

test("十字に整備があれば 4 方向ともつなぐ", () => {
  const map = mapWith(5, {
    road: { "2,2": 90, "1,2": 10, "3,2": 10, "2,1": 10, "2,3": 10 },
  });
  const center = transportSegments(map, "road").get(2 * 5 + 2);
  assert.equal(center.links.length, 4);
  assert.equal(center.stub, false);
});
