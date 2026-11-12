// インフラの整備度: 種類ごと（定義の infrastructure から作る）に、整備度の高いマスほど濃く塗る。
// 棒の高さは整備度に比例する（UI-79）。
import { heat } from "./scale.js";

// 種類ごとの色相（定義の順に使う。道路・鉄道・上下水道・送電網）
const HUES = [25, 300, 200, 50, 120, 0];

/** 定義のインフラの種類ごとの重ね表示。 */
export function infraOverlays(defs) {
  return Object.entries(defs.infrastructure || {}).map(([kind, d], k) => ({
    id: `infra:${kind}`,
    label: `${d.name}の整備度`,
    legend:
      `色が濃いほど${d.name}の整備度が高いマスです。` +
      (d.role === "power" ? `${Math.round((d.grid ?? 0) * 100)}% 以上のマスが電力を通します。` : ""),
    scale: "棒の高さは整備度に比例し、100% で最も高くなります。",
    color(map, i) {
      const level = map.infra?.[kind]?.[i];
      if (!level) return null;
      return heat(level / 100, HUES[k % HUES.length]);
    },
    height(map, i) {
      const level = map.infra?.[kind]?.[i];
      if (!level) return null;
      return Math.min(1, level / 100);
    },
  }));
}
