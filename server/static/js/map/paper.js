// ターンの新聞の地図の切り抜き: 書き出し基盤（export.js）で今の地図を描く。
// 地図を出してよいのはいちばん新しいターンの新聞だけ（paper.py が判定し、
// この置き場所自体がそのときだけテンプレートに出る）。

import { getJson } from "../island/api.js";
import { renderMapCanvas } from "./export.js";
import { loadFlag, loadSprites, spritePriorityKeys } from "./sprites.js";

const box = document.getElementById("paper-map");
if (box) {
  try {
    const [defs, map] = await Promise.all([getJson(box.dataset.defs), getJson(box.dataset.api)]);
    const sprites = await loadSprites(defs.theme, {
      priority: spritePriorityKeys(defs.theme, map, defs),
    });
    const flag = await loadFlag(box.dataset.flag);
    box.appendChild(renderMapCanvas(map, defs, { sprites, flag }));
  } catch {
    box.remove();
  }
}
