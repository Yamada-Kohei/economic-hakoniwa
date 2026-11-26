// 国のカードの画面: 材料（card.py）を読み、書き出し基盤（export.js）で
// 地図を描いてカードに合成し、プレビューと PNG の保存を用意する。

import { getJson } from "../island/api.js";
import { downloadCanvas, drawCountryCard, renderMapCanvas } from "./export.js";
import { loadSprites, spritePriorityKeys } from "./sprites.js";

const data = JSON.parse(document.getElementById("card-data").textContent);
const img = document.getElementById("card-img");
const failed = document.getElementById("card-failed");
const styleSelect = document.getElementById("card-style");
const downloadButton = document.getElementById("card-download");

let cardCanvas = null;

async function build() {
  try {
    const [defs, map] = await Promise.all([getJson(data.defs_url), getJson(data.map_url)]);
    const sprites = await loadSprites(defs.theme, { priority: spritePriorityKeys(defs.theme, map, defs) });
    const flagImg = await new Promise((resolve) => {
      const image = new Image();
      image.onload = () => resolve(image);
      image.onerror = () => resolve(null);
      image.src = data.flag_url;
    });
    const flag = flagImg ? { img: flagImg, w: flagImg.naturalWidth, h: flagImg.naturalHeight } : null;
    const mapCanvas = renderMapCanvas(map, defs, {
      sprites,
      flag,
      season: styleSelect.value === "season",
    });
    cardCanvas = drawCountryCard(data, mapCanvas, flagImg);
    img.src = cardCanvas.toDataURL("image/png");
  } catch {
    failed.hidden = false;
  }
}

styleSelect.addEventListener("change", build);
downloadButton.addEventListener("click", () => {
  if (cardCanvas) downloadCanvas(cardCanvas, "country-card.png");
});
build();
