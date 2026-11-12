// 幸福度: 低いマスは赤、高いマスは緑。棒の高さは幸福度に比例する（UI-79）。
import { goodBad } from "./scale.js";

export default {
  id: "happiness",
  label: "幸福度",
  scale: "棒の高さは幸福度に比例し、100 で最も高くなります。",
  color(map, i) {
    const h = map.happiness[i];
    if (h === null || h === undefined) return null;
    return goodBad(h / 100);
  },
  height(map, i) {
    const h = map.happiness[i];
    if (h === null || h === undefined) return null;
    return h / 100;
  },
};
