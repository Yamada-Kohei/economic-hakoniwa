// 島の画面: 定義データと島のマスを読み込み、俯瞰図を描く。

import { attachControls } from "./controls.js";
import { worldToTile, tileToWorld } from "./projection.js";
import { drawIsland } from "./renderer.js";

const canvas = document.getElementById("island-map");
const info = document.getElementById("tile-info");
const ctx = canvas.getContext("2d");

const [defs, map] = await Promise.all([
  fetch(canvas.dataset.defs).then((r) => r.json()),
  fetch(canvas.dataset.api).then((r) => r.json()),
]);
const theme = defs.theme;
const view = { scale: 1, offsetX: 0, offsetY: 0, dpr: window.devicePixelRatio || 1 };
let selected = null;
let pending = false;

function heightOf(x, y) {
  return defs.terrain[map.terrain[y * map.size + x]]?.height ?? 0;
}

function redraw() {
  if (pending) return;
  pending = true;
  requestAnimationFrame(() => {
    pending = false;
    drawIsland(ctx, map, defs, view, selected);
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
  // 島全体が収まるように拡大率と位置を決める
  const r = canvas.getBoundingClientRect();
  const n = map.size;
  const w = n * theme.tile_width;
  const h = n * theme.tile_height;
  view.scale = Math.min(r.width / w, r.height / h) * 0.95;
  const center = tileToWorld(n / 2, n / 2, theme);
  view.offsetX = r.width / 2 - center.px * view.scale;
  view.offsetY = r.height / 2 - center.py * view.scale;
}

async function showTile(tile) {
  selected = tile;
  redraw();
  if (!tile) {
    info.textContent = "";
    return;
  }
  const url = `/api/islands/${map.id}/tiles/${tile.x},${tile.y}`;
  const data = await fetch(url).then((r) => r.json());
  info.textContent = `(${data.x}, ${data.y}) ${data.terrain_name}　住民 ${data.population} 人`;
}

const controls = attachControls(canvas, view, {
  onChange: redraw,
  onTap(sx, sy) {
    const px = (sx - view.offsetX) / view.scale;
    const py = (sy - view.offsetY) / view.scale;
    showTile(worldToTile(px, py, map, heightOf, theme));
  },
});

document.querySelectorAll("[data-zoom]").forEach((button) => {
  button.addEventListener("click", () => {
    const r = canvas.getBoundingClientRect();
    controls.zoomAt(button.dataset.zoom === "in" ? 1.25 : 0.8, r.width / 2, r.height / 2);
  });
});

window.addEventListener("resize", resize);
fit();
resize();
