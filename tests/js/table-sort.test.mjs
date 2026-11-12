// node --test tests/js/table-sort.test.mjs
// 表の並べ替え（UI-86）: ますの字を数として読む決まりを確かめる。
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../../server/static/js/table-sort.js", import.meta.url), "utf8");

function load() {
  const window = {};
  const document = {
    readyState: "loading",
    addEventListener() {},
  };
  vm.runInNewContext(source, { window, document, Node: { ELEMENT_NODE: 1 } });
  return window.hakoniwaTableSort;
}

test("ますの字を数に読む（通貨・桁の区切り・%・単位・注記）", () => {
  const { numberOf } = load();
  assert.equal(numberOf("1,234"), 1234);
  assert.equal(numberOf("¥12,000"), 12000);
  assert.equal(numberOf("+3.5%"), 3.5);
  assert.equal(numberOf("−2.0%"), -2);
  assert.equal(numberOf("1,200 トン"), 1200);
  assert.equal(numberOf("0 / 20000"), 0);
});

test("空や「-」は値なし（いつも最後）、名前は数にしない", () => {
  const { numberOf } = load();
  assert.equal(numberOf(""), null);
  assert.equal(numberOf("-"), null);
  assert.ok(Number.isNaN(numberOf("農場")));
  assert.ok(Number.isNaN(numberOf("サンプル島1市農産")));
});
