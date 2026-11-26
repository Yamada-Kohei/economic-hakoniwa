// 港の商船（案6）と母港の艦隊（W-100）: 表示の切り替えと、海マスへの置き場所決め。
// 描画そのものは renderer.js（drawShips）。置き場所は座標だけで決める（乱数は使わない）。
// 商船と艦隊は描画を共用し、凡例と切替は分ける（掲示板 #5806 の決め）。
//
// 置き方の決め（掲示板 #6140・#6144）:
// - 1 海マスに 1 隻だけ置く。割り当ては docked の艦（実データで確定している隻数）を
//   先にし、商船はそのあと（確定している方を、換算の入る商船で隠さない）。
// - 溢れた分は、その施設に接する海マスへ座標順に 1 隻ずつ流す。接する海マスが
//   尽きたら描くのはそこまで（凡例に「空きマスが足りない船は省略」と添える）。
// - 艦隊の複数艦種は、大きい艦種から順に割り当てる。
// - 表示の切り替えのたびに割り当ては計算し直す（main.js が描き直しのたびに呼ぶ）。

export const MERCHANT_KEY = "hakoniwa:map-ships-merchant";
export const FLEET_KEY = "hakoniwa:map-ships-fleet";

function readShown(storage, key) {
  try {
    return storage?.getItem(key) !== "off";
  } catch {
    return true;
  }
}

function writeShown(storage, key, shown) {
  try {
    storage?.setItem(key, shown ? "on" : "off");
  } catch {
    // プライベートの窓など
  }
}

/** 商船を出すか。覚えていなければ出す。 */
export function readMerchantShown(storage) {
  return readShown(storage, MERCHANT_KEY);
}

export function writeMerchantShown(storage, shown) {
  writeShown(storage, MERCHANT_KEY, shown);
}

/** 艦隊を出すか。覚えていなければ出す。 */
export function readFleetShown(storage) {
  return readShown(storage, FLEET_KEY);
}

export function writeFleetShown(storage, shown) {
  writeShown(storage, FLEET_KEY, shown);
}

export const MERCHANT_CAP = 6; // 商船を描く隻数の上限
export const FLEET_SHIP_CAP = 4; // 1 つの艦隊で描く隻数の上限
export const FLEET_CAP = 12; // 艦隊の船を描く合計の上限

// 艦種の大きさ（絵の船体の長さ px。tools/sprites/ship_sprites.py の置き方）。
// 艦隊は大きい艦種から順にマスへ割り当てる（掲示板 #6140）
const SHIP_LENGTH = { transport: 40, frigate: 40, submarine: 36, corvette: 34 };

// 施設に隣り合うマスを探す向き（8 近傍）。返し方は座標順に並べ直す
const BERTH_DIRS = [[1, 0], [0, 1], [1, 1], [-1, 0], [0, -1], [1, -1], [-1, 1], [-1, -1]];

/** 施設 (fx, fy) に隣り合う海マスを、座標順（行・列の順）で返す。 */
export function berthCells(map, defs, fx, fy) {
  const n = map.size;
  const out = [];
  for (const [dx, dy] of BERTH_DIRS) {
    const x = fx + dx;
    const y = fy + dy;
    if (x < 0 || y < 0 || x >= n || y >= n) continue;
    if (defs.terrain[map.terrain[y * n + x]]?.land) continue;
    out.push({ x, y });
  }
  out.sort((a, b) => a.y - b.y || a.x - b.x);
  return out;
}

/**
 * 停泊させる船の割り当てを決める。placed は "x,y" → [{ name, dx, dy, fleet }] の Map
 * （1 マス 1 隻なので中身は常に 1 つ）。凡例に出す隻数の集計も一緒に返す。
 * データは地図の API の port_ships（港にいる商船の隻数）と
 * harbor_fleets（母港に繋留中の艦隊）。
 */
function plan(map, defs, shown) {
  const placed = new Map();
  const used = new Set();
  const put = (cells, name, fleet) => {
    for (const c of cells) {
      const k = `${c.x},${c.y}`;
      if (used.has(k)) continue;
      used.add(k);
      placed.set(k, [{ name, dx: 0, dy: 0, fleet }]);
      return true;
    }
    return false;
  };

  // 艦隊が先（docked は実データで確定）。艦隊ごとに母港（軍港）へ割り当て、
  // 軍港の数より艦隊が多いときは、残りを全部の軍港の停泊マスの寄せ集めへ流す
  let fleetTotal = 0;
  let fleetPlaced = 0;
  if (shown.fleet && (map.harbor_fleets || []).length) {
    const bases = (map.facilities || []).filter((f) => f.kind === "naval_base" && f.built);
    const baseCells = bases.map((f) => berthCells(map, defs, f.x, f.y));
    const pooled = [...new Map(baseCells.flat().map((c) => [`${c.x},${c.y}`, c])).values()].sort(
      (a, b) => a.y - b.y || a.x - b.x,
    );
    const fleets = [...map.harbor_fleets].sort(
      (a, b) => (SHIP_LENGTH[b.army] ?? 0) - (SHIP_LENGTH[a.army] ?? 0),
    );
    fleets.forEach((f, i) => {
      const cells = baseCells[i] ?? pooled;
      const count = Math.min(f.ships ?? 1, FLEET_SHIP_CAP);
      for (let j = 0; j < count && fleetTotal < FLEET_CAP; j++) {
        fleetTotal++;
        if (put(cells, `ship/${f.army}`, true)) fleetPlaced++;
      }
    });
  }

  // 商船はあと。港に接する海マスへ座標順に 1 隻ずつ、尽きたらそこまで
  const merchantKnown = Boolean(map.port_ships);
  let merchantTotal = 0;
  let merchantPlaced = 0;
  if (shown.merchant && map.port_ships && map.port_ships.count > 0) {
    merchantTotal = map.port_ships.count;
    const cells = berthCells(map, defs, map.port_ships.x, map.port_ships.y);
    const total = Math.min(merchantTotal, MERCHANT_CAP);
    for (let i = 0; i < total; i++) {
      if (put(cells, "ship/merchant", false)) merchantPlaced++;
    }
  }
  return { placed, merchantKnown, merchantTotal, merchantPlaced, fleetTotal, fleetPlaced };
}

/** 停泊中の船の置き場所。"x,y" → [{ name, dx, dy, fleet }] の Map。 */
export function placeShips(map, defs, shown) {
  return plan(map, defs, shown).placed;
}

/** 凡例に出す隻数（データの隻数と、地図に描けた隻数）。 */
export function shipCounts(map, defs, shown) {
  const { merchantKnown, merchantTotal, merchantPlaced, fleetTotal, fleetPlaced } = plan(
    map,
    defs,
    shown,
  );
  return { merchantKnown, merchantTotal, merchantPlaced, fleetTotal, fleetPlaced };
}
