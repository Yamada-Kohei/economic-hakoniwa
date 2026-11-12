// 汚染: 汚れたマスほど濃い茶色。棒の高さは汚染度に比例し、色と同じく 10 で最も高くする（UI-79）。
import { heat } from "./scale.js";

export default {
  id: "pollution",
  label: "汚染度",
  scale: "棒の高さは汚染度に比例し、10 以上で最も高くなります。",
  color(map, i) {
    const p = map.pollution[i];
    if (!p) return null;
    return heat(p / 10, 30);
  },
  height(map, i) {
    const p = map.pollution[i];
    if (!p) return null;
    return Math.min(1, p / 10);
  },
};
