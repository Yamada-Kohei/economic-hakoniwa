// node --test tests/js/*.test.mjs
// 地図のマスの窓から施設のページ（UI-72・UI-78）へのリンク。
import test from "node:test";
import assert from "node:assert/strict";
import { TilePanel } from "../../server/static/js/island/panel.js";

test("マスの窓の施設に、施設のページへのリンクを添える", () => {
  const panel = Object.create(TilePanel.prototype);
  panel.el = { dataset: { facilityUrl: "/islands/3/facilities/0/" } };
  panel.orders = {};
  assert.equal(
    panel.facilityLink({ number: 15 }),
    '<p class="facility-page-link"><a href="/islands/3/facilities/15/">施設のページで命令を出す</a></p>',
  );
  panel.orders = null; // ほかの国の島（命令の欄なし）
  assert.match(panel.facilityLink({ number: 15 }), />施設のページを開く</);
  panel.el = { dataset: {} }; // URL を渡さない画面ではリンクを出さない
  assert.equal(panel.facilityLink({ number: 15 }), "");
});

test("施設のマスの窓には施設ごとの命令の欄を出さず、場所の命令と施設のページへの案内を出す（UI-78）", () => {
  const panel = Object.create(TilePanel.prototype);
  panel.el = { dataset: { facilityUrl: "/islands/3/facilities/0/" } };
  panel.orders = {};
  panel.defs = { housing: "housing", facilities: {} };
  const factory = {
    number: 17, kind: "factory", priority: 5, priority_hint: "", custom_name: "",
    accounting_target: "self_supporting", accounting_target_name: "独立採算", accounting_name: "国庫直轄",
    ownership_target: "privatize", ownership_target_name: "民営化", owner_name: "国営",
    recipe_choices: [{ recipe: "wood", name: "木材", inputs: [], outputs: [] }],
    subsidy_target: true, emergency_loan: true, resolve_methods: { transfer: "引き継ぎ" },
    expand: { label: "建て増し", facility: "factory", materials: [], turns: 3 },
  };
  const html = panel.actionsHtml({ x: 1, y: 2, place: "北村", facility: factory });
  for (const form of ["rename", "priority", "accounting", "recipe", "ownership", "subsidy", "emergency", "resolve"]) {
    assert.doesNotMatch(html, new RegExp(`data-form="${form}"`));
  }
  assert.doesNotMatch(html, /data-action="demolish"/);
  // 建て増し（工事）と地名は場所の命令なので地図に残す
  assert.match(html, /data-form="expand"/);
  assert.match(html, /data-form="place"/);
  assert.match(html, /<a href="\/islands\/3\/facilities\/17\/#actions">施設のページで命令を出す<\/a>/);
  // 町（住宅地）は施設のページを持たないので、撤去と国の住宅は地図に残す（UI-77）
  const town = panel.actionsHtml({ x: 1, y: 2, facility: { number: 3, kind: "housing" } });
  assert.match(town, /data-action="demolish"/);
  assert.match(town, /data-form="housing"/);
  assert.doesNotMatch(town, /施設のページ/);
  assert.doesNotMatch(panel.actionsHtml({ x: 1, y: 2, capital: true, facility: { number: 3, kind: "housing" } }), /data-action="demolish"/);
});

test("命令で出した生産方式の切り替えと補助金は、窓の施設の表に出す", () => {
  globalThis.document = { getElementById: () => ({ dataset: { change: "生産方式の切り替え", next: "次のターンに「{name}」に切り替えます。" } }) };
  const panel = Object.create(TilePanel.prototype);
  panel.el = { dataset: {} };
  panel.orders = {};
  panel.defs = { facilities: { factory: { recipes: {} } } };
  const html = panel.facilityHtml({
    number: 17, kind: "factory", name: "工場", kind_name: "工場", owner_name: "国営", accounting_name: "国庫直轄",
    built: true, stock: [], recipe_next_name: "木材", subsidies: ["生産補助 10%（あと 12 ターン）"],
  });
  assert.match(html, /<th>生産方式の切り替え<\/th><td>次のターンに「木材」に切り替えます。<\/td>/);
  assert.match(html, /<th>補助金<\/th><td>生産補助 10%（あと 12 ターン）<\/td>/);
});
