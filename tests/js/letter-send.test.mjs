// node --test tests/js/letter-send.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../../server/static/js/letter-send.js", import.meta.url), "utf8");

function formPage(mounted = true) {
  const formHandlers = {};
  const windowHandlers = {};
  const button = { disabled: false };
  const form = {
    querySelector: () => button,
    addEventListener: (name, handler) => { formHandlers[name] = handler; },
  };
  const context = {
    document: { querySelector: () => mounted ? form : null },
    window: { addEventListener: (name, handler) => { windowHandlers[name] = handler; } },
  };
  vm.runInNewContext(source, context);
  return { button, formHandlers, windowHandlers };
}

test("最初の送信でボタンを無効にし、応答待ちの再送を止める", () => {
  const { button, formHandlers } = formPage();
  let prevented = 0;
  const event = { preventDefault: () => { prevented++; } };
  formHandlers.submit(event);
  assert.equal(button.disabled, true);
  assert.equal(prevented, 0);
  formHandlers.submit(event);
  assert.equal(prevented, 1);
});

test("戻る操作で復元した入力欄から再び送信できる", () => {
  const { button, formHandlers, windowHandlers } = formPage();
  const event = { preventDefault: () => assert.fail("送信できる") };
  formHandlers.submit(event);
  windowHandlers.pageshow();
  assert.equal(button.disabled, false);
  formHandlers.submit(event);
  assert.equal(button.disabled, true);
});

test("送信欄のないページでも例外を出さない", () => {
  assert.doesNotThrow(() => formPage(false));
});
