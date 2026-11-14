// 生産調整中のわけ（W-250）と、生産停止中（買い注文不足）の地図の印（UI-84）。
import test from "node:test";
import assert from "node:assert/strict";
import { slowdownHtml } from "../../server/static/js/island/slowdown.js";
import { TilePanel } from "../../server/static/js/island/panel.js";
import { drawStopped } from "../../server/static/js/map/renderer.js";

const SLOWDOWN = {
  stopped: false,
  target: 40,
  good: "fish",
  good_name: "魚",
  reasons: [
    { kind: "below_cost", text: "買い手の付け値が費用に届きません: <値段>" },
    { kind: "stock", text: "在庫 500 トンが売れ行きの見込みの約 5.0 ターン分あります。" },
  ],
  text: "買い手の付け値が費用に届きません: <値段>",
  hint: "「補助金の変更」で生産補助を出して費用の下限を下げることができます。",
};

test("slowdown reasons and the hint are listed and escaped", () => {
  const html = slowdownHtml(SLOWDOWN);
  assert.match(html, /生産調整中のわけ（魚）/);
  assert.match(html, /操業目標 40%/);
  assert.match(html, /&lt;値段&gt;/);
  assert.equal((html.match(/<li>/g) || []).length, 2);
  assert.match(html, /打てる手:<\/strong> 「補助金の変更」/);
  assert.match(slowdownHtml({ ...SLOWDOWN, stopped: true }), /生産を止めたわけ/);
  assert.equal(slowdownHtml(null), "");
});

test("facility window shows the reasons instead of the plain hint row", () => {
  const panel = Object.create(TilePanel.prototype);
  panel.defs = { facilities: { fishing_port: { recipes: { default: {} } } } };
  const facility = {
    kind: "fishing_port", kind_name: "漁港", name: "テスト漁港", owner: "state",
    owner_name: "国営", accounting_name: "国庫直轄", built: true, stock: [],
    status_text: "生産調整中", hint: SLOWDOWN.hint, slowdown: SLOWDOWN,
  };
  const html = panel.facilityHtml(facility, null);
  assert.match(html, /生産調整中のわけ/);
  assert.doesNotMatch(html, /できること/);
  const plain = panel.facilityHtml({ ...facility, slowdown: undefined }, null);
  assert.match(plain, /できること/);
});

test("the red stop mark is drawn only when production was cut to zero", () => {
  const calls = [];
  const ctx = { beginPath() {}, arc() { calls.push("arc"); }, fill() {}, set fillStyle(v) { calls.push(v); } };
  const defs = { housing: "housing" };
  // 止まりかはサーバー（理由の表）が stopped で決める（W-34）
  drawStopped(ctx, { kind: "fishing_port", rate: 400, status: "target", stopped: false }, defs, 0, 0, 8);
  assert.deepEqual(calls, []);
  drawStopped(ctx, { kind: "fishing_port", rate: 0, status: "target", stopped: true }, defs, 0, 0, 8);
  assert.deepEqual(calls, ["#e53935", "arc"]);
  calls.length = 0;
  drawStopped(ctx, { kind: "housing", rate: 0, status: "power", stopped: true }, defs, 0, 0, 8);
  assert.deepEqual(calls, []);
});
