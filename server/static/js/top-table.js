// トップの島の一覧の表: 列の名前を押すと並べ替える（もう一度押すと逆の順）。値のない島はいつも最後。
// 順位の列は、そのときの並びの順に振り直す。並べ替えはどの画面の表とも同じ部品（table-sort.js。UI-86）。
(function () {
  const table = document.getElementById("island-table");
  if (!table || !window.hakoniwaTableSort) return;
  // 省略したバッジも、マウスと読み上げでは全部の名前を確かめられるようにする。
  for (const summary of table.querySelectorAll(".badge-summary")) {
    const names = [...summary.querySelectorAll(".title-badge")].map((badge) => badge.textContent.trim());
    if (names.length) {
      summary.title = names.join("・");
      summary.setAttribute("aria-label", summary.title);
    }
  }
  const numeric = (key) => key !== "name";
  window.hakoniwaTableSort.makeSortable(table, {
    heads: [...table.querySelectorAll("th[data-sort]")],
    rows: (body) => [...body.rows].filter((r) => r.dataset.rank),
    numeric,
    value: (row, key) => {
      const raw = row.dataset[key.replace(/_/g, "")];
      if (!numeric(key)) return raw || "";
      return raw === undefined || raw === "" ? null : Number(raw);
    },
    // 数の列は大きい順から（順位は小さい順から）、名前は五十音の順から
    first: (key) => (numeric(key) && key !== "rank" ? "descending" : "ascending"),
    after: (rows) => rows.forEach((row, i) => (row.querySelector(".rank").textContent = String(i + 1))),
  });
})();
