// サーバーの JSON API を呼ぶ。書き込み（PUT・POST）には CSRF の印を付ける。

function csrfToken() {
  const input = document.querySelector("input[name=csrfmiddlewaretoken]");
  if (input) return input.value;
  const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

export async function getJson(url) {
  const res = await fetch(url, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`${res.status} ${url}`);
  return res.json();
}

/** JSON を送り、{ ok, status, data } を返す（400 のときも data を読む）。 */
export async function sendJson(url, method, body) {
  const res = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
    body: JSON.stringify(body),
  });
  let data = {};
  try {
    data = await res.json();
  } catch {
    data = { error: await res.text().catch(() => "") };
  }
  return { ok: res.ok, status: res.status, data };
}

/**
 * 命令の打ち間違いの確かめ（UI-75）: 命令の列を送ったときにサーバーが返した確かめ（asks: 命令ごとの
 * text と notes）を見せ、このまま並べるかを聞く。確かめの目安はサーバー（server/web/order_confirm.py）
 * が持ち、画面・API・MCP で同じ文を出す。
 */
export function confirmOrders(asks) {
  const lines = asks.map((a) => [`・${a.text}`, ...a.notes.map((n) => `　${n}`)].join("\n"));
  return confirm(`次の命令を確かめてください。\n\n${lines.join("\n\n")}\n\nこのまま命令に追加しますか？`);
}

/** 文字を HTML に入れても安全な形にする。 */
export function escapeHtml(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}
