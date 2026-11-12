// 運賃の率: 陸のマスごとに、拠点（首都と大きな町）まで運ぶときの運賃の率。高いマスほど濃い赤。
// 計算が重いので、選んだときだけ地図の API に ?freight=1 を付けて読む（query と fields）。
// 率の上限（freight_max）に比べるとどのマスも薄くなるので、この島でいちばん高いマスを最も濃くする。
// 棒の高さも同じ物差しで、率に比例させる（UI-79）。
import { heat } from "./scale.js";

/** 拠点まで運べるマスのうち、いちばん高い率（初めて使うときに求めておく）。 */
function topRate(map) {
  if (map._freightTop == null) {
    // 上限に届かない（拠点まで運べる）マスのうちいちばん高い率
    const max = map.freight_max || Infinity;
    map._freightTop = Math.max(1, ...map.freight.filter((r) => r != null && r < max));
  }
  return map._freightTop;
}

export default {
  id: "freight",
  label: "運賃の率",
  legend: "拠点（首都と大きな町）まで運ぶときの運賃の率です。赤が濃いマスほど高く、この島でいちばん高いマスを最も濃くしています。",
  scale: "棒の高さは運賃の率に比例し、この島でいちばん高いマスを最も高くしています。",
  query: "freight=1",
  fields: ["freight", "freight_max"],
  color(map, i) {
    const rate = map.freight?.[i];
    if (rate == null) return null;
    return heat(rate / topRate(map), 0);
  },
  height(map, i) {
    const rate = map.freight?.[i];
    if (rate == null) return null;
    return Math.min(1, rate / topRate(map));
  },
};
