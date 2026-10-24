// 不具合の報告: 画面のどこからでも開ける。書くのは「何が起きたか」だけで、開いていた画面・
// ブラウザ・画面の大きさ・直近の JavaScript のエラーは自動で添える（サーバーが島・ターン・版・
// 直前の命令を添える）。エラーはページを移っても残るよう sessionStorage に直近の数件を持つ。

const KEY = "hk-js-errors";
const KEEP = 5;

function readErrors() {
  try {
    return JSON.parse(sessionStorage.getItem(KEY) || "[]");
  } catch {
    return [];
  }
}

function remember(entry) {
  try {
    const rows = [...readErrors(), entry].slice(-KEEP);
    sessionStorage.setItem(KEY, JSON.stringify(rows));
  } catch {
    // 保存できなくても、報告そのものは送れる
  }
}

const here = () => location.pathname + location.search;

window.addEventListener("error", (e) => {
  remember({
    message: e.message || "error",
    source: e.filename || "",
    line: e.lineno ? `${e.lineno}:${e.colno || 0}` : "",
    page: here(),
    at: new Date().toISOString(),
  });
});

window.addEventListener("unhandledrejection", (e) => {
  const reason = e.reason;
  remember({
    message: `未処理の失敗: ${reason && reason.message ? reason.message : String(reason)}`,
    source: "",
    line: "",
    page: here(),
    at: new Date().toISOString(),
  });
});

function csrfToken() {
  const input = document.querySelector("input[name=csrfmiddlewaretoken]");
  if (input) return input.value;
  const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

const dialog = document.getElementById("bug-report");
if (dialog) {
  const form = dialog.querySelector("form");
  const status = dialog.querySelector(".bug-report-status");
  const send = form.querySelector('button[type="submit"]');

  const close = () => (dialog.close ? dialog.close() : dialog.removeAttribute("open"));
  const open = () => {
    status.textContent = "";
    if (dialog.showModal) dialog.showModal();
    else dialog.setAttribute("open", "");
    form.elements.what.focus();
  };
  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-bug-open]")) open();
    if (e.target.closest("[data-bug-close]")) close();
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!form.reportValidity()) return;
    send.disabled = true;
    status.textContent = "送っています…";
    const body = {
      text: form.elements.what.value,
      kind: form.elements.kind.value,
      url: here(),
      client: {
        viewport: `${innerWidth}x${innerHeight} (画面 ${screen.width}x${screen.height}, 倍率 ${devicePixelRatio})`,
        user_agent: navigator.userAgent,
        language: navigator.language,
        js_errors: readErrors(),
      },
    };
    try {
      const res = await fetch(dialog.dataset.url, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        status.textContent = data.error || `送れませんでした（${res.status}）`;
        return;
      }
      status.textContent = data.message || "ありがとうございます。";
      form.reset();
      setTimeout(close, 1800);
    } catch {
      status.textContent = "送れませんでした。通信を確かめてください。";
    } finally {
      send.disabled = false;
    }
  });
}
