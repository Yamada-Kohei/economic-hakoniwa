// 電力: 住民が受け取れた電力の割合（足りていれば緑、停電は赤）。送電線のマスは青。
// 棒は足りない分だけ: 住民に届かなかった割合を高さにし、足りているマスには立てない（UI-79）。
// 割合はマスの窓と同じく 1% 単位に丸めて見る（窓に 100% と出るマスに棒を立てない）。
import { goodBad } from "./scale.js";

export default {
  id: "power",
  label: "電力充足率",
  scale: "棒の高さは住民に届かなかった電力の割合（足りない分）で、まったく届かないマスが最も高くなります。足りているマスには棒を立てません。",
  color(map, i) {
    if (map.population[i]) return goodBad((map.power[i] ?? 0) / 1000);
    if (map.power_line[i]) return "hsla(210, 90%, 55%, 0.45)";
    return null;
  },
  height(map, i) {
    const received = map.power?.[i];
    if (!map.population[i] || received == null) return null;
    const short = 100 - Math.round(received / 10);
    return short > 0 ? short / 100 : null;
  },
};
