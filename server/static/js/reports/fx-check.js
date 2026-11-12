// 為替の画面の命令の確かめ（UI-80）。画面のほかの部分を読み込まずに試せるよう、ここに分ける。

/**
 * 為替の介入の欄（data-market-check）で、前のターンに相手のいなかった通貨を選んだときの確かめの文
 * （買うなら売りに出ていた量、売るなら買いに出ていた量が 0。UI-80）。記録がない通貨と、やめる命令
 * （額 0）は聞かない。
 */
export function marketWarning(form) {
  if (!("marketCheck" in form.dataset)) return "";
  const option = form.elements.currency?.selectedOptions?.[0];
  const amount = Number(form.elements.amount?.value || 0);
  if (!option || !(amount > 0)) return "";
  const selling = form.elements.side?.value === "sell";
  const found = selling ? option.dataset.wanted : option.dataset.offered;
  if (found === undefined || Number(found) > 0) return "";
  const name = option.textContent.trim();
  return selling
    ? `前のターン、${name}を買う注文は市場にありませんでした。売る介入をしても売れず、行政力だけを使うかもしれません。命令に足しますか？`
    : `前のターン、${name}を売る注文は市場にありませんでした。買う介入をしても買えず、行政力だけを使うかもしれません。命令に足しますか？`;
}
