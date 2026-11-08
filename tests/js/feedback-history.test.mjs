// node --test tests/js/feedback-history.test.mjs
// 実際の報告窓のスクリプトを読み、本人の報告と返事の表示を確かめる。
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../../server/static/js/feedback.js", import.meta.url), "utf8");
const flush = () => new Promise((resolve) => setImmediate(resolve));

class Element {
  constructor(tag = "div") {
    this.tagName = tag;
    this.children = [];
    this.handlers = {};
    this.style = {};
    this.text = "";
  }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return this.text + this.children.map((child) => child.textContent).join(""); }
  set innerHTML(_) { throw new Error("返事や状態を HTML として挿入しない"); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.text = ""; this.children = [...children]; }
  addEventListener(type, handler) { this.handlers[type] = handler; }
  fire(type, event = {}) { return this.handlers[type]?.(event); }
}

function browser({ mounted = true } = {}) {
  const history = new Element();
  const status = new Element();
  const send = new Element("button");
  const form = new Element("form");
  let focused = false;
  let reset = false;
  form.elements = {
    what: { value: "表示が崩れます", focus() { focused = true; } },
    kind: { value: "display" },
  };
  form.querySelector = () => send;
  form.reportValidity = () => true;
  form.reset = () => { reset = true; };
  const dialog = new Element("dialog");
  dialog.dataset = { url: "/api/bug-reports" };
  const parts = { form, ".bug-report-status": status, "[data-bug-history]": history };
  dialog.querySelector = (selector) => parts[selector];
  dialog.showModal = () => { dialog.open = true; };
  dialog.close = () => { dialog.open = false; };
  const document = new Element();
  document.getElementById = () => mounted ? dialog : null;
  document.createElement = (tag) => new Element(tag);
  document.querySelector = () => ({ value: "csrf-test" });
  const requests = [];
  const timers = [];
  const context = vm.createContext({
    document,
    window: new Element(),
    location: { pathname: "/islands/2/", search: "?x=3&y=4" },
    innerWidth: 390, innerHeight: 800, devicePixelRatio: 1,
    screen: { width: 390, height: 844 },
    navigator: { userAgent: "test", language: "ja-JP" },
    sessionStorage: { getItem() { return null; } },
    setTimeout(handler, delay) { timers.push({ handler, delay }); },
    fetch(url, options) {
      return new Promise((resolve, reject) => requests.push({ url, options, resolve, reject }));
    },
  });
  vm.runInContext(source, context);
  return {
    history, status, requests, dialog, send, timers,
    get focused() { return focused; },
    get reset() { return reset; },
    open() {
      document.fire("click", { target: { closest: (selector) => selector === "[data-bug-open]" } });
    },
    submit() { return form.fire("submit", { preventDefault() {} }); },
    async reply(data, status = 200, request = requests.at(-1)) {
      request.resolve({ status, ok: status >= 200 && status < 300, json: async () => data });
      await flush();
    },
    async fail() { requests.at(-1).reject(new Error("通信失敗")); await flush(); },
  };
}

function report(id, status = "未読", reply = "") {
  return { id, created_at: "2026-11-08T09:00:00+09:00", status_name: status, reply };
}

test("窓を開くと本人のAPIを読み、新しい10件の番号・日付・状態・返事を文字で出す", async () => {
  const b = browser();
  assert.equal(b.requests.length, 0, "窓を開く前には読み込まない");
  b.open();
  assert.equal(b.dialog.open, true);
  assert.equal(b.focused, true);
  assert.equal(b.requests.length, 1);
  assert.equal(b.requests[0].url, "/api/bug-reports");
  assert.equal(b.requests[0].options.credentials, "same-origin");
  assert.equal(b.requests[0].options.method, undefined, "GET で本人の一覧を読む");
  const reply = '<img src=x onerror="alert(1)">\nPR #123 で直しました';
  const states = ["未読", "読んだ", "対応中", "済み"];
  const rows = Array.from({ length: 14 }, (_, i) => report(14 - i, states[i % 4], i === 0 ? reply : ""));
  await b.reply({ reports: rows });
  assert.equal(b.history.children.length, 1);
  const list = b.history.children[0];
  assert.equal(list.tagName, "ul");
  assert.equal(list.children.length, 10);
  const sentDate = new Date(rows[0].created_at).toLocaleDateString("ja-JP");
  assert.equal(list.children[0].children[0].textContent, `#14 ・ ${sentDate} ・ 未読`);
  assert.equal(list.children[0].children[1].textContent, reply);
  assert.equal(list.children[0].children[1].style.whiteSpace, "pre-wrap");
  for (const state of states) assert.ok(b.history.textContent.includes(state));
  assert.match(list.children.at(-1).textContent, /#5 ・/);
  assert.ok(!b.history.textContent.includes("#4 ・"), "11件目以降は並べない");
  assert.equal(list.children[1].children.length, 1, "空の返事は空欄を増やさない");
});

test("送った報告がなければ空の案内を出す", async () => {
  const b = browser();
  b.open();
  await b.reply({ reports: [] });
  assert.equal(b.history.textContent, "送った報告はありません");
  assert.equal(b.history.children.length, 0);
});

test("APIが断ったときと通信失敗は案内を出し、開き直せば読み直す", async () => {
  const b = browser();
  b.open();
  await b.reply({ error: "ログインしてください" }, 403);
  assert.match(b.history.textContent, /送った報告を読み込めませんでした/);
  b.open();
  await b.fail();
  assert.match(b.history.textContent, /通信を確かめて/);
  b.open();
  await b.reply({ reports: [report(8, "読んだ", "確認しました")] });
  assert.equal(b.requests.length, 3);
  assert.match(b.history.textContent, /#8/);
  assert.ok(b.history.textContent.includes("確認しました"));
  assert.ok(!b.history.textContent.includes("読み込めませんでした"));
});

test("状態もHTMLにせず文字として表示する", async () => {
  const b = browser();
  b.open();
  await b.reply({ reports: [report(1, "<script>state</script>", "<b>返事</b>")] });
  assert.ok(b.history.textContent.includes("<script>state</script>"));
  assert.ok(b.history.textContent.includes("<b>返事</b>"));
});

test("送信成功後は履歴を読み直し、送った報告をすぐ表示する", async () => {
  const b = browser();
  b.open();
  await b.reply({ reports: [] });
  const submitted = b.submit();
  assert.equal(b.send.disabled, true);
  const request = b.requests.at(-1);
  assert.equal(request.options.method, "POST");
  assert.equal(request.options.headers["X-CSRFToken"], "csrf-test");
  await b.reply({ id: 19, message: "報告番号は19です" }, 201, request);
  await submitted;
  assert.equal(b.send.disabled, false);
  assert.equal(b.reset, true);
  assert.equal(b.requests.length, 3);
  assert.equal(b.requests[2].options.method, undefined);
  await b.reply({ reports: [report(19)] });
  assert.match(b.history.textContent, /#19 ・/);
});

test("ログインしておらず報告窓がないページでは通信しない", () => {
  const b = browser({ mounted: false });
  assert.equal(b.requests.length, 0);
});
