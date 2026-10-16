// マップの絵の見本（map-preview.html）: 定義データにある地形・施設・住宅の段階をすべて並べた
// 架空の島を作り、島の画面と同じ renderer.js で描く。絵を作るときと、その見直しに使う。

import { attachControls } from "./controls.js";
import { overlayList } from "./overlays/index.js";
import { tileToWorld } from "./projection.js";
import { drawIsland } from "./renderer.js";
import { loadSprites } from "./sprites.js";

const canvas = document.getElementById("preview");
const ctx = canvas.getContext("2d");
const params = new URLSearchParams(location.search);
const defs = await fetch(canvas.dataset.defs).then((r) => r.json());
const theme = defs.theme;
const useSprites = params.get("sprites") !== "0";
const sprites = useSprites ? await loadSprites(theme) : new Map();
const { map, labels } = buildMap(defs);
const view = { scale: Number(params.get("scale") || 1), offsetX: 0, offsetY: 0, dpr: window.devicePixelRatio || 1 };
const options = { sprites, overlay: overlayList(defs).find((o) => o.id === params.get("overlay")) || null };

document.getElementById("missing").textContent = useSprites ? missingText(defs, sprites) : "";

/** 見本の島: 1マスおきに1つずつ並べ、背の高いものが隣を隠しにくくする。 */
function buildMap(defs) {
  const items = [];
  const landTerrain = (f) => f.terrain.find((t) => defs.terrain[t]?.land) || "plain";
  for (const [tid, t] of Object.entries(defs.terrain)) items.push({ label: t.name, terrain: tid });
  for (const [fid, f] of Object.entries(defs.facilities)) {
    if (f.tileless || fid === defs.housing) continue;
    items.push({ label: f.name, terrain: landTerrain(f), facility: { kind: fid, built: true } });
  }
  for (const [sid, s] of Object.entries(defs.settlements)) {
    items.push({ label: s.name, terrain: "plain", facility: { kind: defs.housing, built: true, level: sid } });
  }
  const office = Object.keys(defs.facilities).find((k) => defs.facilities[k].category === "admin");
  const sample = Object.keys(defs.facilities).find((k) => defs.facilities[k].category === "industry");
  items.push({ label: "工事中", terrain: "plain", facility: { kind: sample, built: false } });
  items.push({ label: "止まっている", terrain: "plain", facility: { kind: sample, built: true, rate: 0, status: "no_input" } });
  items.push({ label: "首都", terrain: "plain", facility: { kind: office, built: true }, capital: true });
  items.push({ label: "送電線", terrain: "plain", power: true });
  items.push({ label: "開発禁止", terrain: "plain", zone: "no_development" });

  const cols = Math.ceil(Math.sqrt(items.length));
  const n = cols * 2 + 1;
  const size = n * n;
  const sea = Object.keys(defs.terrain).find((t) => !defs.terrain[t].land);
  const map = {
    size: n, terrain: Array(size).fill(sea), population: Array(size).fill(0), zone: Array(size).fill(""),
    power_line: Array(size).fill(0), pollution: Array(size).fill(0), happiness: Array(size).fill(null),
    power: Array(size).fill(null), facilities: [], capital: null,
    // 首都の建物の組み合わせ。?capital=議事堂-王宮 のように選べる（既定は軸ごとの最初の建物）
    capital_buildings: (params.get("capital") || capitalCombos(defs)[0] || "").split("-").filter(Boolean),
  };
  // 陸地の台を敷いてから並べる
  for (let y = 1; y < n - 1; y++) for (let x = 1; x < n - 1; x++) map.terrain[y * n + x] = "plain";
  const labels = [];
  items.forEach((item, k) => {
    const x = 1 + (k % cols) * 2;
    const y = 1 + Math.floor(k / cols) * 2;
    const i = y * n + x;
    map.terrain[i] = item.terrain;
    map.population[i] = 3000;
    if (item.zone) map.zone[i] = item.zone;
    if (item.power) {
      map.power_line[i] = 1;
      map.power_line[i + 1] = 1;
    }
    if (item.facility) map.facilities.push({ number: k + 1, x, y, rate: 1, status: "", ...item.facility });
    if (item.capital) map.capital = [x, y];
    labels.push({ x, y, text: item.label });
  });
  return { map, labels };
}

/** 首都の建物の組み合わせ（軸ごとに体制の建物を 1 つずつ選び、軸の順に "-" でつなぐ）。 */
function capitalCombos(defs) {
  let combos = [[]];
  for (const axis of Object.keys(defs.axes || {})) {
    const buildings = [
      ...new Set(
        Object.values(defs.regimes || {})
          .filter((r) => r.axis === axis && r.capital_building)
          .map((r) => r.capital_building),
      ),
    ];
    if (buildings.length) combos = combos.flatMap((c) => buildings.map((b) => [...c, b]));
  }
  return combos.filter((c) => c.length).map((c) => c.join("-"));
}

function missingText(defs, sprites) {
  const want = [
    ...Object.keys(defs.terrain).map((t) => `terrain/${t}`),
    ...Object.entries(defs.facilities)
      .filter(([fid, f]) => !f.tileless && fid !== defs.housing)
      .map(([, f]) => `facility/${f.sprite}`),
    ...Object.keys(defs.settlements).map((s) => `housing/${s}`),
    ...capitalCombos(defs).map((c) => `capital/${c}`),
  ];
  const missing = want.filter((k) => !sprites.has(k));
  return missing.length ? `絵がないもの（色の箱で描いています）: ${missing.join(", ")}` : "すべて絵があります";
}

function redraw() {
  drawIsland(ctx, map, defs, view, options);
  ctx.font = "11px sans-serif";
  ctx.textAlign = "center";
  for (const l of labels) {
    const p = tileToWorld(l.x + 1, l.y + 1, theme); // マスの下の頂点の少し下
    ctx.lineWidth = 3;
    ctx.strokeStyle = "rgba(0, 0, 0, 0.7)";
    ctx.strokeText(l.text, p.px, p.py + 4);
    ctx.fillStyle = "#fff";
    ctx.fillText(l.text, p.px, p.py + 4);
  }
}

function resize() {
  const r = canvas.getBoundingClientRect();
  view.dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(r.width * view.dpr);
  canvas.height = Math.round(r.height * view.dpr);
  const mid = tileToWorld(map.size / 2, map.size / 2, theme);
  view.offsetX = r.width / 2 - mid.px * view.scale;
  view.offsetY = r.height / 2 - mid.py * view.scale;
  redraw();
}

attachControls(canvas, view, { onChange: redraw, onTap() {} });
window.addEventListener("resize", resize);
resize();
