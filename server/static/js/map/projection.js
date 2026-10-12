// 座標の変換: マス (x, y) と画面上の位置を行き来する。
// マスはひし形に並べる。x が増えると右下へ、y が増えると左下へ進む。

/** マス (x, y) のひし形の上の頂点の位置（地面の高さ 0、拡大前）。 */
export function tileToWorld(x, y, theme) {
  return {
    px: (x - y) * (theme.tile_width / 2),
    py: (x + y) * (theme.tile_height / 2),
  };
}

/** 地面の高さを持ち上げたぶんのずれ（ピクセル）。 */
export function lift(height, theme) {
  return height * theme.step_height;
}

/** 点 (px, py) が、上の頂点 (tx, ty) のひし形の中にあるか。 */
export function inDiamond(px, py, tx, ty, theme) {
  const hw = theme.tile_width / 2;
  const hh = theme.tile_height / 2;
  const dx = Math.abs(px - tx) / hw;
  const dy = Math.abs(py - (ty + hh)) / hh;
  return dx + dy <= 1;
}

/**
 * 拡大前の位置 (px, py) にあるマスを探す。高いマスが手前のマスを隠すので、
 * 手前（x + y の大きい方）から順に、持ち上げたひし形に入るかを調べる。
 */
export function worldToTile(px, py, map, heightOf, theme) {
  const n = map.size;
  for (let s = 2 * (n - 1); s >= 0; s--) {
    for (let x = Math.min(s, n - 1); x >= 0 && s - x < n; x--) {
      const y = s - x;
      const { px: tx, py: ty } = tileToWorld(x, y, theme);
      const h = lift(heightOf(x, y), theme);
      if (inDiamond(px, py, tx, ty - h, theme)) return { x, y };
    }
  }
  return null;
}
