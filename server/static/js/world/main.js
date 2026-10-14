// 世界マップ: 区画ごとに、島の形を1マス1ピクセルで描く。その上に貿易圏・港・航路を重ねる（フェーズ3-1）。

import { drawAllIslands } from "./pixels.js";
import { drawTrade } from "./trade.js";
import { getJson } from "../island/api.js";

const grid = document.getElementById("world-grid");
const [defs, shapes] = await Promise.all([getJson(grid.dataset.defs), getJson(grid.dataset.shapes)]);
drawAllIslands(grid, shapes, defs);
try {
  drawTrade(grid, await getJson(grid.dataset.trade), document.getElementById("world-trade"));
} catch {
  // 貿易を読めなくても、島の形はそのまま見せる
}
