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

/** 文字を HTML に入れても安全な形にする。 */
export function escapeHtml(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}
