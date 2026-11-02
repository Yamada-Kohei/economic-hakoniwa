// node --test tests/js/letter-push.test.mjs
// 許可・購読の作成はクリック後だけ。worker は通知と受信箱への移動だけを受け持つ。
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../../server/static/js/letter-push.js", import.meta.url), "utf8");
const worker = readFileSync(new URL("../../server/templates/letters/sw.js", import.meta.url), "utf8")
  .replaceAll("{{ inbox_url|escapejs }}", "/letters/");
const flush = () => new Promise((resolve) => setImmediate(resolve));

function browser({ secure = true, supported = true, permission = "default", saved = false,
  responseOk = true, subscribeError = false } = {}) {
  const calls = [];
  const elements = {};
  for (const selector of ["[data-push-enable]", "[data-push-disable]", "[data-push-status]"]) {
    elements[selector] = { hidden: true, handlers: {},
      addEventListener(type, handler) { this.handlers[type] = handler; } };
  }
  elements["[name=csrfmiddlewaretoken]"] = { value: "csrf-token" };
  const root = { dataset: { key: "BAAA", url: "/api/letters/push", unsupported: "非対応",
    insecure: "HTTPS が必要", denied: "不許可", error: "保存失敗", enabled: "開始", disabled: "停止" },
    querySelector: (selector) => elements[selector] };
  const subscription = { endpoint: "https://fcm.googleapis.com/example",
    toJSON() { return { endpoint: this.endpoint, keys: { p256dh: "public", auth: "auth" } }; },
    async unsubscribe() { calls.push("unsubscribe"); return true; } };
  let existing = saved ? subscription : null;
  const registration = { pushManager: {
    async getSubscription() { calls.push("getSubscription"); return existing; },
    async subscribe(options) {
      calls.push(["subscribe", options]);
      if (subscribeError) throw new Error("失敗");
      existing = subscription;
      return subscription;
    },
  } };
  const Notification = { permission, async requestPermission() {
    calls.push("requestPermission"); return permission === "denied" ? "denied" : "granted";
  } };
  const context = vm.createContext({
    document: { getElementById: () => root }, Notification, Uint8Array, atob,
    window: { isSecureContext: secure, ...(supported ? { Notification, PushManager: {} } : {}) },
    navigator: { serviceWorker: {
      async getRegistration() { return registration; },
      async register(url, options) { calls.push(["register", url, options]); return registration; },
      ready: Promise.resolve(registration),
    } },
    async fetch(url, options) { calls.push(["fetch", url, options]); return { ok: responseOk }; },
  });
  vm.runInContext(source, context);
  return { calls, elements, async click(selector) {
    elements[selector].handlers.click(); await flush();
  } };
}

test("ページを開いただけでは許可・worker 登録・購読・保存を呼ばない", async () => {
  const b = browser();
  await flush();
  assert.deepEqual(b.calls, ["getSubscription"]);
  assert.equal(b.elements["[data-push-enable]"].hidden, false);
  await b.click("[data-push-enable]");
  assert.ok(b.calls.includes("requestPermission"));
  assert.equal(b.calls.filter((call) => call[0] === "subscribe").length, 1);
  const request = b.calls.find((call) => call[0] === "fetch")[2];
  assert.equal(request.method, "POST");
  assert.equal(request.headers["X-CSRFToken"], "csrf-token");
  assert.equal(b.elements["[data-push-disable]"].hidden, false);
});

for (const [name, options, text] of [
  ["HTTP", { secure: false }, "HTTPS が必要"],
  ["非対応", { supported: false }, "非対応"],
  ["不許可", { permission: "denied" }, "不許可"],
]) {
  test(`${name} ではボタンの代わりに説明を出す`, async () => {
    const b = browser(options);
    await flush();
    assert.equal(b.elements["[data-push-enable]"].hidden, true);
    assert.equal(b.elements["[data-push-status]"].textContent, text);
    assert.ok(!b.calls.includes("requestPermission"));
  });
}

test("停止はサーバーから削除してブラウザの購読も取り消す", async () => {
  const b = browser({ saved: true });
  await flush();
  await b.click("[data-push-disable]");
  assert.equal(b.calls.find((call) => call[0] === "fetch")[2].method, "DELETE");
  assert.ok(b.calls.includes("unsubscribe"));
  assert.equal(b.elements["[data-push-enable]"].hidden, false);
});

test("保存に失敗したら再度登録でき、削除に失敗したら停止を再試行できる", async () => {
  const b = browser({ responseOk: false });
  await flush();
  await b.click("[data-push-enable]");
  assert.equal(b.elements["[data-push-enable]"].hidden, false);
  assert.ok(b.calls.includes("unsubscribe"));
  const saved = browser({ saved: true, responseOk: false });
  await flush();
  await saved.click("[data-push-disable]");
  assert.ok(!saved.calls.includes("unsubscribe"));
  assert.equal(saved.elements["[data-push-disable]"].hidden, false);
});

function serviceWorker(tabs = []) {
  const handlers = {};
  const calls = [];
  const self = {
    location: { origin: "https://game.example" },
    addEventListener(type, handler) { handlers[type] = handler; },
    registration: { async showNotification(title, options) { calls.push([title, options]); } },
    clients: {
      async matchAll() { return tabs; },
      async openWindow(url) { calls.push(["open", url]); },
    },
  };
  vm.runInNewContext(worker, { self, URL });
  return { handlers, calls, async fire(type, extra = {}) {
    let promise;
    handlers[type]({ ...extra, waitUntil(value) { promise = value; } });
    await promise;
  } };
}

test("worker は固定の tag で通知を置き換える", async () => {
  const sw = serviceWorker();
  assert.deepEqual(Object.keys(sw.handlers).sort(), ["notificationclick", "push"]);
  await sw.fire("push", { data: { json: () => ({ title: "箱庭", body: "未読の公電が 3 通あります" }) } });
  assert.equal(sw.calls[0][1].tag, "hakoniwa-letters");
  assert.equal(sw.calls[0][1].body, "未読の公電が 3 通あります");
});

test("通知を押すと受信箱のタブを前に出し、なければ新しく開く（ほかのタブは動かさない）", async () => {
  for (const path of ["/letters/", "/islands/1/", null]) {
    const actions = [];
    const tab = { url: `https://game.example${path}`, async focus() { actions.push("focus"); },
      async navigate(url) { actions.push(url); } };
    const sw = serviceWorker(path ? [tab] : []);
    await sw.fire("notificationclick", { notification: { close() { actions.push("close"); } } });
    assert.equal(actions[0], "close");
    if (path === "/letters/") {
      assert.deepEqual(actions, ["close", "focus"]);
      assert.deepEqual(sw.calls, []);
    } else {
      assert.ok(!actions.includes("https://game.example/letters/"));
      assert.deepEqual(sw.calls, [["open", "https://game.example/letters/"]]);
    }
  }
});
