// トップの島の一覧の表: 列の名前を押すと並べ替える（もう一度押すと逆の順）。値のない島はいつも最後。
// 順位の列は、そのときの並びの順に振り直す。
(function () {
  const table = document.getElementById("island-table");
  if (!table) return;
  // 省略したバッジも、マウスと読み上げでは全部の名前を確かめられるようにする。
  for (const summary of table.querySelectorAll(".badge-summary")) {
    const names = [...summary.querySelectorAll(".title-badge")].map((badge) => badge.textContent.trim());
    if (names.length) {
      summary.title = names.join("・");
      summary.setAttribute("aria-label", summary.title);
    }
  }
  const body = table.tBodies[0];
  const heads = table.querySelectorAll("th[data-sort]");
  const numeric = (key) => key !== "name";
  const value = (row, key) => {
    const raw = row.dataset[key.replace(/_/g, "")];
    if (!numeric(key)) return raw || "";
    return raw === undefined || raw === "" ? null : Number(raw);
  };

  function sortBy(head, key, descending) {
    const rows = Array.from(body.rows).filter((r) => r.dataset.rank);
    rows.sort((a, b) => {
      const x = value(a, key);
      const y = value(b, key);
      if (x === null || y === null) return x === y ? 0 : x === null ? 1 : -1;
      const order = numeric(key) ? x - y : String(x).localeCompare(String(y), "ja");
      return descending ? -order : order;
    });
    rows.forEach((row, i) => {
      body.appendChild(row);
      row.querySelector(".rank").textContent = String(i + 1);
    });
    heads.forEach((h) => h.removeAttribute("aria-sort"));
    head.setAttribute("aria-sort", descending ? "descending" : "ascending");
  }

  heads.forEach((head) => {
    const key = head.dataset.sort;
    head.querySelector("button").addEventListener("click", () => {
      const current = head.getAttribute("aria-sort");
      // 数の列は大きい順から（順位は小さい順から）、名前は五十音の順から
      const first = numeric(key) && key !== "rank" ? "descending" : "ascending";
      const descending = current ? current === "ascending" : first === "descending";
      sortBy(head, key, descending);
    });
  });
})();
