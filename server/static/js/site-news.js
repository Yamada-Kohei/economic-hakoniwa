// 更新のお知らせ（UI-96）: 見ていない更新履歴があるとき、画面を開いたら窓で1度だけ出す。
// 出したらすぐサーバーに「ここまで見た」と送るので、次の画面からは出ない（閉じなくても）。
// トップの画面で更新履歴を読む人の邪魔にならないよう、窓は閉じるボタンか Esc で閉じる。

function csrfToken() {
  const input = document.querySelector("input[name=csrfmiddlewaretoken]");
  if (input) return input.value;
  const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

export async function markSeen(dialog, fetchImpl = fetch) {
  const last = Number(dialog.dataset.last || 0);
  try {
    await fetchImpl(dialog.dataset.url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
      body: JSON.stringify({ last }),
    });
  } catch {
    // 送れなかったら、次に開いた画面でもう一度出る（見落とさないほうを取る）
  }
}

export function showNews(dialog, fetchImpl = fetch) {
  if (!dialog || dialog.open) return false;
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", "");
  markSeen(dialog, fetchImpl);
  return true;
}

if (typeof document !== "undefined") {
  showNews(document.getElementById("site-news"));
}
