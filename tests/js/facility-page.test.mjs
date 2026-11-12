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
    '<p class="facility-page-link"><a href="/islands/3/facilities/15/">施設のページを開く（命令と帳簿の推移）</a></p>',
  );
  panel.orders = null; // ほかの国の島（命令の欄なし）
  assert.match(panel.facilityLink({ number: 15 }), />施設のページを開く</);
  panel.el = { dataset: {} }; // URL を渡さない画面ではリンクを出さない
  assert.equal(panel.facilityLink({ number: 15 }), "");
});
