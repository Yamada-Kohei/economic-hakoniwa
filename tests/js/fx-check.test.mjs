// node --test tests/js/fx-check.test.mjs
import test from "node:test";
import assert from "node:assert/strict";

import { marketWarning } from "../../server/static/js/reports/fx-check.js";

function form({ offered, wanted, side = "buy", amount = "100", check = true }) {
  const dataset = {};
  if (offered !== undefined) dataset.offered = String(offered);
  if (wanted !== undefined) dataset.wanted = String(wanted);
  return {
    dataset: check ? { marketCheck: "" } : {},
    elements: {
      currency: { selectedOptions: [{ dataset, textContent: " 円 " }] },
      side: { value: side },
      amount: { value: amount },
    },
  };
}

test("売りが無かった通貨を買う介入には確かめを出す（UI-80）", () => {
  const text = marketWarning(form({ offered: 0, wanted: 5 }));
  assert.match(text, /円を売る注文は市場にありませんでした/);
  assert.equal(marketWarning(form({ offered: 3, wanted: 0 })), "");
});

test("買いが無かった通貨を売る介入には確かめを出す", () => {
  assert.match(marketWarning(form({ offered: 5, wanted: 0, side: "sell" })), /円を買う注文/);
  assert.equal(marketWarning(form({ offered: 0, wanted: 2, side: "sell" })), "");
});

test("記録のない通貨・額 0（やめる）・確かめのない欄では聞かない", () => {
  assert.equal(marketWarning(form({})), "");
  assert.equal(marketWarning(form({ offered: 0, amount: "0" })), "");
  assert.equal(marketWarning(form({ offered: 0, check: false })), "");
});
