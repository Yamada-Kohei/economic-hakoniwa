// 人口: 住民の多いマスほど濃い紫。棒の高さは人口の平方根で、この島でいちばん多いマスを最も高くする
// （大都市と村が並んでも村の棒が見えるように。UI-79）。
import { heat } from "./scale.js";

export default {
  id: "population",
  label: "人口",
  scale: "棒の高さは人口の平方根で、この島でいちばん人の多いマスを最も高くしています（人口が 4 倍で高さが 2 倍）。",
  color(map, i) {
    const p = map.population[i];
    if (!p) return null;
    return heat(Math.log10(p) / Math.log10(30000), 280);
  },
  height(map, i) {
    const p = map.population[i];
    if (!p) return null;
    // 初めて測るときに、この島でいちばん多いマスの人口を求めておく
    if (map._populationTop == null) map._populationTop = Math.max(1, ...map.population.map((v) => v || 0));
    return Math.sqrt(p / map._populationTop);
  },
};
