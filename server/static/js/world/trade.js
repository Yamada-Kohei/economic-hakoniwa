// 世界マップの貿易（フェーズ3-1）: 貿易圏ごとの色で島の区画を縁取り（2つ以上の圏に入っている島は
// 内側に縁を重ねる）、港のある島に白い丸を付け、船で運ぶ途中の荷のある航路を線で結ぶ（太いほど船が
// 多く、矢じりは荷の向かう先。自国の航路は黄色）。区画の上に SVG を重ね、押すのは下の区画のまま。
// 荷の中身は API が自国の荷の分だけ返すので、航路の一覧にだけ出す。

import { turnDate } from "../calendar.js";
import { COLORS } from "../chart/line.js";
import { escapeHtml } from "../island/api.js";

const SVG = "http://www.w3.org/2000/svg";
const RING = 0.07; // 縁の太さ（区画の 1 辺に対する割合）
const MINE = "#ffca28"; // 自国の航路の色（site.css の自国の区画の縁の色）
const ROUTE = "#ffffff";

function svg(name, attrs) {
  const el = document.createElementNS(SVG, name);
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, String(value));
  return el;
}

// 島の区画の地の色（site.css の .world-grid .island）。グラフの系列の 1 色目と同じなので、縁には使わない
const CELL = "#2f6db5";
const BLOC_COLORS = COLORS.filter((c) => c.toLowerCase() !== CELL);

/** 貿易圏の色（圏の番号の順に、グラフの系列と同じ色を使う。区画の地と同じ色は除く）。 */
function blocColors(blocs) {
  const colors = new Map();
  [...blocs].sort((a, b) => a.id - b.id).forEach((b, i) => colors.set(b.id, BLOC_COLORS[i % BLOC_COLORS.length]));
  return colors;
}

/** 世界マップ（grid: #world-grid）に貿易圏・港・航路を重ね、section（#world-trade）に凡例と一覧を書く。 */
export function drawTrade(grid, data, section) {
  const size = Number(grid.style.getPropertyValue("--grid")) || 1;
  const colors = blocColors(data.blocs);
  const islands = new Map(data.islands.map((i) => [i.id, i]));
  const layer = svg("svg", {
    viewBox: `0 0 ${size} ${size}`,
    preserveAspectRatio: "none",
    "aria-hidden": "true",
  });
  // 区画の並び（padding 2px）の上にぴったり重ねる。押すのは下の区画
  layer.style.cssText = "position:absolute; inset:2px; width:calc(100% - 4px); height:calc(100% - 4px); pointer-events:none; z-index:2; overflow:visible";
  grid.style.position = "relative";

  // 縁は区画の下端の名前（.cell-name。高さ 14px ほど）にかからないよう、下を開けてその上で止める
  const cellPx = grid.clientWidth / size || 32;
  const nameBand = Math.min(0.5, Math.max(0.15, 15 / cellPx));
  for (const island of data.islands) {
    island.blocs.forEach((id, k) => {
      const inset = RING * (k + 0.5) + 0.02;
      const x0 = island.wx + inset;
      const x1 = island.wx + 1 - inset;
      const y0 = island.wy + inset;
      const y1 = island.wy + 1 - nameBand;
      layer.append(svg("path", {
        d: `M ${x0} ${y1} L ${x0} ${y0} L ${x1} ${y0} L ${x1} ${y1}`,
        fill: "none", stroke: colors.get(id) || "#999", "stroke-width": RING,
      }));
    });
    if (island.port) {
      layer.append(svg("circle", {
        cx: island.wx + 0.82, cy: island.wy + 0.18, r: 0.08,
        fill: "#fff", stroke: "#1d3f66", "stroke-width": 0.03,
      }));
    }
  }
  for (const route of data.routes) drawRoute(layer, route, islands);
  grid.append(layer);
  describeCells(grid, data);
  if (section) writeLegend(section, data, colors);
}

/** 航路を1本描く（行きと帰りが重ならないように、向きの右側へ少しずらす）。 */
function drawRoute(layer, route, islands) {
  const a = islands.get(route.from);
  const b = islands.get(route.to);
  if (!a || !b) return;
  const [x1, y1, x2, y2] = [a.wx + 0.5, a.wy + 0.5, b.wx + 0.5, b.wy + 0.5];
  const length = Math.hypot(x2 - x1, y2 - y1) || 1;
  const [nx, ny] = [-(y2 - y1) / length * 0.08, (x2 - x1) / length * 0.08];
  const color = route.mine ? MINE : ROUTE;
  const width = Math.min(0.22, 0.04 + 0.04 * Math.max(route.ships, 0.25));
  const line = svg("line", {
    x1: x1 + nx, y1: y1 + ny, x2: x2 + nx, y2: y2 + ny,
    stroke: color, "stroke-width": width, "stroke-linecap": "round", opacity: 0.85,
  });
  layer.append(line);
  // 荷の向かう先を指す矢じり（線の 6 割の所）
  const [ux, uy] = [(x2 - x1) / length, (y2 - y1) / length];
  const [mx, my] = [x1 + nx + (x2 - x1) * 0.6, y1 + ny + (y2 - y1) * 0.6];
  const head = 0.16;
  const points = [
    [mx + ux * head, my + uy * head],
    [mx - uy * head * 0.6, my + ux * head * 0.6],
    [mx + uy * head * 0.6, my - ux * head * 0.6],
  ];
  layer.append(svg("polygon", {
    points: points.map((p) => p.join(",")).join(" "),
    fill: color, stroke: "#1d3f66", "stroke-width": 0.015,
  }));
}

/** 区画の吹き出し（title）に、加盟している圏と港を書き足す。 */
function describeCells(grid, data) {
  const names = new Map(data.blocs.map((b) => [b.id, b.name]));
  for (const island of data.islands) {
    const canvas = grid.querySelector(`canvas[data-island="${island.id}"]`);
    const cell = canvas?.closest("a");
    if (!cell) continue;
    const parts = [];
    if (island.blocs.length) parts.push(`貿易圏: ${island.blocs.map((id) => names.get(id)).join("・")}`);
    if (island.port) parts.push("港あり");
    if (parts.length) cell.title = `${cell.title}\n${parts.join("\n")}`;
  }
}

function writeLegend(section, data, colors) {
  const slot = (name) => section.querySelector(`[data-slot="${name}"]`);
  const blocs = data.blocs.map((b) => {
    const members = b.members.map((m) => escapeHtml(m.name)).join("・");
    return `<li><span class="swatch" style="background:${colors.get(b.id)}"></span>
      <strong>${escapeHtml(b.name)}</strong>
      <small class="muted">決済通貨 ${escapeHtml(b.currency_name)}・代表 ${escapeHtml(b.rep_name)}・${b.open ? "誰でも入れる" : "承認制"}</small><br>
      <small>${members}</small></li>`;
  });
  slot("blocs").innerHTML = blocs.length
    ? `<ul class="plain-list">${blocs.join("")}</ul>`
    : `<p class="muted">貿易圏はまだありません。</p>`;
  const routes = data.routes.map((r) => {
    const cargo = (r.cargo || [])
      .map((c) => `${escapeHtml(c.name)} ${c.amount} ${escapeHtml(c.unit)}: ${escapeHtml(turnDate(c.arrive))}に着く`)
      .join("<br>");
    const ships = r.ships > 0 ? `船 ${r.ships} 隻分（${r.load} トン）` : `荷 ${r.shipments} 件`;
    return `<li>${escapeHtml(r.from_name)} → ${escapeHtml(r.to_name)}: ${ships}${cargo ? `<br><small>${cargo}</small>` : ""}</li>`;
  });
  slot("routes").innerHTML = routes.length
    ? `<ul class="plain-list">${routes.join("")}</ul>`
    : `<p class="muted">運ぶ途中の荷はありません。</p>`;
  section.hidden = !(data.blocs.length || data.routes.length || data.islands.some((i) => i.port));
}
