// 世界マップ: 区画ごとに、島の形を1マス1ピクセルで描く。

import { drawAllIslands } from "./pixels.js";
import { getJson } from "../island/api.js";

const grid = document.getElementById("world-grid");
const [defs, shapes] = await Promise.all([getJson(grid.dataset.defs), getJson(grid.dataset.shapes)]);
drawAllIslands(grid, shapes, defs);
