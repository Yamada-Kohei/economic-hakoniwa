// node --test tests/js/map-ships.test.mjs
// 港の商船（案6）と母港の艦隊（W-100）: 切り替えの覚え方と、海マスへの置き場所決め。
// 置き場所は座標だけで決まり（乱数なし）、同じ入力では同じ結果になること。
// 置き方の決めは掲示板 #6140・#6144: 1 海マスに 1 隻、docked の艦が先、溢れた分は
// その施設に接する海マスへ座標順に流れ、尽きたら描くのはそこまで。
import test from "node:test";
import assert from "node:assert/strict";
import {
  FLEET_CAP,
  FLEET_KEY,
  FLEET_SHIP_CAP,
  MERCHANT_CAP,
  MERCHANT_KEY,
  berthCells,
  placeShips,
  readFleetShown,
  readMerchantShown,
  shipCounts,
  writeFleetShown,
  writeMerchantShown,
} from "../../server/static/js/map/ships.js";

function memory() {
  const items = new Map();
  return { getItem: (k) => items.get(k) ?? null, setItem: (k, v) => items.set(k, String(v)) };
}

test("覚えていなければ商船も艦隊も出す", () => {
  assert.equal(readMerchantShown(memory()), true);
  assert.equal(readFleetShown(memory()), true);
  assert.equal(readMerchantShown(null), true);
  assert.equal(readFleetShown(null), true);
});

test("商船と艦隊は別々に覚える", () => {
  const storage = memory();
  writeMerchantShown(storage, false);
  assert.equal(storage.getItem(MERCHANT_KEY), "off");
  assert.equal(readMerchantShown(storage), false);
  assert.equal(readFleetShown(storage), true);
  writeFleetShown(storage, false);
  assert.equal(storage.getItem(FLEET_KEY), "off");
  assert.equal(readFleetShown(storage), false);
  writeMerchantShown(storage, true);
  assert.equal(readMerchantShown(storage), true);
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
  assert.doesNotThrow(() => writeMerchantShown(broken, false));
  assert.doesNotThrow(() => writeFleetShown(broken, false));
  assert.equal(readMerchantShown(broken), true);
  assert.equal(readFleetShown(broken), true);
});

// 5x5 の地図。全部海で、指定したマスだけ陸にできる
function fakeMap({ land = [], facilities = [], ...rest } = {}) {
  const size = 5;
  const terrain = Array.from({ length: size * size }, (_, i) => (land.includes(i) ? "plain" : "sea"));
  return { size, terrain, facilities, ...rest };
}
const DEFS = { terrain: { sea: { land: false }, plain: { land: true } } };
const ON = { merchant: true, fleet: true };

test("停泊マスは隣の海マスを座標順に返し、陸は除く", () => {
  const map = fakeMap({ land: [2 * 5 + 3] }); // (3,2) は陸
  const cells = berthCells(map, DEFS, 2, 2).map((c) => `${c.x},${c.y}`);
  assert.deepEqual(cells, ["1,1", "2,1", "3,1", "1,2", "1,3", "2,3", "3,3"]);
});

test("商船は港にいる隻数だけ、座標順に 1 マス 1 隻で置く", () => {
  const map = fakeMap({ port_ships: { x: 2, y: 2, count: 3 } });
  const placed = placeShips(map, DEFS, ON);
  const all = [...placed.values()].flat();
  assert.equal(all.length, 3);
  assert.ok(all.every((s) => s.name === "ship/merchant" && !s.fleet));
  assert.ok([...placed.values()].every((list) => list.length === 1)); // 1 マス 1 隻
  // 座標順の最初のマス (1,1) から順に置く
  assert.deepEqual(placed.get("1,1"), [{ name: "ship/merchant", dx: 0, dy: 0, fleet: false }]);
  assert.deepEqual([...placed.keys()], ["1,1", "2,1", "3,1"]);
});

test("商船の隻数には上限がある", () => {
  const map = fakeMap({ port_ships: { x: 2, y: 2, count: 99 } });
  const all = [...placeShips(map, DEFS, ON).values()].flat();
  assert.equal(all.length, MERCHANT_CAP);
});

test("商船を消すと商船だけ消え、港の材料がなければ何も出ない", () => {
  const map = fakeMap({ port_ships: { x: 2, y: 2, count: 3 } });
  assert.equal(placeShips(map, DEFS, { merchant: false, fleet: true }).size, 0);
  assert.equal(placeShips(fakeMap({}), DEFS, ON).size, 0);
  assert.equal(placeShips(fakeMap({ port_ships: { x: 2, y: 2, count: 0 } }), DEFS, ON).size, 0);
});

test("艦隊は軍港の隣に艦種ごとに置き、隻数の上限と合計の上限がある", () => {
  const base = { x: 2, y: 2, kind: "naval_base", built: true };
  const map = fakeMap({
    facilities: [base],
    harbor_fleets: [
      { army: "frigate", ships: 99, mine: true },
      { army: "submarine", ships: null, mine: false }, // 見えた艦隊は隻数なし → 1 隻
    ],
  });
  const all = [...placeShips(map, DEFS, ON).values()].flat();
  assert.equal(all.filter((s) => s.name === "ship/frigate").length, FLEET_SHIP_CAP);
  assert.equal(all.filter((s) => s.name === "ship/submarine").length, 1);
  assert.ok(all.every((s) => s.fleet));
  assert.ok(all.length <= FLEET_CAP);
});

test("艦隊は大きい艦種から割り当てる", () => {
  // 軍港 (0,0) の停泊マスは (1,0)・(0,1)・(1,1) の 3 つだけ。
  // 小さい艦種が先に届いても、割り当ては大きい艦種（輸送船 40px）から
  const map = fakeMap({
    facilities: [{ x: 0, y: 0, kind: "naval_base", built: true }],
    harbor_fleets: [
      { army: "corvette", ships: 2, mine: true },
      { army: "transport", ships: 2, mine: true },
    ],
  });
  const placed = placeShips(map, DEFS, ON);
  const names = [...placed.values()].flat().map((s) => s.name);
  assert.deepEqual(names, ["ship/transport", "ship/transport", "ship/corvette"]);
});

test("軍港がなければ艦隊は出ない。艦隊を消すと艦隊だけ消える", () => {
  const fleets = [{ army: "frigate", ships: 2, mine: true }];
  assert.equal(placeShips(fakeMap({ harbor_fleets: fleets }), DEFS, ON).size, 0);
  const withBase = fakeMap({
    facilities: [{ x: 0, y: 0, kind: "naval_base", built: true }],
    harbor_fleets: fleets,
  });
  assert.equal(placeShips(withBase, DEFS, { merchant: true, fleet: false }).size, 0);
});

test("同じ入力では同じ置き場所になる（決め打ち）", () => {
  const map = fakeMap({
    facilities: [{ x: 0, y: 0, kind: "naval_base", built: true }],
    port_ships: { x: 4, y: 4, count: 4 },
    harbor_fleets: [{ army: "transport", ships: 3, mine: true }],
  });
  assert.deepEqual(placeShips(map, DEFS, ON), placeShips(map, DEFS, ON));
});

// 港と軍港が海マスを分け合う例（掲示板 #6143・#6145・#6147 の割り当て試験）。
// 港 (2,2) と軍港 (4,2) の両方に接する海マスは共有マス (3,2) だけで、港だけに
// 接する海マスは (1,3) だけ。他の隣は全部陸にしてある。本番の座標順では、
// 港の停泊マスは [(3,2), (1,3)] の順になり、共有マスが先頭に来る配置。
const HARBOR_LAND = [
  [2, 2], [4, 2], // 港と軍港のマス
  [3, 1], [3, 3], [1, 1], [2, 1], [1, 2], [2, 3], [4, 1], [4, 3],
].map(([x, y]) => y * 5 + x);
function harborMap(rest) {
  return fakeMap({
    land: HARBOR_LAND,
    facilities: [{ x: 4, y: 2, kind: "naval_base", built: true }],
    port_ships: { x: 2, y: 2, count: 1 },
    harbor_fleets: [{ army: "frigate", ships: 1, mine: true }],
    ...rest,
  });
}
const merchantAt = (map, shown) =>
  [...placeShips(map, DEFS, shown).entries()].find(([, v]) => v[0].name === "ship/merchant")?.[0];

test("競合する共有マスは docked の艦が取り、商船は港専用マスへ流れる", () => {
  const map = harborMap({});
  const placed = placeShips(map, DEFS, ON);
  assert.deepEqual(placed.get("3,2"), [{ name: "ship/frigate", dx: 0, dy: 0, fleet: true }]);
  assert.deepEqual(placed.get("1,3"), [{ name: "ship/merchant", dx: 0, dy: 0, fleet: false }]);
  assert.equal(placed.size, 2);
});

test("艦隊の切り替えで商船の置き場所が移る（オン→オフ→オン。掲示板 #6147）", () => {
  const map = harborMap({});
  // 艦隊オン: 商船は港専用マス (1,3)
  assert.equal(merchantAt(map, { merchant: true, fleet: true }), "1,3");
  // 艦隊オフ: 割り当てを計算し直し、商船は共有マス (3,2) へ移る
  assert.equal(merchantAt(map, { merchant: true, fleet: false }), "3,2");
  // 艦隊オンに戻す: 商船は港専用マス (1,3) へ戻る
  assert.equal(merchantAt(map, { merchant: true, fleet: true }), "1,3");
});

test("共有マスの例: 商船 3 隻・docked 1 隻で、溢れた商船は描かれない（掲示板 #6143）", () => {
  const map = harborMap({ port_ships: { x: 2, y: 2, count: 3 } });
  const both = placeShips(map, DEFS, ON);
  assert.equal(both.size, 2); // 共有マスに軍艦、港専用マスに商船 1 隻、残り 2 隻は省略
  assert.equal(both.get("3,2")[0].name, "ship/frigate");
  assert.equal(both.get("1,3")[0].name, "ship/merchant");
  const noFleet = placeShips(map, DEFS, { merchant: true, fleet: false });
  assert.equal(noFleet.size, 2); // 艦隊オフでは商船 2 隻（共有＋港専用）
  assert.ok([...noFleet.values()].every((v) => v[0].name === "ship/merchant"));
  // 凡例の隻数: データ 3 隻に対して、地図に出たのは両方オンで 1 隻・艦隊オフで 2 隻
  assert.deepEqual(shipCounts(map, DEFS, ON), {
    merchantKnown: true, merchantTotal: 3, merchantPlaced: 1, fleetTotal: 1, fleetPlaced: 1,
  });
  assert.equal(shipCounts(map, DEFS, { merchant: true, fleet: false }).merchantPlaced, 2);
});
