// 人口: 住民の多いマスほど濃い紫。
import { heat } from "./scale.js";

export default {
  id: "population",
  label: "人口",
  color(map, i) {
    const p = map.population[i];
    if (!p) return null;
    return heat(Math.log10(p) / Math.log10(30000), 280);
  },
};
