/* 公電のブラウザ通知。許可を聞くのはボタンを押したときだけ。 */
(() => {
  const root = document.getElementById("letter-push");
  if (!root) return;
  const enable = root.querySelector("[data-push-enable]");
  const disable = root.querySelector("[data-push-disable]");
  const status = root.querySelector("[data-push-status]");
  let registration;
  let subscription;
  let busy = false;

  function message(text) { status.textContent = text; }
  function show() {
    const denied = Notification.permission === "denied";
    enable.hidden = denied || !!subscription;
    disable.hidden = denied || !subscription;
    if (denied) message(root.dataset.denied);
  }
  async function save(method, value) {
    const response = await fetch(root.dataset.url, {
      method,
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": root.querySelector("[name=csrfmiddlewaretoken]").value,
      },
      body: JSON.stringify(value),
    });
    if (!response.ok) throw new Error("push registration failed");
  }
  function publicKey() {
    const value = root.dataset.key.replace(/-/g, "+").replace(/_/g, "/");
    return Uint8Array.from(atob(value + "=".repeat((4 - value.length % 4) % 4)),
      (char) => char.charCodeAt(0));
  }
  async function run(action) {
    if (busy) return;
    busy = true;
    enable.disabled = disable.disabled = true;
    try { await action(); }
    catch { message(Notification.permission === "denied" ? root.dataset.denied : root.dataset.error); }
    finally {
      busy = false;
      enable.disabled = disable.disabled = false;
      show();
    }
  }
  if (!window.isSecureContext) {
    message(root.dataset.insecure);
    return;
  }
  if (!("Notification" in window) || !("serviceWorker" in navigator) || !("PushManager" in window)) {
    message(root.dataset.unsupported);
    return;
  }
  enable.addEventListener("click", () => run(async () => {
    // 最初の await より前に呼び、ユーザーの操作とのつながりを保つ。
    const permission = await Notification.requestPermission();
    if (permission !== "granted") { message(root.dataset.denied); return; }
    registration = registration || await navigator.serviceWorker.register("/sw.js", { scope: "/" });
    await navigator.serviceWorker.ready;
    subscription = await registration.pushManager.getSubscription();
    if (!subscription) {
      subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true, applicationServerKey: publicKey(),
      });
    }
    try { await save("POST", subscription.toJSON()); }
    catch (error) {
      // 保存できなければ、もう一度「知らせる」で登録し直せる状態に戻す。
      await subscription.unsubscribe();
      subscription = null;
      throw error;
    }
    message(root.dataset.enabled);
  }));
  disable.addEventListener("click", () => run(async () => {
    await save("DELETE", { endpoint: subscription.endpoint });
    await subscription.unsubscribe();
    subscription = null;
    message(root.dataset.disabled);
  }));
  // 開いただけでは worker の登録・許可の確認・購読の作成をしない。
  navigator.serviceWorker.getRegistration("/").then(async (found) => {
    registration = found;
    subscription = found ? await found.pushManager.getSubscription() : null;
    show();
  }).catch(() => message(root.dataset.error));
})();
