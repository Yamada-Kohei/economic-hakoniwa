// 表の並べ替え（UI-86、掲示板 #25 の 45 番）: 列の頭を押すと、その列で並べ替える（もう一度押すと
// 逆の順）。値のない行（空・「-」）はいつも最後。数の列は大きい順から、文字の列は五十音の順から。
// トップの島の一覧（top-table.js）も同じ部品を使う。
//
// どの画面でも、頭が1行で、本体の行が2行以上あり、本体にまたがるます（rowspan・colspan）のない表を
// 並べ替えられるようにする（後から足された表も）。並べ替えたくない表には data-nosort を付ける。
// ますの値は data-sort があればそれを、なければ見えている字（小さな注記 small を除く）を読む。
(function () {
  const SKIP = "[data-nosort], #island-table";

  /** ますの並べ替えの値の字。 */
  function cellText(cell) {
    if (!cell) return "";
    if (cell.dataset.sort !== undefined) return cell.dataset.sort;
    let text = "";
    for (const node of cell.childNodes) {
      if (node.nodeType === Node.ELEMENT_NODE && (node.matches("small, br, button, details") || node.hidden)) {
        if (node.matches("br") && text.trim()) break; // 改行の後は注記
        continue;
      }
      text += node.textContent;
    }
    return text.trim();
  }

  /** 字を数に。通貨の記号・桁の区切り・%・単位を外して読む。数でなければ NaN、空なら null。 */
  function numberOf(text) {
    if (text === "" || text === "-" || text === "—" || text === "－") return null;
    const clean = text.replace(/[,，\s]/g, "").replace(/−/g, "-");
    const m = clean.match(/[-+]?\d+(?:\.\d+)?/);
    if (!m) return NaN;
    // 数の前に字が多い（「第3農場」など）ものは数に数えない（通貨の記号と括弧だけはよい）
    const before = clean.slice(0, m.index).replace(/[¥$€£₩元円(（]/g, "");
    return before.length > 1 ? NaN : Number(m[0]);
  }

  /**
   * 表 table を並べ替えられるようにする。
   * options.value(row, key, head) → 値（数・字・null）。なければますの字から読む
   * options.numeric(key, head) → 数の列か。なければ列の半分以上が数なら数の列
   * options.after(rows) → 並べ替えた後に呼ぶ（順位の振り直しなど）
   * options.rows(body) → 並べ替える行。なければ本体の行すべて
   * options.heads → 並べ替える列の頭（th）。なければ頭の行のすべての th（空の頭を除く）
   */
  function makeSortable(table, options = {}) {
    if (table.dataset.sortReady) return;
    table.dataset.sortReady = "1";
    const body = table.tBodies[0];
    const allHeads = [...table.tHead.rows[0].cells];
    const heads = options.heads || allHeads.filter((h) => h.textContent.trim());
    const rowsOf = options.rows || ((b) => [...b.rows]);
    const valueOf = (row, key, head) => {
      if (options.value) return options.value(row, key, head);
      const text = cellText(row.cells[allHeads.indexOf(head)]);
      return isNumeric(key, head) ? numberOf(text) : text || null;
    };
    const numericCache = new Map();
    function isNumeric(key, head) {
      if (options.numeric) return options.numeric(key, head);
      if (!numericCache.has(head)) {
        const index = allHeads.indexOf(head);
        const values = rowsOf(body).map((r) => numberOf(cellText(r.cells[index]))).filter((v) => v !== null);
        numericCache.set(head, values.length > 0 && values.filter((v) => !Number.isNaN(v)).length * 2 >= values.length);
      }
      return numericCache.get(head);
    }

    function sortBy(head, descending) {
      const key = head.dataset.sort || String(allHeads.indexOf(head));
      const numeric = isNumeric(key, head);
      const rows = rowsOf(body);
      const keyed = rows.map((row, i) => {
        let v = valueOf(row, key, head);
        if (numeric && Number.isNaN(v)) v = null;
        return { row, v, i };
      });
      keyed.sort((a, b) => {
        if (a.v === null || b.v === null) return a.v === b.v ? a.i - b.i : a.v === null ? 1 : -1;
        const order = numeric ? a.v - b.v : String(a.v).localeCompare(String(b.v), "ja");
        return (descending ? -order : order) || a.i - b.i;
      });
      for (const { row } of keyed) body.appendChild(row);
      // 並べ替えない行（合計など）は最後に残す
      for (const row of [...body.rows]) if (!rows.includes(row)) body.appendChild(row);
      heads.forEach((h) => h.removeAttribute("aria-sort"));
      head.setAttribute("aria-sort", descending ? "descending" : "ascending");
      if (options.after) options.after(keyed.map((k) => k.row));
    }

    for (const head of heads) {
      let button = head.querySelector("button");
      if (!button) {
        button = document.createElement("button");
        button.type = "button";
        button.className = "sort-head";
        button.append(...head.childNodes);
        head.append(button);
      }
      button.addEventListener("click", () => {
        const key = head.dataset.sort || String(allHeads.indexOf(head));
        const current = head.getAttribute("aria-sort");
        const first = options.first ? options.first(key, head) : isNumeric(key, head) ? "descending" : "ascending";
        const descending = current ? current === "ascending" : first === "descending";
        sortBy(head, descending);
      });
    }
  }

  /** 並べ替えられる形の表か（頭が1行、本体が2行以上、本体にまたがるますがない）。 */
  function fits(table) {
    if (table.matches(SKIP) || table.dataset.sortReady) return false;
    if (!table.tHead || table.tHead.rows.length !== 1 || table.tBodies.length !== 1) return false;
    const body = table.tBodies[0];
    if (body.rows.length < 2) return false;
    return !body.querySelector("td[rowspan], td[colspan], th[rowspan], th[colspan]");
  }

  function scan(root) {
    const tables = root.matches?.("table") ? [root] : root.querySelectorAll?.("table") || [];
    for (const table of tables) if (fits(table)) makeSortable(table);
  }

  window.hakoniwaTableSort = { makeSortable, cellText, numberOf };

  function start() {
    scan(document);
    // 後から JavaScript が描いた表（推移・一覧の読み込みなど）も
    const added = new Set();
    let pending = null;
    new MutationObserver((records) => {
      for (const r of records) for (const node of r.addedNodes) if (node.nodeType === 1) added.add(node);
      if (pending) return;
      pending = requestAnimationFrame(() => {
        pending = null;
        const nodes = [...added];
        added.clear();
        for (const node of nodes) if (node.isConnected) scan(node.closest?.("table") || node);
      });
    }).observe(document.body, { childList: true, subtree: true });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
