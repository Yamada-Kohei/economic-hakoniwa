// 世界マップ: 区画ごとに、島の形を1マス1ピクセルで描く。その上に貿易圏・港・航路を重ねる（フェーズ3-1）。

import { drawAllIslands } from "./pixels.js";
import { drawFleets } from "./fleets.js";
import { drawTrade } from "./trade.js";
import { loadSprites } from "../map/sprites.js";
import { getJson } from "../island/api.js";
import { mountFleetCommands } from "./military.js";

const grid = document.getElementById("world-grid");
const defsReady = getJson(grid.dataset.defs);
const fleetsReady = getJson(grid.dataset.fleets);
// 艦隊の操作は、島の絵や航路やスプライトの読み込みに依存させない。
mountFleetCommands(grid, defsReady, fleetsReady);
// 艦隊の絵を描くまでに失敗が返っても、未処理の拒否にしない。
fleetsReady.catch(() => {});
const [defs, shapes] = await Promise.all([defsReady, getJson(grid.dataset.shapes)]);
drawAllIslands(grid, shapes, defs);
try {
  drawTrade(grid, await getJson(grid.dataset.trade), document.getElementById("world-trade"));
} catch {
  // 貿易を読めなくても、島の形はそのまま見せる
}
try {
  const [fleets, sprites] = await Promise.all([fleetsReady, loadSprites(defs.theme)]);
  drawFleets(grid, fleets, sprites);
} catch {
  // 艦隊を読めなくても、島の形と貿易はそのまま見せる
}
