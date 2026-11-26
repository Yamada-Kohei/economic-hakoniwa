// node --test tests/js/map-wave2.test.mjs
// グラフィック強化 第2波の決まり: 煙の出る施設と粒（案1）、変化マスの光（案3）、
// 表示の切り替えの覚え方（案44・47）、ズーム別の詳しさ（案46）、明暗の枠（P2 の 13）。
import test from "node:test";
import assert from "node:assert/strict";
import {
  GLOW_SECONDS,
  LIGHT_KEY,
  SMOKE_KEY,
  detailLevel,
  glowAlpha,
  isSmokeSource,
  phaseFor,
  readLightMode,
  readSmokeShown,
  smokePuffs,
  writeLightMode,
  writeSmokeShown,
} from "../../server/static/js/map/anim.js";
import {
  THEME_KEY,
  applyTheme,
  readThemePref,
  themeForPref,
  writeThemePref,
} from "../../server/static/js/theme.js";

function memory() {
  const items = new Map();
  return { getItem: (k) => items.get(k) ?? null, setItem: (k, v) => items.set(k, String(v)) };
}

const broken = {
  getItem: () => {
    throw new Error("denied");
  },
  setItem: () => {
    throw new Error("denied");
  },
};

const DEFS = {
  facilities: {
    factory: { category: "industry" },
    thermal_power_plant: { category: "power" },
    hydro_power_plant: { category: "power" },
    farm: { category: "primary" },
  },
};

test("煙は既定で出し、消したことを覚える", () => {
  const storage = memory();
  assert.equal(readSmokeShown(storage), true);
  writeSmokeShown(storage, false);
  assert.equal(storage.getItem(SMOKE_KEY), "off");
  assert.equal(readSmokeShown(storage), false);
  writeSmokeShown(storage, true);
  assert.equal(readSmokeShown(storage), true);
});

test("軽量モードは利用者の選択が優先。なければ既定（狭い画面では入、PCでは切）", () => {
  const storage = memory();
  assert.equal(readLightMode(storage, false), false); // PC の既定は切
  assert.equal(readLightMode(storage, true), true); // 狭い画面の既定は入
  writeLightMode(storage, true);
  assert.equal(storage.getItem(LIGHT_KEY), "on");
  assert.equal(readLightMode(storage, false), true); // 覚えた選択が既定に勝つ
  writeLightMode(storage, false);
  assert.equal(readLightMode(storage, true), false);
});

test("覚えられない窓でも止まらず、既定に戻る", () => {
  assert.doesNotThrow(() => {
    writeSmokeShown(broken, true);
    writeLightMode(broken, true);
  });
  assert.equal(readSmokeShown(broken), true);
  assert.equal(readLightMode(broken, false), false);
  assert.equal(readLightMode(broken, true), true);
});

test("位相は同じマスでいつも同じで、0〜1 に収まる", () => {
  for (const [x, y] of [[0, 0], [3, 5], [17, 2], [40, 40]]) {
    const p = phaseFor(x, y);
    assert.equal(p, phaseFor(x, y));
    assert.ok(p >= 0 && p < 1, `${x},${y} の位相 ${p}`);
  }
  assert.notEqual(phaseFor(3, 5), phaseFor(5, 3));
});

test("煙の出る施設: 稼働中の工場と火力発電所だけ", () => {
  const base = { built: true, stopped: false };
  assert.equal(isSmokeSource({ ...base, kind: "factory" }, DEFS), true);
  assert.equal(isSmokeSource({ ...base, kind: "thermal_power_plant" }, DEFS), true);
  assert.equal(isSmokeSource({ ...base, kind: "hydro_power_plant" }, DEFS), false);
  assert.equal(isSmokeSource({ ...base, kind: "farm" }, DEFS), false);
  assert.equal(isSmokeSource({ ...base, kind: "factory", stopped: true }, DEFS), false);
  assert.equal(isSmokeSource({ ...base, kind: "factory", built: false }, DEFS), false);
  assert.equal(isSmokeSource(null, DEFS), false);
});

test("ズーム別の詳しさ: 近く・中・遠くの3段", () => {
  assert.equal(detailLevel(1.5), "near");
  assert.equal(detailLevel(0.9), "near");
  assert.equal(detailLevel(0.7), "mid");
  assert.equal(detailLevel(0.5), "mid");
  assert.equal(detailLevel(0.25), "far");
});

test("煙の粒: 詳しさで数が変わり、昇るほど大きく薄くなる", () => {
  const f = { x: 4, y: 6 };
  assert.equal(smokePuffs(f, 0, "near").length, 3);
  assert.equal(smokePuffs(f, 0, "mid").length, 2);
  assert.equal(smokePuffs(f, 0, "far").length, 1);
  const sorted = [...smokePuffs(f, 1.2, "near")].sort((a, b) => a.rise - b.rise);
  for (const puff of sorted) {
    assert.ok(puff.rise >= 0 && puff.rise < 1);
    assert.ok(puff.alpha >= 0 && puff.alpha <= 0.3);
    assert.ok(puff.dy < 0); // てっぺんより上
    // 粒の大きさは半径でマス幅の 0.045〜0.122（直径 6〜9px 程度の小ささ。#6090）
    assert.ok(puff.r >= 0.045 && puff.r <= 0.122, `粒の半径 ${puff.r}`);
  }
  for (let k = 1; k < sorted.length; k++) {
    assert.ok(sorted[k].r > sorted[k - 1].r, "昇るほど広がる");
  }
  // 同じ時刻では同じ姿（静止表示の土台）
  assert.deepEqual(smokePuffs(f, 2.5, "near"), smokePuffs(f, 2.5, "near"));
});

test("変化マスの光: 一度だけ強くなって、終われば 0", () => {
  assert.equal(glowAlpha(0), 0);
  assert.equal(glowAlpha(-1), 0);
  assert.equal(glowAlpha(GLOW_SECONDS), 0);
  assert.equal(glowAlpha(GLOW_SECONDS + 1), 0);
  const mid = glowAlpha(GLOW_SECONDS / 2);
  assert.ok(mid > 0.5 && mid <= 1, `途中の強さ ${mid}`);
});

test("明暗の枠: 覚えたときだけ OS に合わせ、既定では属性を付けない", () => {
  const storage = memory();
  assert.equal(readThemePref(storage), null);
  assert.equal(themeForPref(null, true), null);
  assert.equal(themeForPref("os", true), "dark");
  assert.equal(themeForPref("os", false), "light");
  writeThemePref(storage, "os");
  assert.equal(storage.getItem(THEME_KEY), "os");
  assert.equal(readThemePref(storage), "os");

  const attrs = new Map();
  const doc = {
    documentElement: {
      setAttribute: (k, v) => attrs.set(k, v),
      removeAttribute: (k) => attrs.delete(k),
    },
  };
  assert.equal(applyTheme(doc, memory(), true), null);
  assert.equal(attrs.has("data-theme"), false);
  assert.equal(applyTheme(doc, storage, true), "dark");
  assert.equal(attrs.get("data-theme"), "dark");
  assert.equal(applyTheme(doc, storage, false), "light");
  assert.equal(attrs.get("data-theme"), "light");
});
