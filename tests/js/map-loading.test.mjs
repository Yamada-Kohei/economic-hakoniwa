// 地図の初期化と、モジュールに依存しない読み込み失敗の知らせを確かめる。
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../../server/static/js/map/main.js", import.meta.url), "utf8")
  .replace(/^import .*;\r?\n/gm, "");
const template = readFileSync(new URL("../../server/templates/web/island.html", import.meta.url), "utf8");
const watchdog = template.match(/<script>([\s\S]*?)<\/script>/)[1];

function environment({ failedUrl, failedDraw = false } = {}) {
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      hidden: true, dataset: {}, style: {},
      addEventListener(event, callback) { this[event] = callback; },
      getBoundingClientRect: () => ({ width: 640, height: 320 }),
      getContext: () => ({}), querySelector: () => element("close"),
    });
    return elements.get(id);
  }
  element("island-map").dataset = { defs: "/defs", api: "/map" };
  const frames = [];
  const timers = [];
  let reloads = 0;
  let drawings = 0;
  const context = {
    document: { getElementById: element, querySelectorAll: () => [], addEventListener() {} },
    window: { devicePixelRatio: 1, matchMedia: () => ({ matches: false, addEventListener() {} }), addEventListener() {} },
    location: { search: "", reload: () => { reloads += 1; } },
    setTimeout: (callback, delay) => timers.push({ callback, delay }),
    requestAnimationFrame: (callback) => frames.push(callback),
    URLSearchParams,
    ResizeObserver: class { observe() {} },
    mountRecentChanges() {},
    async getJson(url) {
      if (url === failedUrl) throw new Error("読み込めません");
      return url === "/defs"
        ? { theme: { tile_width: 64, tile_height: 32 }, terrain: { plain: { height: 0 } } }
        : { size: 1, terrain: ["plain"], mine: false };
    },
    words: () => ({ hidden: "非公開" }), overlayList: () => [],
    tileToWorld: () => ({ px: 0, py: 0 }), lift: () => 0,
    attachControls: () => ({}),
    MissileTarget: class {}, TilePanel: class {},
    drawIsland() { if (failedDraw) throw new Error("描けません"); drawings += 1; },
    drawMinimap() {}, loadSprites: async () => new Map(),
  };
  return { context: vm.createContext(context), element, frames, timers,
    get reloads() { return reloads; }, get drawings() { return drawings; } };
}

for (const failedUrl of ["/defs", "/map"]) {
  test(`${failedUrl} を読めなければ直ちに再読み込みを知らせる`, async () => {
    const env = environment({ failedUrl });
    await assert.rejects(vm.runInContext(`(async () => { ${source}\n })()`, env.context));
    assert.equal(env.element("map-load-notice").hidden, false);
    assert.equal(env.element("island-map").dataset.ready, undefined);
  });
}

test("描けたときだけ完了の印を付け、遅れて成功したら知らせを閉じる", async () => {
  const env = environment();
  vm.runInContext(watchdog, env.context);
  assert.equal(env.timers[0].delay, 15000);
  env.timers[0].callback();
  assert.equal(env.element("map-load-notice").hidden, false);
  await vm.runInContext(`(async () => { ${source}\n })()`, env.context);
  env.frames.shift()();
  assert.equal(env.drawings, 1);
  assert.equal(env.element("island-map").dataset.ready, "1");
  assert.equal(env.element("map-load-notice").hidden, true);
  env.timers[0].callback();
  assert.equal(env.element("map-load-notice").hidden, true);
});

test("描画に失敗したら完了の印を付けずに知らせる", async () => {
  const env = environment({ failedDraw: true });
  await vm.runInContext(`(async () => { ${source}\n })()`, env.context);
  env.frames.shift()();
  assert.equal(env.element("island-map").dataset.ready, undefined);
  assert.equal(env.element("map-load-notice").hidden, false);
});

test("モジュールが起動しなくても15秒後に知らせ、ボタンで再読み込みする", () => {
  const env = environment();
  vm.runInContext(watchdog, env.context);
  assert.equal(env.element("map-load-notice").hidden, true);
  assert.equal(env.timers[0].delay, 15000);
  env.timers[0].callback();
  assert.equal(env.element("map-load-notice").hidden, false);
  env.element("map-reload").click();
  assert.equal(env.reloads, 1);
});
