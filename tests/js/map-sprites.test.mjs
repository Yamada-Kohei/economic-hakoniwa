// 絵の読み込み: 使う絵を先に読み始め、1 枚読めるごとに知らせ、渡した Map に順に入ることを確かめる。
import test from "node:test";
import assert from "node:assert/strict";

const started = [];
globalThis.Image = class {
  constructor() { this.naturalWidth = 10; this.naturalHeight = 20; }
  set src(value) {
    started.push(value);
    queueMicrotask(() => this.onload && this.onload());
  }
};

const { loadSprites, spritePriorityKeys } = await import("../../server/static/js/map/sprites.js");

const theme = {
  base: "/sprites/",
  sprites: {
    "terrain/plain": "plain.svg",
    "terrain/sea": "sea.svg",
    "facility/farm": "farm.svg",
    "facility/farm@s2": "farm2.svg",
    "facility/mine": "mine.svg",
  },
};

test("priority の絵から読み始め、読めるごとに onLoad が呼ばれて into に入る", async () => {
  started.length = 0;
  const into = new Map();
  const loaded = [];
  const result = await loadSprites(theme, {
    into,
    priority: new Set(["facility/farm@s2"]),
    onLoad: (key) => loaded.push(key),
  });
  assert.equal(started[0], "/sprites/farm2.svg");
  assert.equal(result, into);
  assert.equal(into.size, 5);
  assert.deepEqual([...loaded].sort(), Object.keys(theme.sprites).sort());
  assert.deepEqual(into.get("terrain/plain"), { img: into.get("terrain/plain").img, w: 10, h: 20, ax: 5, ay: 20 });
});

test("priority がなければ全部をそのまま読み、返り値は新しい Map", async () => {
  const result = await loadSprites(theme);
  assert.equal(result.size, 5);
});

test("flag_anchor のある絵には倍率を掛けた flagAnchor が付き、ない絵には付かない", async () => {
  const result = await loadSprites({
    base: "/sprites/",
    sprite_scale: 2,
    sprites: {
      "capital/parliament-palace": { file: "cap.svg", flag_anchor: [27, 4] },
      "terrain/plain": "plain.svg",
    },
  });
  assert.deepEqual(result.get("capital/parliament-palace").flagAnchor, { x: 54, y: 8 });
  assert.equal(result.get("terrain/plain").flagAnchor, undefined);
});

test("その島で使う絵だけを priority に集める（施設の印付きの絵も含む）", () => {
  const keys = spritePriorityKeys(
    { ...theme, construction_looks: { farm: "field" }, sprites: { ...theme.sprites, construction: "c.svg", "construction@field": "cf.svg", "housing/town": "h.svg" } },
    { terrain: ["plain", "plain"], facilities: [{ built: false, kind: "farm" }, { built: true, kind: "farm" }, { built: true, kind: "housing", level: "town" }, { built: true, kind: "mine" }] },
    { housing: "housing", facilities: { mine: {}, farm: {} } },
  );
  for (const key of ["terrain/plain", "facility/farm", "facility/farm@s2", "facility/mine", "construction", "construction@field", "housing/town"]) {
    assert.ok(keys.has(key), key);
  }
  assert.ok(!keys.has("terrain/sea"));
});
