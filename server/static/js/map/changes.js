// 前のターンから変わったマスの印（グラフィック強化 案3 の静止版＋案10 の衰退の印）。
// 変わったマスに静止した枠と種類のアイコン（建設・撤去・段階・衰退）を重ねる。初回表示で
// 一度だけ光らせる演出は第 2 波で、描画の基盤と一緒に足す。
// 衰退（decline）のマスは、印に加えて家の絵そのものも暗く・くすませて描く
// （renderer.js の darkenedSprite。新しい絵は描かず既存の絵に効果をかける）。
// それも含めて、出さないのが既定で、この切り替えを入れたときだけ見える。
// 印の材料は地図の API の ?changes=1 で初めて切り替えたときに読み（changed の並び）、
// 種類の決め方はサーバー側（server/web/changes.py の対応表）にある。ここでは並びを
// マスの組に直すだけにする。出しているかどうかはブラウザに覚える（島によらず1つ。
// 覚えられない窓では毎回出さない）。

export const CHANGES_KEY = "hakoniwa:map-changes";

/** 変わったマスの印を出すか。覚えていない・読めないときは出さない（既定）。 */
export function readChangesShown(storage) {
  try {
    return storage?.getItem(CHANGES_KEY) === "on";
  } catch {
    return false;
  }
}

/** 出すかどうかを覚える。覚えられなくても表示は切り替える。 */
export function writeChangesShown(storage, shown) {
  try {
    storage?.setItem(CHANGES_KEY, shown ? "on" : "off");
  } catch {
    // プライベートの窓など
  }
}

/** 種類ごとの枠とアイコンの色。凡例（island.html の .change-swatch）と同じ色にする。 */
export const CHANGE_COLORS = {
  built: "#2e7d32", // 建設: 建った
  removed: "#c62828", // 撤去: 無くなった
  stage: "#ef6c00", // 段階: 姿が変わった
  decline: "#6a1b9a", // 衰退: 住宅の段階が下がった（案10 の第一段）
};

/**
 * API の changed（[{x, y, kind}]）を "x,y" → 種類の Map に直す。
 * 知らない種類や壊れた行は飛ばす（印を出さない側に倒す）。
 */
export function changeMarks(map) {
  const marks = new Map();
  for (const row of map?.changed || []) {
    if (!row || !(row.kind in CHANGE_COLORS)) continue;
    if (!Number.isInteger(row.x) || !Number.isInteger(row.y)) continue;
    marks.set(`${row.x},${row.y}`, row.kind);
  }
  return marks;
}
