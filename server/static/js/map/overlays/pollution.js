// 汚染: 汚れたマスほど濃い茶色。
import { heat } from "./scale.js";

export default {
  id: "pollution",
  label: "汚染度",
  color(map, i) {
    const p = map.pollution[i];
    if (!p) return null;
    return heat(p / 10, 30);
  },
};
