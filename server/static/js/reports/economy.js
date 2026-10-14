// 経済の画面: 主な指標のグラフと、統計を選んで見るグラフ。

import { mountCharts } from "./common.js";

const group = mountCharts();
const choice = document.getElementById("stat-choice");
const free = document.getElementById("free-chart");

if (group && choice && free) {
  // 選ぶ欄は、グラフの並びの最後の枠に置く（選ぶまでは空の枠）
  free.hidden = false;
  const item = group.add(free);
  choice.addEventListener("change", () => {
    free.dataset.names = choice.value;
    free.dataset.label = choice.selectedOptions[0]?.textContent || "";
    if (!choice.value) {
      free.querySelector(".line-chart")?.remove();
      item.chart = null;
      return;
    }
    group.load();
  });
}
