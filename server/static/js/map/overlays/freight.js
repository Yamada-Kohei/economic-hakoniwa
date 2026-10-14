// 運賃の率: 陸のマスごとに、拠点（首都と大きな町）まで運ぶときの運賃の率。高いマスほど濃い赤。
// 計算が重いので、選んだときだけ地図の API に ?freight=1 を付けて読む（query と fields）。
// 率の上限（freight_max）に比べるとどのマスも薄くなるので、この島でいちばん高いマスを最も濃くする。
import { heat } from "./scale.js";

export default {
  id: "freight",
  label: "運賃の率",
  legend: "拠点（首都と大きな町）まで運ぶときの運賃の率です。赤が濃いマスほど高く、この島でいちばん高いマスを最も濃くしています。",
  query: "freight=1",
  fields: ["freight", "freight_max"],
  color(map, i) {
    const rate = map.freight?.[i];
    if (rate == null) return null;
    if (map._freightTop == null) {
      // 初めて塗るときに、上限に届かない（拠点まで運べる）マスのうちいちばん高い率を求めておく
      const max = map.freight_max || Infinity;
      map._freightTop = Math.max(1, ...map.freight.filter((r) => r != null && r < max));
    }
    return heat(rate / map._freightTop, 0);
  },
};
