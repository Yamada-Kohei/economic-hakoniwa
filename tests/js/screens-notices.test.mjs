import test from "node:test";
import assert from "node:assert/strict";
import { TilePanel } from "../../server/static/js/island/panel.js";
import { OrderQueue } from "../../server/static/js/island/orders.js";
import { formatValue } from "../../server/static/js/reports/common.js";

test("通貨記号の英数字と額を分け、記号一字と負数も整える", () => {
  for (const [symbol, prefix] of [["RYO", "RYO "], ["R2", "R2 "], ["¤", "¤"], ["$", "$"], ["", ""]]) {
    assert.equal(formatValue(12145, "money", symbol, true), `${prefix}12,145`);
    assert.equal(formatValue(-12145, "money", symbol, true), `-${prefix}12,145`);
    assert.equal(formatValue(12145, `money:${symbol}`, "", true), `${prefix}12,145`);
  }
});

test("建設の見積もりは選んだ方式の人手と現行の建設費を出す", () => {
  const panel = Object.create(TilePanel.prototype);
  panel.defs = { facilities: { farm: {
    workers: 500, construction: { turns: 2, labor: 200, materials: { wood: 5 } },
    recipes: {
      default: { workers: 500 },
      luxury_crop_mechanized: {
        workers: 170,
        build_hint: "機械化の方式・段階の工事が必要<確認>",
        construction: { turns: 5, materials: { wood: 5, machinery: 2 } },
      },
    },
  } }, goods: { wood: { name: "木材", unit: "トン" } } };
  assert.match(panel.buildInfo("farm", "default"), /完成後の従業者 500 人/);
  const mechanized = panel.buildInfo("farm", "luxury_crop_mechanized");
  // 段階の作り方は、段階までの工事を足した工期と資材（W-257）。建設労働者は手作業と同じ
  assert.match(mechanized, /工期 5 ターン.*建設労働者 200 人.*完成後の従業者 170 人.*木材 5 トン・machinery 2/);
  assert.match(mechanized, /段階の工事が必要&lt;確認&gt;/);
  let checked = false;
  const estimate = {};
  const form = {
    dataset: { form: "build" },
    elements: { facility: { value: "farm" }, recipe: { value: "luxury_crop_mechanized" } },
    querySelector: () => estimate,
  };
  panel.checkBuild = () => { checked = true; };
  panel.handleChange({ target: { name: "recipe", form, matches: () => false } });
  assert.equal(estimate.innerHTML, mechanized);
  assert.ok(checked);
});

test("倉庫の量の見出しは買い・売り・備蓄で切り替える", () => {
  const queue = Object.create(OrderQueue.prototype);
  const label = {};
  const form = { elements: { kind: { value: "buy" } }, querySelector: () => label };
  for (const [kind, text] of [["buy", "1ターンに買う量"], ["sell", "1ターンに売る量"], ["reserve", "備蓄の目標量"], ["stop", "1ターンの売り買いの量／備蓄の目標量"]]) {
    form.elements.kind.value = kind;
    queue.standingQuantityLabel(form);
    assert.equal(label.textContent, text);
  }
});
