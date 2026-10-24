import { getJson } from "./api.js";

// 既存の要約が返す前後の表示値を使う。変化の大きさや原因は推定しない。
export async function mountRecentChanges() {
  const root = document.getElementById("recent-changes");
  if (!root) return;
  const routes = { unemployment: "facilities", "food.ratio": "market", "power.capacity": "facilities", "power.demand": "facilities", happiness: "economy", gdp: "economy" };
  try {
    const data = await getJson(root.dataset.url);
    const changed = Object.keys(routes).flatMap((key) => data.indicators.filter((r) => r.key === key && r.before != null)).slice(0, 3);
    for (const row of changed) {
      const li = document.createElement("li");
      const link = document.createElement("a");
      link.href = `/islands/${data.island.id}/${routes[row.key]}/`;
      link.textContent = `${row.label}: ${row.before} → ${row.now}（詳しく調べる）`;
      li.append(link);
      root.querySelector("ul").append(li);
    }
    root.hidden = !changed.length;
  } catch {
    root.hidden = false;
    root.querySelector("ul").textContent = "前のターンとの比較を読み込めませんでした。各画面で確認してください。";
  }
}
