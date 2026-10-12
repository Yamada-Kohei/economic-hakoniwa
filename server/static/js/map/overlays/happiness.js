// 幸福度: 低いマスは赤、高いマスは緑。
import { goodBad } from "./scale.js";

export default {
  id: "happiness",
  label: "幸福度",
  color(map, i) {
    const h = map.happiness[i];
    if (h === null || h === undefined) return null;
    return goodBad(h / 100);
  },
};
