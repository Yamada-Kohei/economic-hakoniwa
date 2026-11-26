// 道路・鉄道の線（案2。合意形は掲示板 #5688・#5690）: 表示の切り替えと、線の引き方決め。
// 描画そのものは renderer.js（drawTransportLine）。引き方は座標と整備度だけで決める（乱数なし）。
//
// 描き方の決め:
// - 道路・鉄道はマスごとの整備度（map.infra の種類別の並び、%）しかデータが無い。
//   送電網のような「つながっている」という記録は無いので、線は破線にして、
//   実際に通っているようには見せない（#5690）。
// - 隣のマスにも同じ種類の整備があれば、そちらへ向けて線をつなぐ。無いマスは
//   マスの中に短い線だけを引く（整備した場所の印）。「つながり」と「整備した場所」を
//   混同しないための 2 段構え（#5688）。
// - 線の太さと濃さは整備度で変える。鉄道は横木（枕木）の印を足して道路と見分ける。

export const ROAD_KEY = "hakoniwa:map-road-lines";
export const RAIL_KEY = "hakoniwa:map-rail-lines";

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

/** 道路の線を出すか。覚えていなければ出す。 */
export function readRoadShown(storage) {
  return readShown(storage, ROAD_KEY);
}

export function writeRoadShown(storage, shown) {
  writeShown(storage, ROAD_KEY, shown);
}

/** 鉄道の線を出すか。覚えていなければ出す。 */
export function readRailShown(storage) {
  return readShown(storage, RAIL_KEY);
}

export function writeRailShown(storage, shown) {
  writeShown(storage, RAIL_KEY, shown);
}

// 線をつなぐ向き（上下左右。斜めはつながない）
const LINK_DIRS = [[1, 0], [-1, 0], [0, 1], [0, -1]];

/**
 * 種類 kind の線の引き方を決める。マスの番号（y * size + x）→
 * { level, links, stub } の Map を返す。level は整備度（%）、links は同じ種類の
 * 整備がある隣への向き [dx, dy] の並び、stub はつなぐ隣が無い（短い線だけ）か。
 * 整備度 0 のマスと、データの無い種類は Map に入らない。
 */
export function transportSegments(map, kind) {
  const out = new Map();
  const levels = map.infra?.[kind];
  if (!levels) return out;
  const n = map.size;
  for (let y = 0; y < n; y++) {
    for (let x = 0; x < n; x++) {
      const i = y * n + x;
      const level = levels[i] || 0;
      if (!level) continue;
      const links = [];
      for (const [dx, dy] of LINK_DIRS) {
        const nx = x + dx;
        const ny = y + dy;
        if (nx < 0 || ny < 0 || nx >= n || ny >= n) continue;
        if (levels[ny * n + nx]) links.push([dx, dy]);
      }
      out.set(i, { level, links, stub: links.length === 0 });
    }
  }
  return out;
}
