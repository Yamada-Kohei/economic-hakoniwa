// 島の画面: 定義データと島のマスを読み込み、俯瞰図・小さな地図・マスの情報・命令の列を動かす。

import { attachControls } from "./controls.js";
import { drawMinimap, minimapTile } from "./minimap.js";
import { overlays } from "./overlays/index.js";
import { lift, tileToWorld, worldToTile } from "./projection.js";
import { drawIsland } from "./renderer.js";
import { loadSprites } from "./sprites.js";
import { OrderQueue } from "../island/orders.js";
import { TilePanel } from "../island/panel.js";

const canvas = document.getElementById("island-map");
const ctx = canvas.getContext("2d");
const minimap = document.getElementById("minimap");
const overlaySelect = document.getElementById("overlay");

const [defs, map] = await Promise.all([
  fetch(canvas.dataset.defs).then((r) => r.json()),
  fetch(canvas.dataset.api).then((r) => r.json()),
]);
const theme = defs.theme;
const view = { scale: 1, offsetX: 0, offsetY: 0, dpr: window.devicePixelRatio || 1 };
const state = { selected: null, marked: new Set(), overlay: null, sprites: new Map() };
let pending = false;

function heightOf(x, y) {
  return defs.terrain[map.terrain[y * map.size + x]]?.height ?? 0;
}

function redraw() {
  if (pending) return;
  pending = true;
  requestAnimationFrame(() => {
    pending = false;
    drawIsland(ctx, map, defs, view, state);
    if (minimap) drawMinimap(minimap, map, defs, state.selected);
  });
}

function resize() {
  const r = canvas.getBoundingClientRect();
  view.dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(r.width * view.dpr);
  canvas.height = Math.round(r.height * view.dpr);
  redraw();
}

function fit() {
  // 陸地（と浅瀬）が収まるように拡大率と位置を決める。周りの海は少しだけ見せる
  const r = canvas.getBoundingClientRect();
  const n = map.size;
  let [x0, y0, x1, y1] = [n, n, -1, -1];
  for (let i = 0; i < n * n; i++) {
    if (map.terrain[i] === map.terrain[0]) continue; // 角のマス（外洋）と同じ地形は除く
    const x = i % n;
    const y = Math.floor(i / n);
    [x0, y0, x1, y1] = [Math.min(x0, x), Math.min(y0, y), Math.max(x1, x), Math.max(y1, y)];
  }
  if (x1 < 0) [x0, y0, x1, y1] = [0, 0, n - 1, n - 1];
  const span = x1 - x0 + y1 - y0 + 2;
  view.scale = Math.min(r.width / (span * theme.tile_width / 2), r.height / (span * theme.tile_height / 2)) * 0.9;
  centerOn((x0 + x1 + 1) / 2, (y0 + y1 + 1) / 2);
}

function centerOn(x, y) {
  const r = canvas.getBoundingClientRect();
  const p = tileToWorld(x, y, theme);
  view.offsetX = r.width / 2 - p.px * view.scale;
  view.offsetY = r.height / 2 - (p.py - lift(heightOf(Math.floor(x), Math.floor(y)), theme)) * view.scale;
}

const orders = map.mine ? new OrderQueue(document.getElementById("orders"), defs, map) : null;
const panel = new TilePanel(document.getElementById("tile-panel"), defs, map, {
  orders,
  marked: state.marked,
  onMarkedChange: redraw,
  mapArea: canvas.closest(".map-column"),
  notice: document.getElementById("order-notice"),
});

function select(tile) {
  state.selected = tile;
  redraw();
  panel.show(tile);
}

const controls = attachControls(canvas, view, {
  onChange: redraw,
  onTap(sx, sy) {
    const px = (sx - view.offsetX) / view.scale;
    const py = (sy - view.offsetY) / view.scale;
    const tile = worldToTile(px, py, map, heightOf, theme);
    if (tile && panel.marking) {
      panel.toggleMark(tile);
      return;
    }
    select(tile);
  },
});

document.querySelectorAll("[data-zoom]").forEach((button) => {
  button.addEventListener("click", () => {
    const r = canvas.getBoundingClientRect();
    controls.zoomAt(button.dataset.zoom === "in" ? 1.25 : 0.8, r.width / 2, r.height / 2);
  });
});

if (overlaySelect) {
  for (const o of overlays) overlaySelect.add(new Option(o.label, o.id));
  overlaySelect.addEventListener("change", () => {
    state.overlay = overlays.find((o) => o.id === overlaySelect.value) || null;
    redraw();
  });
}

if (minimap) {
  minimap.addEventListener("click", (e) => {
    const tile = minimapTile(minimap, map, e.clientX, e.clientY);
    if (!tile) return;
    centerOn(tile.x + 0.5, tile.y + 0.5);
    select(tile);
  });
}

window.addEventListener("resize", resize);
fit();
resize();

// 絵は読み込めたら描き直す（読み込むまでは色の箱で描く）
loadSprites(theme).then((sprites) => {
  state.sprites = sprites;
  redraw();
});

// ターンログのリンクなどから ?x=..&y=.. で来たら、そのマスを選ぶ
const params = new URLSearchParams(location.search);
if (params.has("x") && params.has("y")) {
  const tile = { x: Number(params.get("x")), y: Number(params.get("y")) };
  centerOn(tile.x + 0.5, tile.y + 0.5);
  select(tile);
}
