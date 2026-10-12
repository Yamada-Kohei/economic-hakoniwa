// 電力: 住民が受け取れた電力の割合（足りていれば緑、停電は赤）。送電線のマスは青。
import { goodBad } from "./scale.js";

export default {
  id: "power",
  label: "電力充足率",
  color(map, i) {
    if (map.population[i]) return goodBad((map.power[i] ?? 0) / 1000);
    if (map.power_line[i]) return "hsla(210, 90%, 55%, 0.45)";
    return null;
  },
};
