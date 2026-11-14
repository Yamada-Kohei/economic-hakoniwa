// node --test tests/js/site-news.test.mjs
// 更新のお知らせの窓（UI-96）: 開いたら1度だけ出し、すぐ「ここまで見た」を送る。
import test from "node:test";
import assert from "node:assert/strict";

globalThis.document = undefined; // 読み込んだときに自動で出さない
const { showNews } = await import("../../server/static/js/site-news.js");

function fakeDialog() {
  return {
    open: false,
    dataset: { url: "/api/updates/seen", last: "42" },
    showModal() { this.open = true; },
    setAttribute() { this.open = true; },
  };
}

test("shows once and posts the last update number", async () => {
  const calls = [];
  const fetchImpl = async (url, init) => { calls.push({ url, init }); return { ok: true }; };
  globalThis.document = { querySelector: () => ({ value: "tok" }), cookie: "" };
  const dialog = fakeDialog();
  assert.equal(showNews(dialog, fetchImpl), true);
  assert.equal(dialog.open, true);
  assert.equal(showNews(dialog, fetchImpl), false); // 開いている間は出し直さない
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "/api/updates/seen");
  assert.equal(calls[0].init.method, "POST");
  assert.deepEqual(JSON.parse(calls[0].init.body), { last: 42 });
  assert.equal(calls[0].init.headers["X-CSRFToken"], "tok");
});

test("no dialog means nothing to show", () => {
  assert.equal(showNews(null), false);
});
