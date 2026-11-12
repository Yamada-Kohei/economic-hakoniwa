// 重ね表示の棒（UI-79）: マスの上に半透明の角柱を立てる。高さの物差しは重ね表示ごとに height と
// scale（凡例に書く物差しの説明）で決める。height のない重ね表示（区域・工事中）は量がないので塗り分けのまま。
// 表し方（棒か塗り分けか）はブラウザに覚える。描くのは renderer.js、ここは描く順と高さと文の決まりだけ。

export const OVERLAY_MODE_KEY = "hakoniwa:map-overlay-mode";
export const BARS = "bars";
export const FILL = "fill";

/** 棒のいちばん高いとき、マスの高さの何倍か。 */
export const BAR_TILES = 3;
/** 量が小さくても見えるようにする棒の最低の高さ（ピクセル）。 */
const BAR_MIN = 2;

/** 覚えた表し方。覚えていない・読めないときは棒（既定）。 */
export function readOverlayMode(storage) {
  try {
    return storage?.getItem(OVERLAY_MODE_KEY) === FILL ? FILL : BARS;
  } catch {
    return BARS;
  }
}

/** 表し方を覚える。覚えられない（プライベートの窓など）ときは毎回棒に戻る。 */
export function writeOverlayMode(storage, mode) {
  try {
    storage?.setItem(OVERLAY_MODE_KEY, mode === FILL ? FILL : BARS);
  } catch {
    // 覚えられなくても表示は切り替える
  }
}

/** この重ね表示を棒で描くか。高さの物差しのない重ね表示は塗り分けのまま。 */
export function drawsBars(overlay, mode) {
  return Boolean(overlay?.height) && mode === BARS;
}

/** 凡例: 重ね表示の説明に、棒のときは高さの物差しを添える。 */
export function overlayLegend(overlay, mode) {
  if (!overlay) return "";
  const parts = [overlay.legend || ""];
  if (mode === BARS) {
    parts.push(overlay.height ? overlay.scale || "" : "量のない表示なので、棒にせず塗り分けで示します。");
  }
  return parts.filter(Boolean).join(" ");
}

/** 0〜1 の高さを棒のピクセルにする。0 や値なしは棒を立てない。 */
export function barPixels(value, theme) {
  if (!(value > 0)) return 0;
  return Math.max(BAR_MIN, Math.min(1, value) * theme.tile_height * BAR_TILES);
}

/**
 * 棒を立てるマスを、奥から手前へ（x + y の小さい順、同じ並びは x の小さい順に）並べる。
 * 手前の棒があとに描かれ、半透明なので奥の棒も透けて見える。
 * 返すのは { x, y, i, value } の列（value は 0〜1 にそろえた高さ）。
 */
export function barsBackToFront(map, overlay, defs) {
  const n = map.size;
  const bars = [];
  for (let s = 0; s <= 2 * (n - 1); s++) {
    for (let x = Math.max(0, s - n + 1); x <= Math.min(s, n - 1); x++) {
      const y = s - x;
      const i = y * n + x;
      const value = overlay.height(map, i, defs);
      if (value > 0) bars.push({ x, y, i, value: Math.min(1, value) });
    }
  }
  return bars;
}

/** "hsla(…, a)"・"rgba(…, a)" の透明度を外した色（棒の透け具合は描く側で決める）。 */
export function solidColor(color) {
  const m = /^(hsl|rgb)a\((.*),\s*[\d.]+\)$/.exec(color || "");
  return m ? `${m[1]}(${m[2]})` : color;
}
