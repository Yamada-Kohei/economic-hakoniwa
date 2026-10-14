// 建国の画面: 区画を選び、島の形を描き、地形・資源・首都・体制・名前を決めて送る。
// 地形は「描いた形」と「種」からサーバーが配る。画面で見えている地形がそのまま建国後の地形になる。

import { escapeHtml, getJson, sendJson } from "../island/api.js";
import { drawAllIslands } from "../world/pixels.js";

const root = document.getElementById("founding");
const canvas = document.getElementById("founding-map");
const ctx = canvas.getContext("2d");

const [defs, world, cells, shapes] = await Promise.all([
  getJson(root.dataset.defs),
  getJson(root.dataset.world),
  getJson(root.dataset.cells),
  getJson(root.dataset.shapes),
]);
const config = defs.founding;
const n = config.size;

const state = {
  cell: null,
  land: new Set(), // "x,y"
  edits: new Map(), // "x,y" → 地形
  seed: Number(root.dataset.seed),
  terrain: null, // サーバーが配った地形（y * n + x の順）
  capital: null, // [x, y]（選ばなければ候補を使う）
  suggested: null,
  capitalTerrain: [],
  resources: new Set(),
};

// --- 1. 区画 ------------------------------------------------------------------

function drawCells() {
  const grid = document.getElementById("cell-grid");
  const free = new Set(cells.free.map(([x, y]) => `${x},${y}`));
  const islands = new Map(world.islands.map((i) => [`${i.wx},${i.wy}`, i]));
  let size = world.grid;
  for (const key of free) size = Math.max(size, ...key.split(",").map((v) => Number(v) + 1));
  grid.style.setProperty("--grid", size);
  const rows = cells.rows || [];
  const parts = [];
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const key = `${x},${y}`;
      const island = islands.get(key);
      if (island) {
        parts.push(
          `<span class="cell island" title="${escapeHtml(island.name)}">` +
            `<canvas class="island-pixels" data-island="${island.id}" width="32" height="32" aria-hidden="true"></canvas>` +
            `<span class="cell-name">${escapeHtml(island.name.slice(0, 8))}</span></span>`,
        );
      } else if (free.has(key)) {
        const chosen = state.cell === key ? " chosen" : "";
        const zone = rows[Math.min(y, rows.length - 1)] || "";
        const name = zoneOf(zone)?.name || "";
        parts.push(`<button type="button" class="cell free zone-${escapeHtml(zone)}${chosen}" data-cell="${key}" aria-label="区画 (${x}, ${y}) ${escapeHtml(name)}"></button>`);
      } else {
        parts.push(`<span class="cell sea"></span>`);
      }
    }
  }
  grid.innerHTML = parts.join("");
  drawAllIslands(grid, shapes, defs);
  let text = "";
  if (state.cell) {
    const y = Number(state.cell.split(",")[1]);
    const zone = zoneOf(rows[Math.min(y, rows.length - 1)]);
    text = `選んだ区画: (${state.cell})` + (zone ? ` ・ 気候: ${zone.name}（${zone.text}）` : "");
  }
  document.getElementById("cell-text").textContent = text;
  document.getElementById("cell-zones").innerHTML = (cells.zones || [])
    .map((z) => `<li><span class="zone-swatch zone-${escapeHtml(z.zone)}"></span>${escapeHtml(z.name)}: ${escapeHtml(z.text)}</li>`)
    .join("");
}

/** 気候帯（フェーズ3-2）の名前と説明。 */
function zoneOf(zone) {
  return (cells.zones || []).find((z) => z.zone === zone);
}

document.getElementById("cell-grid").addEventListener("click", (e) => {
  const button = e.target.closest("[data-cell]");
  if (!button) return;
  state.cell = button.dataset.cell;
  drawCells();
});

// --- 2. 形と地形 --------------------------------------------------------------

const cellPx = canvas.width / n;

function landList() {
  return [...state.land].map((k) => k.split(",").map(Number));
}

function regimeChoice() {
  const regime = {};
  for (const select of document.querySelectorAll("#regimes select")) regime[select.name] = select.value;
  return regime;
}

function drawShape() {
  const theme = defs.theme;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  for (let y = 0; y < n; y++) {
    for (let x = 0; x < n; x++) {
      const key = `${x},${y}`;
      // 送り直す前の古い地形が陸と海で食い違うときは、描いた形の方に合わせる
      let t = state.edits.get(key) ?? state.terrain?.[y * n + x];
      const land = state.land.has(key);
      if (!t || Boolean(defs.terrain[t]?.land) !== land) t = land ? config.land_terrains[0] : config.sea_terrain;
      ctx.fillStyle = (theme.terrain[t] || theme.fallback).top;
      ctx.fillRect(x * cellPx, y * cellPx, cellPx, cellPx);
      if (state.edits.has(key)) {
        ctx.strokeStyle = "rgba(255,255,255,0.8)";
        ctx.lineWidth = 1;
        ctx.strokeRect(x * cellPx + 2, y * cellPx + 2, cellPx - 4, cellPx - 4);
      }
    }
  }
  // 縁の1マスは海のまま（描けない）
  ctx.strokeStyle = "rgba(255,255,255,0.35)";
  ctx.lineWidth = 1;
  ctx.strokeRect(cellPx, cellPx, (n - 2) * cellPx, (n - 2) * cellPx);
  const capital = state.capital || state.suggested;
  if (capital) {
    const [x, y] = capital;
    ctx.fillStyle = state.capital ? "#e53935" : "rgba(229,57,53,0.55)";
    ctx.beginPath();
    ctx.arc((x + 0.5) * cellPx, (y + 0.5) * cellPx, cellPx * 0.35, 0, Math.PI * 2);
    ctx.fill();
  }
}

let terrainTimer = null;
let terrainRequest = 0;

/** 形や地形の変更を少し待ってからまとめてサーバーに送り、配られた地形と問題を受け取る。 */
function refreshTerrain() {
  clearTimeout(terrainTimer);
  terrainTimer = setTimeout(async () => {
    const request = ++terrainRequest;
    const edits = [...state.edits].map(([k, t]) => [...k.split(",").map(Number), t]);
    const res = await sendJson(root.dataset.terrain, "POST", {
      land: landList(),
      seed: state.seed,
      edits,
      regime: regimeChoice(),
    });
    if (request !== terrainRequest || !res.ok) return;
    state.terrain = res.data.terrain;
    state.suggested = res.data.suggested_capital;
    state.capitalTerrain = res.data.capital_terrain;
    if (state.capital && !state.land.has(state.capital.join(","))) state.capital = null;
    showProblems("shape-problems", res.data.problems);
    drawShape();
  }, 250);
  updateShapeStatus();
}

function updateShapeStatus() {
  const count = state.land.size;
  const capital = state.capital ? `首都: (${state.capital.join(", ")})` : "首都: 自動選択（薄い赤の印）";
  document.getElementById("shape-status").textContent =
    `陸地 ${count} マス（${config.land_min}〜${config.land_max}）・地形の変更 ${state.edits.size} / ${config.terrain_edits} ・ ${capital}`;
}

function showProblems(id, problems) {
  document.getElementById(id).innerHTML = problems.map((p) => `<li>${escapeHtml(p)}</li>`).join("");
}

function tool() {
  return document.querySelector("input[name=tool]:checked").value;
}

/** ポインターのあるマス（縁や島の外でも返す。描けるかは drawable で見る）。 */
function tileAt(e) {
  const r = canvas.getBoundingClientRect();
  const x = Math.floor(((e.clientX - r.left) / r.width) * n);
  const y = Math.floor(((e.clientY - r.top) / r.height) * n);
  return [x, y];
}

/** 縁の1マスは海のまま（描けない）。 */
function drawable([x, y]) {
  return x >= 1 && y >= 1 && x < n - 1 && y < n - 1;
}

/** from から to までの線が通るマス（from は含まない）。上下左右につながるように1マスずつ進む。 */
function tilesBetween([x0, y0], [x1, y1]) {
  const dx = Math.abs(x1 - x0);
  const dy = Math.abs(y1 - y0);
  const sx = x0 < x1 ? 1 : -1;
  const sy = y0 < y1 ? 1 : -1;
  const tiles = [];
  let [x, y] = [x0, y0];
  let err = dx - dy;
  while (x !== x1 || y !== y1) {
    if (2 * err > -dy) {
      err -= dy;
      x += sx;
    } else {
      err += dx;
      y += sy;
    }
    tiles.push([x, y]);
  }
  return tiles;
}

function paintTile(pos) {
  const key = pos.join(",");
  const t = tool();
  if (t === "land") {
    state.land.add(key);
  } else if (t === "sea") {
    state.land.delete(key);
    state.edits.delete(key);
  } else if (t === "terrain") {
    if (!state.land.has(key)) return;
    const value = document.getElementById("edit-terrain").value;
    if (!state.edits.has(key) && state.edits.size >= config.terrain_edits) return;
    state.edits.set(key, value);
  } else if (t === "capital") {
    if (state.land.has(key)) state.capital = pos;
  }
}

function paint(tiles) {
  tiles = tiles.filter(drawable);
  if (!tiles.length) return;
  for (const pos of tiles) paintTile(pos);
  drawShape();
  refreshTerrain();
}

let drawing = false;
let last = null; // 前に描いたときのポインターのマス
canvas.addEventListener("pointerdown", (e) => {
  const pos = tileAt(e);
  if (!drawable(pos)) return;
  drawing = tool() !== "capital";
  last = pos;
  canvas.setPointerCapture(e.pointerId);
  paint([pos]);
});
canvas.addEventListener("pointermove", (e) => {
  if (!drawing) return;
  const pos = tileAt(e);
  if (pos[0] === last[0] && pos[1] === last[1]) return;
  // 速く動かすと1回の動きで何マスも進むので、間のマスも描く
  paint(tilesBetween(last, pos));
  last = pos;
});
for (const type of ["pointerup", "pointercancel"]) {
  canvas.addEventListener(type, () => {
    drawing = false;
  });
}

document.getElementById("random-shape").addEventListener("click", async () => {
  state.seed = Math.floor(Math.random() * 2 ** 31);
  const res = await sendJson(root.dataset.shape, "POST", { seed: state.seed });
  if (!res.ok) return;
  state.land = new Set(res.data.land.map(([x, y]) => `${x},${y}`));
  state.edits.clear();
  state.capital = null;
  drawShape();
  refreshTerrain();
});

document.getElementById("reroll-terrain").addEventListener("click", () => {
  state.seed = Math.floor(Math.random() * 2 ** 31);
  refreshTerrain();
});

document.getElementById("clear-edits").addEventListener("click", () => {
  state.edits.clear();
  drawShape();
  refreshTerrain();
});

const editTerrain = document.getElementById("edit-terrain");
for (const t of config.land_terrains) editTerrain.add(new Option(defs.terrain[t].name, t));
document.getElementById("shape-hint").textContent =
  `陸地は ${config.land_min}〜${config.land_max} マスです（外周の1マスは海）。離れ小島も描けます。` +
  `平地・森・山は自動で配られ、${config.terrain_edits} マスまで変更できます。押したまま動かすと続けて描けます。`;

// --- 3. 地下資源 --------------------------------------------------------------

function drawResources() {
  document.getElementById("resource-hint").textContent =
    `${config.max_resources} 種類まで選べます。選んだ資源だけを山の鉱山・採石場で採掘できます` +
    `（鋼鉄には鉄鉱石と石炭の両方が必要です。鉱山は1国 ${defs.facilities.mine?.max_per_island ?? "-"} か所まで）。`;
  document.getElementById("resources").innerHTML = Object.entries(defs.mining)
    .map(([id, name]) => `<label><input type="checkbox" value="${id}"> ${escapeHtml(name)}</label>`)
    .join("");
}

document.getElementById("resources").addEventListener("change", (e) => {
  const box = e.target;
  if (box.checked && state.resources.size >= config.max_resources) {
    box.checked = false;
    return;
  }
  if (box.checked) state.resources.add(box.value);
  else state.resources.delete(box.value);
});

// --- 4. 体制 -----------------------------------------------------------------

function drawRegimes() {
  document.getElementById("regimes").innerHTML = Object.entries(defs.axes)
    .map(([axis, a]) => {
      const options = Object.entries(a.choices).map(([id, name]) => `<option value="${id}">${escapeHtml(name)}</option>`).join("");
      return `<label>${escapeHtml(a.name)} <select name="${axis}">${options}</select></label>`;
    })
    .join("");
  showCapitalBuilding();
}

function showCapitalBuilding() {
  const names = Object.values(regimeChoice())
    .map((id) => defs.regimes[id]?.capital_building_name)
    .filter(Boolean);
  document.getElementById("capital-building").textContent = names.length ? `首都に建つ建物: ${names.join("・")}` : "";
}

document.getElementById("regimes").addEventListener("change", () => {
  showCapitalBuilding();
  refreshTerrain(); // 首都の施設が変わると、首都の候補も変わる
});

// --- 5. 名前 -----------------------------------------------------------------

function drawNames() {
  const styles = Object.entries(config.place_styles)
    .map(([id, label]) => `<option value="${id}"${id === config.place_style ? " selected" : ""}>${escapeHtml(label)}</option>`)
    .join("");
  document.getElementById("names").innerHTML =
    Object.entries(config.name_max)
      .map(([key, max]) => `<label>${escapeHtml(config.name_labels[key])} <input name="${key}" maxlength="${max}" required></label>`)
      .join("") + `<label>地名の様式 <select name="place_style">${styles}</select></label>`;
}

// --- 建国 --------------------------------------------------------------------

document.getElementById("found-button").addEventListener("click", async () => {
  const problems = [];
  if (!state.cell) problems.push("世界マップの区画を選んでください。");
  const capital = state.capital || state.suggested;
  if (!capital) problems.push("首都の場所を決めてください（陸地を描くと自動で候補が出ます）。");
  if (problems.length) {
    showProblems("found-problems", problems);
    return;
  }
  const names = {};
  for (const input of document.querySelectorAll("#names input")) names[input.name] = input.value.trim();
  const placeStyle = document.querySelector("#names select[name=place_style]")?.value || "";
  const button = document.getElementById("found-button");
  button.disabled = true;
  const res = await sendJson(root.dataset.found, "POST", {
    land: landList(),
    capital,
    regime: regimeChoice(),
    names,
    resources: [...state.resources],
    edits: [...state.edits].map(([k, t]) => [...k.split(",").map(Number), t]),
    seed: state.seed,
    place_style: placeStyle,
    cell: state.cell.split(",").map(Number),
  });
  button.disabled = false;
  if (res.ok) {
    location.href = res.data.url;
    return;
  }
  showProblems("found-problems", res.data.problems || ["建国できませんでした。"]);
  if (res.status === 400) {
    // 区画が先に取られたときのために、空いている区画を読み直す
    Object.assign(cells, await getJson(root.dataset.cells));
    if (state.cell && !cells.free.some(([x, y]) => `${x},${y}` === state.cell)) state.cell = null;
    drawCells();
  }
});

drawCells();
drawResources();
drawRegimes();
drawNames();
drawShape();
document.getElementById("random-shape").click();
