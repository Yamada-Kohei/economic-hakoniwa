// 地図の変遷（グラフィック強化 案29）: 節目のターンの地図の写しと今を見比べる。
// 写しの一覧と名前は地図の API の ?history=1 で読み（history）、昔の地図そのものは
// ?snapshot=<ターン>、変化レンズの材料は ?lens=<ターン> で読む（lens の並び）。
// 種類の決め方はサーバー側（server/web/history.py）にある。ここでは並びをマスの組に
// 直すことと、出すかどうかをブラウザに覚えることだけをする（変わったマスと同じ流儀。
// 覚えていない・読めないときは出さない）。

export const LENS_KEY = "hakoniwa:map-lens";

/** 変化レンズを出すか。覚えていない・読めないときは出さない（既定）。 */
export function readLensShown(storage) {
  try {
    return storage?.getItem(LENS_KEY) === "on";
  } catch {
    return false;
  }
}

/** 出すかどうかを覚える。覚えられなくても表示は切り替える。 */
export function writeLensShown(storage, shown) {
  try {
    storage?.setItem(LENS_KEY, shown ? "on" : "off");
  } catch {
    // プライベートの窓など
  }
}

/** 種類ごとの印の色。凡例（island.html の .lens-swatch）と同じ色にする。 */
export const LENS_COLORS = {
  new: "#2e7d32", // 新しくできた
  down: "#6a1b9a", // 無くなった・衰えた（衰退の印と同じ紫）
  changed: "#00838f", // 姿が変わった（輪郭だけ）
};

/**
 * API の lens（[{x, y, kind}]）を "x,y" → 種類の Map に直す。
 * 知らない種類や壊れた行は飛ばす（印を出さない側に倒す）。
 */
export function lensMarks(map) {
  const marks = new Map();
  for (const row of map?.lens || []) {
    if (!row || !(row.kind in LENS_COLORS)) continue;
    if (!Number.isInteger(row.x) || !Number.isInteger(row.y)) continue;
    marks.set(`${row.x},${row.y}`, row.kind);
  }
  return marks;
}

// 記憶の刻印（案35）: 災害や戦いの跡を、そのマスに小さな石の印で出す。
// 一覧は ?history=1 の engravings で読む。出すかどうかはレンズと同じく覚える。

export const ENGRAVINGS_KEY = "hakoniwa:map-engravings";

/** 刻印を出すか。覚えていない・読めないときは出さない（既定）。 */
export function readEngravingsShown(storage) {
  try {
    return storage?.getItem(ENGRAVINGS_KEY) === "on";
  } catch {
    return false;
  }
}

/** 出すかどうかを覚える。覚えられなくても表示は切り替える。 */
export function writeEngravingsShown(storage, shown) {
  try {
    storage?.setItem(ENGRAVINGS_KEY, shown ? "on" : "off");
  } catch {
    // プライベートの窓など
  }
}

/** 刻印の種類ごとの石の色。凡例（island.html の .engraving-swatch）と同じ色にする。 */
export const ENGRAVING_COLORS = {
  disaster: "#616161", // 災害の跡（石の灰色）
  scar: "#6d4c41", // 戦い・騒ぎの跡（焦げた茶色）
};

/**
 * API の engravings（[{x, y, mark}]）を "x,y" → 種類の Map に直す。
 * 知らない種類や壊れた行は飛ばす。同じマスに複数あれば後の物（新しい出来事）を採る。
 */
export function engravingMarks(rows) {
  const marks = new Map();
  for (const row of rows || []) {
    if (!row || !(row.mark in ENGRAVING_COLORS)) continue;
    if (!Number.isInteger(row.x) || !Number.isInteger(row.y)) continue;
    marks.set(`${row.x},${row.y}`, row.mark);
  }
  return marks;
}
