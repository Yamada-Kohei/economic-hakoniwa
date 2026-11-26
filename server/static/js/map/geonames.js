// 地名（案34）: 山・湾・岬の名前は、地図を拡大したときだけ出す（hal の条件）。
// 材料はサーバーが地形から決める（web/geonames.py）。ここは出す境目と、
// 材料の並びの確かめだけを持つ。名前そのものは renderer.js が描く。

/** ラベルを出す拡大の境目（anim.js の detailLevel で「near」になる 0.9 と同じ）。 */
export const GEO_LABEL_MIN_SCALE = 0.9;

export function geoLabelsShown(scale) {
  return typeof scale === "number" && scale >= GEO_LABEL_MIN_SCALE;
}

/** API の geonames の並び → 描画に使える行だけを残す。 */
export function geoNameRows(rows) {
  if (!Array.isArray(rows)) return [];
  const out = [];
  for (const r of rows) {
    if (!r || !Number.isInteger(r.x) || !Number.isInteger(r.y)) continue;
    if (typeof r.name !== "string" || !r.name) continue;
    out.push({ x: r.x, y: r.y, name: r.name });
  }
  return out;
}
