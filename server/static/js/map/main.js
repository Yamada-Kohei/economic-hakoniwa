// 島の画面: 定義データと島のマスを読み込み、俯瞰図・小さな地図・マスの情報・命令の列を動かす。
// マスの情報は、押したマスのそばに出す小さな窓（ポップオーバー）に出す。右上の × と Esc で閉じ、
// ほかのマスを押すとそのマスの情報だけに替わる。地図を動かすと窓もマスに付いて動く。

import { attachControls } from "./controls.js";
import { drawMinimap, minimapTile } from "./minimap.js";
import { overlayList } from "./overlays/index.js";
import { lift, tileToWorld, worldToTile } from "./projection.js";
import { drawIsland } from "./renderer.js";
import { loadSprites } from "./sprites.js";
import { OrderQueue } from "../island/orders.js";
import { TilePanel } from "../island/panel.js";

const canvas = document.getElementById("island-map");
const ctx = canvas.getContext("2d");
const minimap = document.getElementById("minimap");
const overlaySelect = document.getElementById("overlay");
const overlayLegend = document.getElementById("overlay-legend");
const popover = document.getElementById("tile-popover");
const narrow = window.matchMedia("(max-width: 760px)"); // 狭い画面では窓を下から出す板にする（site.css）
const POPOVER_GAP = 12; // マスと窓の間（ピクセル）
const MAP_EDGE = 8; // 窓を地図の端からこれだけ離す（右は拡大縮小のボタンを避ける）

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
    placePopover();
  });
}

/** マスの中ほどの、地図の上の位置（CSS ピクセル）。 */
function screenOf({ x, y }) {
  const p = tileToWorld(x + 0.5, y + 0.5, theme);
  return { sx: p.px * view.scale + view.offsetX, sy: (p.py - lift(heightOf(x, y), theme)) * view.scale + view.offsetY };
}

/** 狭い画面で、下から出した板に隠れないように、選んだマスを画面の上のほうまで送る。 */
function revealAboveSheet(tile) {
  const top = canvas.getBoundingClientRect().top + screenOf(tile).sy;
  const limit = window.innerHeight - popover.offsetHeight - 24;
  if (top < 8 || top > limit) window.scrollBy({ top: top - Math.min(limit, window.innerHeight / 4), behavior: "smooth" });
}

/**
 * マスの情報の窓を、選んだマスのそばに置く。マスの右に入らなければ左に、どちらにも入らなければ広い方に
 * 寄せ、上下は地図の中に収める。狭い画面では CSS が下に留めるので位置を決めない。
 */
function placePopover() {
  popover.hidden = !state.selected;
  if (!state.selected || narrow.matches) {
    popover.style.left = popover.style.top = "";
    return;
  }
  const r = canvas.getBoundingClientRect();
  const { sx, sy } = screenOf(state.selected);
  const w = popover.offsetWidth;
  const h = popover.offsetHeight;
  const gap = (theme.tile_width / 2) * view.scale + POPOVER_GAP;
  const right = r.width - MAP_EDGE - 48; // 拡大縮小のボタンの幅を空ける
  let left = sx + gap;
  if (left + w > right) {
    const leftSide = sx - gap - w;
    left = leftSide >= MAP_EDGE ? leftSide : sx > r.width / 2 ? MAP_EDGE : right - w;
  }
  popover.style.left = `${Math.max(MAP_EDGE, Math.min(left, right - w))}px`;
  popover.style.top = `${Math.max(MAP_EDGE, Math.min(sy - h / 2, r.height - MAP_EDGE - h))}px`;
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
const overlays = overlayList(defs);
const panel = new TilePanel(document.getElementById("tile-panel"), defs, map, {
  orders,
  marked: state.marked,
  onMarkedChange: redraw,
  tools: document.getElementById("mark-tools"),
  notice: document.getElementById("order-notice"),
  // インフラの工事のマスを選ぶ間は、その種類の整備度を重ねて見せる
  onInfraKind: (kind) => showOverlay(`infra:${kind}`),
});

async function select(tile) {
  state.selected = tile;
  redraw();
  await panel.show(tile);
  if (tile && narrow.matches && state.selected === tile) revealAboveSheet(tile);
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

/** 重ね表示を切り替える。要るデータ（運賃の率など）は、初めて選んだときに地図の API から読む。 */
async function showOverlay(id) {
  const overlay = overlays.find((o) => o.id === id) || null;
  if (overlaySelect) overlaySelect.value = overlay ? overlay.id : "";
  if (overlayLegend) overlayLegend.textContent = overlay?.legend || "";
  if (overlay?.query && overlay.fields.some((f) => map[f] === undefined)) {
    if (overlayLegend) overlayLegend.textContent = "読み込み中…";
    try {
      const extra = await fetch(`${canvas.dataset.api}?${overlay.query}`).then((r) => r.json());
      for (const f of overlay.fields) map[f] = extra[f];
    } catch {
      if (overlayLegend) overlayLegend.textContent = "読み込めませんでした。";
      return;
    }
    if (overlaySelect && overlaySelect.value !== overlay.id) return; // 待つ間に別のものが選ばれた
    if (overlayLegend) overlayLegend.textContent = overlay.legend || "";
  }
  state.overlay = overlay;
  redraw();
}

if (overlaySelect) {
  for (const o of overlays) overlaySelect.add(new Option(o.label, o.id));
  overlaySelect.addEventListener("change", () => showOverlay(overlaySelect.value));
}

if (minimap) {
  minimap.addEventListener("click", (e) => {
    const tile = minimapTile(minimap, map, e.clientX, e.clientY);
    if (!tile) return;
    centerOn(tile.x + 0.5, tile.y + 0.5);
    if (panel.marking) {
      panel.toggleMark(tile);
      return;
    }
    select(tile);
  });
}

popover.querySelector("[data-close]").addEventListener("click", () => select(null));
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && panel.marking) {
    panel.endMarking();
    return;
  }
  // 入力欄で書いている途中は閉じない（書きかけを消さないように）
  if (e.key === "Escape" && state.selected && !e.target.closest?.("input, select, textarea")) select(null);
});
// 窓の中身を読み込んだり書き換えたりして大きさが変わったら、置き直す
new ResizeObserver(() => placePopover()).observe(popover);
narrow.addEventListener("change", () => placePopover());

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
