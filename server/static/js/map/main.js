// 島の画面: 定義データと島のマスを読み込み、俯瞰図・小さな地図・マスの情報・命令の列を動かす。
// マスの情報は、押したマスのそばに出す小さな窓（ポップオーバー）に出す。右上の × と Esc で閉じ、
// ほかのマスを押すとそのマスの情報だけに替わる。地図を動かすと窓もマスに付いて動く。

import { attachControls } from "./controls.js";
import { changeMarks, readChangesShown, writeChangesShown } from "./changes.js";
import { lensMarks, readLensShown, writeLensShown } from "./history.js";
import { readFlagShown, writeFlagShown } from "./flagtoggle.js";
import { drawMinimap, minimapTile } from "./minimap.js";
import { BARS, FILL, overlayLegend, readOverlayMode, writeOverlayMode } from "./overlays/bars.js";
import { overlayList } from "./overlays/index.js";
import { lift, tileToWorld, worldToTile } from "./projection.js";
import { drawIsland } from "./renderer.js";
import { drawRuler, readRulerShown, writeRulerShown } from "./ruler.js";
import { placeShips, readFleetShown, readMerchantShown, shipCounts, writeFleetShown, writeMerchantShown } from "./ships.js";
import { loadFlag, loadSprites, spritePriorityKeys } from "./sprites.js";
import { OrderQueue } from "../island/orders.js";
import { TilePanel } from "../island/panel.js";
import { MissileTarget } from "../island/missiles.js";
import { words } from "../military/common.js";
import { getJson } from "../island/api.js";


const canvas = document.getElementById("island-map");
const ctx = canvas.getContext("2d");
const minimap = document.getElementById("minimap");
const overlaySelect = document.getElementById("overlay");
const legendBox = document.getElementById("overlay-legend");
const barsToggle = document.getElementById("overlay-bars");
const popover = document.getElementById("tile-popover");
const narrow = window.matchMedia("(max-width: 760px)"); // 狭い画面では窓を下から出す板にする（site.css）
const POPOVER_GAP = 12; // マスと窓の間（ピクセル）
const MAP_EDGE = 8; // 窓を地図の端からこれだけ離す（右は拡大縮小のボタンを避ける）

function showLoadFailure() {
  document.getElementById("map-load-notice").hidden = false;
}

let defs, map;
try {
  [defs, map] = await Promise.all([
    getJson(canvas.dataset.defs),
    getJson(canvas.dataset.api),
  ]);
} catch (error) {
  showLoadFailure();
  throw error;
}
const theme = defs.theme;
const view = { scale: 1, offsetX: 0, offsetY: 0, dpr: window.devicePixelRatio || 1 };
// 重ね表示の表し方（棒か塗り分けか。UI-79）はブラウザに覚える。覚えていなければ棒
const storage = (() => {
  try {
    return window.localStorage;
  } catch {
    return null; // 覚えられない窓では毎回棒に戻る
  }
})();
const state = {
  selected: null, marked: new Set(), overlay: null, overlayMode: readOverlayMode(storage),
  sprites: new Map(), flag: null, undergroundLabel: words().hidden,
  ruler: readRulerShown(storage), // 縁の目盛りと座標（UI-85）。既定は出さない
  changes: readChangesShown(storage), changeMarks: null, // 前のターンから変わったマスの印（案3 の静止版）。既定は出さない
  lens: readLensShown(storage), lensMarks: null, // 変化レンズ（案29）。既定は出さない
  historyTurn: null, snapshotMap: null, historyNow: "", // 地図の変遷（案29）: 選んだ節目の写しと、その見比べ方
  flagShown: readFlagShown(storage), // 首都の旗（案23）を出すか。既定は出す
  merchantShown: readMerchantShown(storage), // 港の商船（案6）を出すか。既定は出す
  fleetShown: readFleetShown(storage), // 母港の艦隊（W-100）を出すか。既定は出す
};
let pending = false;

function heightOf(x, y) {
  return defs.terrain[map.terrain[y * map.size + x]]?.height ?? 0;
}

function redraw() {
  if (pending) return;
  pending = true;
  requestAnimationFrame(() => {
    pending = false;
    try {
      if (state.snapshotMap) {
        // 昔の地図（案29）: 節目の写しを、同じ位置・倍率のまま描く。重ね表示・船・部隊は
        // 今の地図の物なので出さない。小さな地図は今の姿のまま（位置の目印）
        drawIsland(ctx, state.snapshotMap, defs, view, { sprites: state.sprites });
        if (minimap) drawMinimap(minimap, map, defs, state.selected);
        canvas.dataset.ready = "1";
        document.getElementById("map-load-notice").hidden = true;
        return;
      }
      const shipsShown = { merchant: state.merchantShown, fleet: state.fleetShown };
      drawIsland(ctx, map, defs, view, {
        ...state,
        flag: state.flagShown ? state.flag : null,
        ships: placeShips(map, defs, shipsShown),
      });
      refreshShipsLegend(shipCounts(map, defs, shipsShown));
      if (state.ruler) drawRuler(ctx, map, view, theme);
      if (minimap) drawMinimap(minimap, map, defs, state.selected);
      placePopover();
      canvas.dataset.ready = "1";
      document.getElementById("map-load-notice").hidden = true;
    } catch {
      showLoadFailure();
    }
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
const missiles = new MissileTarget(document.getElementById("missile-target"), defs, orders);
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
  missiles.select(tile);
  redraw();
  await panel.show(tile);
  if (tile && narrow.matches && state.selected === tile) revealAboveSheet(tile);
}

// 地図の拡大の大きさと位置をブラウザに覚え、次に開いたときに戻す（UI-85）。位置は地図の真ん中に
// 見えている所（拡大前の位置）で覚えるので、窓の大きさが変わってもだいたい同じ所が出る
const VIEW_KEY = `hakoniwa:map-view:${canvas.dataset.island}`;
let saveTimer = null;

function saveView() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    const r = canvas.getBoundingClientRect();
    const center = { px: (r.width / 2 - view.offsetX) / view.scale, py: (r.height / 2 - view.offsetY) / view.scale };
    try {
      localStorage.setItem(VIEW_KEY, JSON.stringify({ scale: view.scale, ...center }));
    } catch {
      // 覚えられない（プライベートの窓など）ときは毎回島の全体を出す
    }
  }, 300);
}

/** 覚えた拡大と位置に戻す。覚えていなければ false。 */
function restoreView() {
  let saved = null;
  try {
    saved = JSON.parse(localStorage.getItem(VIEW_KEY) || "null");
  } catch {
    return false;
  }
  if (!saved || ![saved.scale, saved.px, saved.py].every(Number.isFinite) || saved.scale <= 0) return false;
  const r = canvas.getBoundingClientRect();
  view.scale = saved.scale;
  view.offsetX = r.width / 2 - saved.px * view.scale;
  view.offsetY = r.height / 2 - saved.py * view.scale;
  return true;
}

// 指（カーソル）の下のマスの座標を地図の隅に出す（UI-85）。縁の目盛りと一緒に「座標」のボタンで出し入れする
const coord = document.getElementById("map-coord");
const rulerToggle = document.getElementById("map-ruler-toggle");
function tileAtScreen(sx, sy) {
  return worldToTile((sx - view.offsetX) / view.scale, (sy - view.offsetY) / view.scale, map, heightOf, theme);
}
if (coord) {
  canvas.addEventListener("pointermove", (e) => {
    if (!state.ruler) return;
    const r = canvas.getBoundingClientRect();
    const tile = tileAtScreen(e.clientX - r.left, e.clientY - r.top);
    coord.hidden = !tile;
    if (tile) coord.textContent = `(${tile.x}, ${tile.y})`;
  });
  canvas.addEventListener("pointerleave", () => (coord.hidden = true));
}
if (rulerToggle) {
  rulerToggle.setAttribute("aria-pressed", String(state.ruler));
  rulerToggle.addEventListener("click", () => {
    state.ruler = !state.ruler;
    writeRulerShown(storage, state.ruler);
    rulerToggle.setAttribute("aria-pressed", String(state.ruler));
    if (!state.ruler && coord) coord.hidden = true;
    redraw();
  });
}

const controls = attachControls(canvas, view, {
  onChange() {
    saveView();
    redraw();
  },
  onTap(sx, sy) {
    if (state.snapshotMap) return; // 昔の地図を見ている間はマスを選ばない（今のマスの情報と食い違うため）
    const tile = tileAtScreen(sx, sy);
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

function showLegend(text) {
  if (legendBox) legendBox.textContent = text;
}

/** 重ね表示を切り替える。要るデータ（運賃の率など）は、初めて選んだときに地図の API から読む。 */
async function showOverlay(id) {
  const overlay = overlays.find((o) => o.id === id) || null;
  if (overlaySelect) overlaySelect.value = overlay ? overlay.id : "";
  showLegend(overlayLegend(overlay, state.overlayMode));
  if (overlay?.query && overlay.fields.some((f) => map[f] === undefined)) {
    showLegend("読み込み中…");
    try {
      const extra = await fetch(`${canvas.dataset.api}?${overlay.query}`).then((r) => r.json());
      for (const f of overlay.fields) map[f] = extra[f];
    } catch {
      showLegend("読み込めませんでした。");
      return;
    }
    if (overlaySelect && overlaySelect.value !== overlay.id) return; // 待つ間に別のものが選ばれた
    showLegend(overlayLegend(overlay, state.overlayMode));
  }
  state.overlay = overlay;
  redraw();
}

if (overlaySelect) {
  for (const o of overlays) overlaySelect.add(new Option(o.label, o.id));
  overlaySelect.addEventListener("change", () => showOverlay(overlaySelect.value));
}

// 前のターンから変わったマスの印（案3 の静止版）。材料は初めて出したときに地図の API から読む。
// 出すかどうかは覚え、次に開いたときも使う（座標の目盛りと同じ流儀）
const changesToggle = document.getElementById("map-changes");
const changesLegend = document.getElementById("changes-legend");
async function applyChanges() {
  if (changesLegend) changesLegend.hidden = !state.changes;
  if (state.changes && map.changed === undefined) {
    try {
      const extra = await fetch(`${canvas.dataset.api}?changes=1`).then((r) => r.json());
      map.changed = extra.changed || [];
    } catch {
      map.changed = [];
    }
  }
  state.changeMarks = state.changes ? changeMarks(map) : null;
  redraw();
}
if (changesToggle) {
  changesToggle.checked = state.changes;
  changesToggle.addEventListener("change", () => {
    state.changes = changesToggle.checked;
    writeChangesShown(storage, state.changes);
    applyChanges();
  });
  if (state.changes) applyChanges();
}

// 地図の変遷（案29）: 節目の写しの一覧を地図の API から読み、選んだ写しを「昔の地図」
// として同じ位置・倍率で描く。変化レンズを入れたときは、今の地図に写しとの差の印を
// 重ねる（写しの地図そのものは描かない）。レンズを出すかどうかは覚える
const historySelect = document.getElementById("map-history");
const lensToggle = document.getElementById("map-lens");
const lensLegend = document.getElementById("lens-legend");
const historyTurns = document.getElementById("history-turns");
const historyLabels = new Map();
async function applyHistory() {
  const turn = state.historyTurn;
  if (lensToggle) lensToggle.disabled = turn === null;
  if (historyTurns) {
    historyTurns.hidden = turn === null;
    if (turn !== null) historyTurns.textContent = `${historyLabels.get(turn) || ""} ⇔ ${state.historyNow}`;
  }
  if (lensLegend) lensLegend.hidden = !(turn !== null && state.lens);
  state.lensMarks = null;
  state.snapshotMap = null;
  if (turn === null) {
    redraw();
    return;
  }
  if (state.lens) {
    if (map.lensTurn !== turn) {
      try {
        const extra = await fetch(`${canvas.dataset.api}?lens=${turn}`).then((r) => r.json());
        map.lens = extra.lens || [];
      } catch {
        map.lens = [];
      }
      map.lensTurn = turn;
    }
    state.lensMarks = lensMarks(map);
  } else {
    try {
      state.snapshotMap = await fetch(`${canvas.dataset.api}?snapshot=${turn}`).then((r) => r.json());
    } catch {
      state.snapshotMap = null;
    }
    select(null);
  }
  redraw();
}
if (historySelect) {
  getJson(`${canvas.dataset.api}?history=1`)
    .then((extra) => {
      const history = extra.history || { snapshots: [], now: "" };
      state.historyNow = history.now || "";
      for (const snap of history.snapshots || []) {
        historyLabels.set(snap.turn, snap.label);
        historySelect.add(new Option(snap.label, String(snap.turn)));
      }
    })
    .catch(() => {});
  historySelect.addEventListener("change", () => {
    state.historyTurn = historySelect.value ? Number(historySelect.value) : null;
    applyHistory();
  });
}
if (lensToggle) {
  lensToggle.checked = state.lens;
  lensToggle.addEventListener("change", () => {
    state.lens = lensToggle.checked;
    writeLensShown(storage, state.lens);
    applyHistory();
  });
}

// 首都の旗（案23）の表示の切り替え（案44 の旗の分）。出すかどうかは覚え、
// 次に開いたときも使う（変わったマスと同じ流儀）
const flagToggle = document.getElementById("map-flag");
if (flagToggle) {
  flagToggle.checked = state.flagShown;
  flagToggle.addEventListener("change", () => {
    state.flagShown = flagToggle.checked;
    writeFlagShown(storage, state.flagShown);
    redraw();
  });
}

// 港の商船（案6）と母港の艦隊（W-100）の表示の切り替え。出すかどうかは別々に覚え、
// 次に開いたときも使う（首都の旗と同じ流儀）。凡例は、材料のある側だけを出す。
// 商船の隻数は「停泊中の商船（推計）N隻／地図にM隻」の形で、データの隻数と
// 地図に描けた隻数を分けて出す（掲示板 #6148）。描き直しのたびに割り当てを
// 計算し直すので、凡例の隻数も描き直しのたびに更新する（refreshShipsLegend）
const shipsMerchantToggle = document.getElementById("map-ships-merchant");
const shipsFleetToggle = document.getElementById("map-ships-fleet");
const shipsLegend = document.getElementById("ships-legend");
const shipsMerchantPart = document.getElementById("ships-merchant-part");
const shipsMerchantTotal = document.getElementById("ships-merchant-total");
const shipsMerchantPlaced = document.getElementById("ships-merchant-placed");
const shipsFleetPart = document.getElementById("ships-fleet-part");
const shipsOmitNote = document.getElementById("ships-omit-note");
let lastShipCounts = null;
function refreshShipsLegend(counts) {
  lastShipCounts = counts;
  if (!shipsLegend) return;
  const merchantVisible = state.merchantShown && counts.merchantKnown;
  const fleetVisible = state.fleetShown && counts.fleetTotal > 0;
  if (shipsMerchantPart) shipsMerchantPart.hidden = !merchantVisible;
  if (shipsFleetPart) shipsFleetPart.hidden = !fleetVisible;
  if (shipsMerchantTotal) shipsMerchantTotal.textContent = String(counts.merchantTotal);
  if (shipsMerchantPlaced) shipsMerchantPlaced.textContent = String(counts.merchantPlaced);
  if (shipsOmitNote) {
    shipsOmitNote.hidden = !(
      counts.merchantPlaced < counts.merchantTotal || counts.fleetPlaced < counts.fleetTotal
    );
  }
  shipsLegend.hidden = !(merchantVisible || fleetVisible);
}
function applyShipsLegend() {
  // 地図を読み終わるまでは隻数が分からないので、その間は隠したままにする
  if (lastShipCounts) refreshShipsLegend(lastShipCounts);
  else if (shipsLegend) shipsLegend.hidden = true;
}
if (shipsMerchantToggle) {
  shipsMerchantToggle.checked = state.merchantShown;
  shipsMerchantToggle.addEventListener("change", () => {
    state.merchantShown = shipsMerchantToggle.checked;
    writeMerchantShown(storage, state.merchantShown);
    applyShipsLegend();
    redraw();
  });
}
if (shipsFleetToggle) {
  shipsFleetToggle.checked = state.fleetShown;
  shipsFleetToggle.addEventListener("change", () => {
    state.fleetShown = shipsFleetToggle.checked;
    writeFleetShown(storage, state.fleetShown);
    applyShipsLegend();
    redraw();
  });
}
applyShipsLegend();

// 棒と塗り分けの切り替え（UI-79）。選んだ表し方は覚え、次に開いたときも使う
if (barsToggle) {
  barsToggle.checked = state.overlayMode === BARS;
  barsToggle.addEventListener("change", () => {
    state.overlayMode = barsToggle.checked ? BARS : FILL;
    writeOverlayMode(storage, state.overlayMode);
    if (state.overlay) showLegend(overlayLegend(state.overlay, state.overlayMode));
    redraw();
  });
}

if (minimap) {
  minimap.addEventListener("click", (e) => {
    const tile = minimapTile(minimap, map, e.clientX, e.clientY);
    if (!tile) return;
    centerOn(tile.x + 0.5, tile.y + 0.5);
    saveView();
    if (panel.marking) {
      panel.toggleMark(tile);
      return;
    }
    select(tile);
  });
}

popover.querySelector("[data-close]").addEventListener("click", () => { select(null); canvas.focus({ preventScroll: true }); });
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && panel.marking) {
    panel.endMarking();
    return;
  }
  // 入力欄で書いている途中は閉じない（書きかけを消さないように）
  if (e.key === "Escape" && state.selected && !e.target.closest?.("input, select, textarea")) {
    select(null);
    canvas.focus({ preventScroll: true });
  }
});
// 窓の中身を読み込んだり書き換えたりして大きさが変わったら、置き直す
new ResizeObserver(() => placePopover()).observe(popover);
narrow.addEventListener("change", () => placePopover());

window.addEventListener("resize", resize);
if (!restoreView()) fit();
resize();

// 地図の API にない反乱軍の位置は、持ち主だけが軍の要約から読む。
if (map.mine) {
  getJson(document.getElementById("tile-panel").dataset.militaryUrl).then(({ military }) => {
    map.civil_war = military.civil_war;
    redraw();
  }).catch(() => {
    const notice = document.getElementById("rebel-map-notice");
    notice.textContent = words().error;
    notice.hidden = false;
  });
}

// 絵は 1 枚読めるごとに描き直す（読み込むまでは色の箱で描く）。この島で使う絵を先に読み、
// 残りはあとに回す。redraw は requestAnimationFrame でまとめられるので描き直しは増えすぎない
loadSprites(theme, { into: state.sprites, priority: spritePriorityKeys(theme, map, defs), onLoad: () => redraw() });

// 首都のマスに立てる旗（その国の旗の画像）。読めたら描き直す
if (canvas.dataset.flag) loadFlag(canvas.dataset.flag, (flag) => { state.flag = flag; redraw(); });

// ターンログのリンクなどから ?x=..&y=.. で来たら、そのマスを選ぶ
const params = new URLSearchParams(location.search);
if (params.has("missile_base")) {
  const id = Number(params.get("missile_base"));
  if (Number.isInteger(id) && id > 0) missiles.start(id, true);
}
document.getElementById("tile-panel").addEventListener("click", (event) => {
  const button = event.target.closest("[data-missile-base]");
  if (button) missiles.start(Number(button.dataset.missileBase));
});
if (params.has("x") && params.has("y")) {
  const tile = { x: Number(params.get("x")), y: Number(params.get("y")) };
  if (Number.isInteger(tile.x) && Number.isInteger(tile.y) && tile.x >= 0 && tile.y >= 0 && tile.x < map.size && tile.y < map.size) {
    centerOn(tile.x + 0.5, tile.y + 0.5);
    select(tile);
  }
}
