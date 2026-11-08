import { getJson } from "./api.js";

// サーバーが目安の幅で選んだ変化を、同じ表示値と行き先で並べる。
export async function mountRecentChanges() {
  const root = document.getElementById("recent-changes");
  if (!root) return;
  try {
    const data = await getJson(root.dataset.url);
    const changed = data.notable_changes;
    const list = root.querySelector("ul");
    list.replaceChildren();
    for (const row of changed) {
      const li = document.createElement("li");
      const link = document.createElement("a");
      link.href = `/islands/${data.island.id}/${row.page}/`;
      link.textContent = `${row.label}: ${row.before} → ${row.now}（詳しく調べる）`;
      li.append(link);
      list.append(li);
    }
    if (!changed.length) {
      const li = document.createElement("li");
      li.textContent = "目安を超えた変化はありません";
      list.append(li);
    }
    root.hidden = false;
  } catch {
    root.hidden = false;
    root.querySelector("ul").textContent = "前のターンとの比較を読み込めませんでした。各画面で確認してください。";
  }
}
