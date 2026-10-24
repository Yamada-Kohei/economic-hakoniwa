// 公電の新着を、画面が見えている間だけ調べる。保存を禁じたブラウザでもページ内で動く。
const notice = document.getElementById("letter-notice");
if (notice) mountLetterNotice(notice);

function mountLetterNotice(root) {
  const INTERVAL = 60_000;
  const TIMEOUT = 15_000;
  // 同じブラウザでアカウントや島を替えても、既読の基準を混ぜない。
  const prefix = `hk-letter-notice:${root.dataset.user}:${root.dataset.island}`;
  const lastKey = `${prefix}:last-id`;
  const enabledKey = `${prefix}:enabled`;
  const card = root.querySelector(".letter-notice-card");
  const link = root.querySelector("[data-letter-notice-link]");
  const close = root.querySelector("[data-letter-notice-close]");
  const nav = document.getElementById("letters-link");
  const preference = document.getElementById("letter-notice-preference");
  const checkbox = document.getElementById("letter-notice-enabled");
  const pending = new Map();
  let lastId = readLastId();
  let enabled = readStorage(enabledKey) !== "false";
  let timer;
  let active;
  let stopped = false;

  function readStorage(key) {
    try {
      return localStorage.getItem(key);
    } catch {
      return null;
    }
  }

  function writeStorage(key, value) {
    try {
      localStorage.setItem(key, String(value));
    } catch {
      // ページ内の値を使い続ける。
    }
  }

  function readLastId() {
    const value = readStorage(lastKey);
    const id = value === null || value === "" ? null : Number(value);
    return Number.isSafeInteger(id) && id >= 0 ? id : null;
  }

  function dismiss() {
    pending.clear();
    card.hidden = true;
    link.textContent = "";
  }

  function showPending() {
    if (!pending.size) return;
    if (pending.size === 1) {
      const letter = pending.values().next().value;
      link.textContent = `${letter.from.name}から公電: ${letter.subject}`;
      link.href = root.dataset.letterUrl.replace(/0\/$/, `${letter.id}/`);
    } else {
      link.textContent = `公電が ${pending.size} 通届きました`;
      link.href = nav.href;
    }
    card.hidden = false;
  }

  function updateBadge(unread) {
    let badge = nav.querySelector(".badge");
    if (!unread) {
      if (badge) badge.remove();
      return;
    }
    if (!badge) {
      badge = document.createElement("span");
      badge.className = "badge";
      nav.append(" ", badge);
    }
    badge.textContent = `未読の公電 ${unread}`;
  }

  function syncPreference() {
    if (checkbox) checkbox.checked = enabled;
    if (!enabled) dismiss();
  }

  close.addEventListener("click", dismiss);
  link.addEventListener("click", dismiss);
  link.addEventListener("auxclick", (event) => {
    if (event.button === 1) dismiss();
  });
  if (checkbox) {
    syncPreference();
    preference.hidden = false;
    checkbox.addEventListener("change", () => {
      enabled = checkbox.checked;
      writeStorage(enabledKey, enabled);
      syncPreference();
    });
  }
  window.addEventListener("storage", (event) => {
    if (event.key === enabledKey || event.key === null) {
      enabled = readStorage(enabledKey) !== "false";
      syncPreference();
    }
  });

  async function poll() {
    if (stopped || active || document.visibilityState !== "visible") return;
    clearTimeout(timer);
    const controller = new AbortController();
    active = controller;
    const timeout = setTimeout(() => controller.abort(), TIMEOUT);
    try {
      // 別のタブで知らせた分も基準に取り込む。
      const savedId = readLastId();
      const baseline = savedId === null ? lastId : Math.max(lastId ?? 0, savedId);
      let newest = baseline ?? 0;
      let page = 1;
      const fresh = new Map();
      while (true) {
        const url = new URL(root.dataset.url, window.location.href);
        url.searchParams.set("box", "inbox");
        url.searchParams.set("page", page);
        const response = await fetch(url, {
          credentials: "same-origin",
          cache: "no-store",
          signal: controller.signal,
        });
        if (controller.signal.aborted || document.visibilityState !== "visible") return;
        if (response.status === 403) {
          stopped = true;
          dismiss();
          return;
        }
        if (!response.ok) return;
        const data = await response.json();
        if (controller.signal.aborted || document.visibilityState !== "visible") return;
        if (String(data.island.id) !== root.dataset.island) {
          stopped = true;
          dismiss();
          return;
        }
        if (page === 1) {
          updateBadge(data.unread);
        }
        for (const letter of data.letters) {
          newest = Math.max(newest, letter.id);
          if (baseline !== null && letter.id > baseline && !letter.read) {
            fresh.set(letter.id, letter);
          }
        }
        // 初回は記録だけ。普段は新着が次のページにも続く場合だけ読む。
        if (baseline === null || !enabled || page >= data.pages ||
            !data.letters.length || data.letters.some((letter) => letter.id <= baseline)) break;
        page++;
      }
      if (controller.signal.aborted || document.visibilityState !== "visible") return;
      const sharedId = readLastId();
      lastId = Math.max(newest, lastId ?? 0, sharedId ?? 0);
      writeStorage(lastKey, lastId);
      if (enabled) {
        for (const [id, letter] of fresh) {
          if (sharedId === null || id > sharedId) pending.set(id, letter);
        }
        if (fresh.size) showPending();
      }
    } catch {
      // 通信失敗・中断では基準を進めず、次の確認で再試行する。
    } finally {
      clearTimeout(timeout);
      if (active === controller) {
        active = null;
        if (!stopped && document.visibilityState === "visible") timer = setTimeout(poll, INTERVAL);
      }
    }
  }

  function pause() {
    clearTimeout(timer);
    if (active) active.abort();
    active = null;
  }

  document.addEventListener("visibilitychange", () => {
    pause();
    if (document.visibilityState === "visible") poll();
  });
  window.addEventListener("pagehide", pause);
  window.addEventListener("pageshow", () => poll());
  poll();
}
