// 地図に重ねて表示する情報の一覧。新しい重ね方は、このフォルダにファイルを1つ足してここに並べる。
// 各モジュールは { id, label, color(map, i, defs) } を持つ。color は色の文字列か null（塗らない）。
// legend（選んだときに出す説明）、query と fields（選んだときだけ地図の API に query を付けて読み、
// その fields を地図のデータに足す）も持てる。

import freight from "./freight.js";
import happiness from "./happiness.js";
import { infraOverlays } from "./infra.js";
import pollution from "./pollution.js";
import population from "./population.js";
import power from "./power.js";
import works from "./works.js";
import zone from "./zone.js";

export const overlays = [population, happiness, power, pollution, zone];

/** 定義データから作る重ね方（インフラの種類ごと）も含めた一覧。 */
export function overlayList(defs) {
  return [...overlays, ...infraOverlays(defs), freight, works];
}
