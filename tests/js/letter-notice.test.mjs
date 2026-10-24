// node --test tests/js/letter-notice.test.mjs
// 通信・時刻・タブの表示状態を置き換え、新着の境目と通知の寿命を確かめる。
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../../server/static/js/letter-notice.js", import.meta.url), "utf8");
const prefix = "hk-letter-notice:7:2";
const lastKey = `${prefix}:last-id`;
const enabledKey = `${prefix}:enabled`;
const flush = () => new Promise((resolve) => setImmediate(resolve));
const letter = (id, read = false) => ({ id, read, from: { name: "海辺国" }, subject: "会談のお願い" });
const inbox = (letters, extra = {}) => ({
  island: { id: 2 }, unread: letters.filter((row) => !row.read).length, pages: 1, letters, ...extra,
});

class Element {
  constructor() {
    this.handlers = {};
    this.hidden = true;
    this.textContent = "";
    this.children = [];
  }
  addEventListener(type, handler) { this.handlers[type] = handler; }
  fire(type, event = {}) { return this.handlers[type]?.(event); }
  querySelector() { return this.children.find((child) => child.className === "badge") || null; }
  append(...children) {
    for (const child of children.filter((value) => typeof value !== "string")) {
      this.children.push(child);
      child.remove = () => { this.children = this.children.filter((value) => value !== child); };
    }
  }
}

function browser({ stored = {}, storageFails = false, visible = true, mounted = true } = {}) {
  const values = new Map(Object.entries(stored));
  const root = new Element();
  root.dataset = { user: "7", island: "2", url: "/api/letters", letterUrl: "/letters/0/" };
  const card = new Element();
  const link = new Element();
  const close = new Element();
  const preference = new Element();
  const checkbox = new Element();
  const nav = new Element();
  nav.href = "https://game.example/letters/";
  const parts = { ".letter-notice-card": card, "[data-letter-notice-link]": link, "[data-letter-notice-close]": close };
  root.querySelector = (selector) => parts[selector];
  const elements = { "letter-notice": mounted ? root : null, "letters-link": nav,
    "letter-notice-preference": preference, "letter-notice-enabled": checkbox };
  const document = new Element();
  document.visibilityState = visible ? "visible" : "hidden";
  document.getElementById = (id) => elements[id];
  document.createElement = () => new Element();
  const window = new Element();
  window.location = { href: "https://game.example/islands/2/" };
  const requests = [];
  const timers = new Map();
  let nextTimer = 0;
  const storage = {
    getItem(key) {
      if (storageFails) throw new Error("保存禁止");
      return values.get(key) ?? null;
    },
    setItem(key, value) {
      if (storageFails) throw new Error("保存禁止");
      values.set(key, value);
    },
  };
  const context = vm.createContext({ document, window, localStorage: storage, URL, AbortController,
    setTimeout(handler, delay) { const id = ++nextTimer; timers.set(id, { handler, delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
    fetch(url, options) {
      return new Promise((resolve, reject) => {
        requests.push({ url, options, resolve, reject });
        options.signal.addEventListener("abort", () => reject(new Error("中断")));
      });
    },
  });
  vm.runInContext(source, context);
  return {
    values, card, link, close, preference, checkbox, nav, requests, timers, window,
    async reply(data, status = 200) {
      requests.at(-1).resolve({ status, ok: status === 200, json: async () => data });
      await flush();
    },
    async tick() {
      const entry = [...timers].find(([, timer]) => timer.delay === 60_000);
      assert.ok(entry, "次の1分の確認が予約されている");
      timers.delete(entry[0]);
      entry[1].handler();
      await flush();
    },
    async visibility(state) {
      document.visibilityState = state;
      document.fire("visibilitychange");
      await flush();
    },
  };
}

test("初回は昔の未読を知らせず、バッジと基準だけを記録する", async () => {
  const b = browser();
  await b.reply(inbox([letter(4), letter(3)], { pages: 2 }));
  assert.equal(b.card.hidden, true);
  assert.equal(b.values.get(lastKey), "4");
  assert.equal(b.nav.querySelector().textContent, "未読の公電 2");
  assert.equal(b.requests.length, 1);
  assert.equal(b.checkbox.checked, true);
  assert.equal(b.preference.hidden, false);
  assert.equal(b.requests[0].options.cache, "no-store");
});

test("空の受信箱も初回の基準にし、1通は本文を文字として表示する", async () => {
  const b = browser();
  await b.reply(inbox([]));
  assert.equal(b.values.get(lastKey), "0");
  await b.tick();
  await b.reply(inbox([{ ...letter(1), subject: '<img src=x onerror="alert(1)">' }]));
  assert.equal(b.card.hidden, false);
  assert.equal(b.link.textContent, '海辺国から公電: <img src=x onerror="alert(1)">');
  assert.equal(b.link.href, "/letters/1/");
  await b.tick();
  await b.reply(inbox([letter(1)]));
  assert.equal(b.card.hidden, false, "時間だけで消えない");
  b.link.fire("click");
  assert.equal(b.card.hidden, true);
});

test("未読の新着だけをまとめ、閉じるまで次の到着分も足す", async () => {
  const b = browser({ stored: { [lastKey]: "4" } });
  await b.reply(inbox([letter(8, true), letter(7), letter(6), letter(4)]));
  assert.equal(b.link.textContent, "公電が 2 通届きました");
  assert.equal(b.link.href, b.nav.href);
  await b.tick();
  await b.reply(inbox([letter(9), letter(8, true), letter(7)]));
  assert.equal(b.link.textContent, "公電が 3 通届きました");
  b.close.fire("click");
  assert.equal(b.card.hidden, true);
  await b.tick();
  await b.reply(inbox([letter(9), letter(8, true), letter(7)]));
  assert.equal(b.card.hidden, true);
  assert.equal(b.values.get(lastKey), "9");
});

test("初めから隠れたタブでは通信せず、復帰時に1回確認する", async () => {
  const b = browser({ visible: false });
  assert.equal(b.requests.length, 0);
  await b.visibility("visible");
  assert.equal(b.requests.length, 1);
  await b.reply(inbox([]));
  await b.visibility("hidden");
  assert.equal(b.timers.size, 0);
  await b.visibility("visible");
  assert.equal(b.requests.length, 2);
});

test("通信中に隠れたら中断し、すぐ戻っても新たに確認する", async () => {
  const b = browser({ stored: { [lastKey]: "4" } });
  await b.visibility("hidden");
  assert.equal(b.requests[0].options.signal.aborted, true);
  await b.visibility("visible");
  await b.reply(inbox([letter(5)]));
  assert.equal(b.requests.length, 2);
  assert.equal(b.link.href, "/letters/5/");
});

test("保存禁止でもページ内で基準と設定を保つ", async () => {
  const b = browser({ storageFails: true });
  await b.reply(inbox([letter(4)]));
  await b.tick();
  await b.reply(inbox([letter(5)]));
  assert.equal(b.card.hidden, false);
  b.checkbox.checked = false;
  b.checkbox.fire("change");
  assert.equal(b.card.hidden, true);
  await b.tick();
  await b.reply(inbox([letter(6)]));
  assert.equal(b.card.hidden, true);
  b.checkbox.checked = true;
  b.checkbox.fire("change");
  await b.tick();
  await b.reply(inbox([letter(6)]));
  assert.equal(b.card.hidden, true, "停止中の到着分は後で知らせ直さない");
});

test("設定を切っていてもバッジを更新し、0なら取り除く", async () => {
  const b = browser({ stored: { [lastKey]: "4", [enabledKey]: "false" } });
  await b.reply(inbox([letter(5)]));
  assert.equal(b.card.hidden, true);
  assert.equal(b.checkbox.checked, false);
  assert.equal(b.nav.querySelector().textContent, "未読の公電 1");
  await b.tick();
  await b.reply(inbox([letter(5, true)]));
  assert.equal(b.nav.querySelector(), null);
  b.checkbox.checked = true;
  b.checkbox.fire("change");
  assert.equal(b.values.get(enabledKey), "true");
});

test("30通を超える新着もページをまたいでまとめる", async () => {
  const b = browser({ stored: { [lastKey]: "4" } });
  const first = Array.from({ length: 30 }, (_, i) => letter(36 - i));
  await b.reply(inbox(first, { pages: 2, unread: 32 }));
  assert.equal(b.requests[1].url.searchParams.get("page"), "2");
  await b.reply(inbox([letter(6), letter(5), letter(4, true)], { pages: 2 }));
  assert.equal(b.link.textContent, "公電が 32 通届きました");
  assert.equal(b.nav.querySelector().textContent, "未読の公電 32");
  assert.equal(b.values.get(lastKey), "36");
});

test("通信失敗や途中のページの失敗では基準を進めず再試行する", async () => {
  const b = browser({ stored: { [lastKey]: "4" } });
  await b.reply(inbox([letter(6)], { pages: 2 }));
  await b.reply({}, 500);
  assert.equal(b.values.get(lastKey), "4");
  assert.equal(b.card.hidden, true);
  await b.tick();
  b.requests.at(-1).reject(new Error("オフライン"));
  await flush();
  await b.tick();
  await b.reply(inbox([letter(6), letter(5), letter(4)]));
  assert.equal(b.link.textContent, "公電が 2 通届きました");
});

test("403と島の切り替わりでは通知と定期確認を止める", async () => {
  for (const [data, status] of [[{}, 403], [inbox([letter(5)], { island: { id: 9 } }), 200]]) {
    const b = browser({ stored: { [lastKey]: "4" } });
    await b.reply(data, status);
    assert.equal(b.card.hidden, true);
    assert.equal(b.timers.size, 0);
    assert.equal(b.values.get(lastKey), "4");
    await b.visibility("visible");
    assert.equal(b.requests.length, 1);
  }
});

test("別タブで確認済みのIDと設定を取り込む", async () => {
  const b = browser({ stored: { [lastKey]: "4" } });
  b.values.set(lastKey, "5");
  await b.reply(inbox([letter(6), letter(5)]));
  assert.equal(b.link.href, "/letters/6/");
  b.values.set(enabledKey, "false");
  b.window.fire("storage", { key: enabledKey });
  assert.equal(b.checkbox.checked, false);
  assert.equal(b.card.hidden, true);
});

test("対象のHTMLがないページでは通信しない", () => {
  const b = browser({ mounted: false });
  assert.equal(b.requests.length, 0);
  assert.equal(b.timers.size, 0);
});
