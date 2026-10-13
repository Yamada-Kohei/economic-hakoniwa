// 地図に重ねて表示する情報の一覧。新しい重ね方は、このフォルダにファイルを1つ足してここに並べる。
// 各モジュールは { id, label, color(map, i, defs) } を持つ。color は色の文字列か null（塗らない）。

import happiness from "./happiness.js";
import pollution from "./pollution.js";
import population from "./population.js";
import power from "./power.js";
import zone from "./zone.js";

export const overlays = [population, happiness, power, pollution, zone];
