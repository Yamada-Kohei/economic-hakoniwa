// 世界マップの貿易（フェーズ3-1）: 貿易圏ごとの色で島の区画を縁取り（2つ以上の圏に入っている島は
// 内側に縁を重ねる）、港のある島に白い丸を付ける。区画の上に SVG を重ね、押すのは下の区画のまま。
// 航路の線は島の組ごとの貿易の読み替え（グラフィック強化 案4・第3波）: 額が分かる向きは実線で
// 太さ＝貿易額（直近 12 ターン、基準の通貨に換算）、色＝その向きの主な品目。額が分からない向き
// （発着だけ確認できた）は等幅の破線、禁輸・戦争の組は赤い点線。線種の決めは pairLineKind。
// 荷の中身は API が自国の荷の分だけ返すので、航路の一覧にだけ出す。
// 他国どうしの航路は、国が増えると線で地図が白く埋まって島が見えなくなる（不具合報告 #17）ので、
// 既定では出さず、地図の上のボタンで出す（出すかどうかはブラウザに覚える）。自国の航路はいつも出す。

import { turnDate } from "../calendar.js";
import { COLORS } from "../chart/line.js";
import { escapeHtml } from "../island/api.js";

const SVG = "http://www.w3.org/2000/svg";
const RING = 0.07; // 縁の太さ（区画の 1 辺に対する割合）
const ROUTE = "#ffffff";
const ROUTES_KEY = "world.routes"; // 他国の航路を出すか（localStorage）

// 航路の線種（凡例のスウォッチと共用の定義。区画 1 辺を 1 とする長さ）
// 破線は、375px 幅・最短の航路（長さ 1）でも隙間が 1 つ以上見える間隔にする（掲示板 #5825・#5827）
export const ROUTE_DASH = [0.34, 0.2];
export const BLOCKED_DASH = [0.03, 0.13]; // 赤い点線（線端を丸めて点にする）
export const BLOCKED_COLOR = "#ff5252"; // 禁輸・戦争（fleets.js の他国の色と同じ）
const DASH_WIDTH = 0.07; // 破線・点線の太さ（額で変わらない）

/** 主な品目の色: この地図に現れる品目どうしで重ならないよう、財の並び順に割り当てる。 */
export function goodColors(pairs) {
  const goods = new Map();
  for (const pair of pairs || []) {
    for (const dir of [pair.ab, pair.ba]) {
      if (dir?.good && !goods.has(dir.good.good)) goods.set(dir.good.good, dir.good);
    }
  }
  const sorted = [...goods.values()].sort((x, y) => (x.index ?? 0) - (y.index ?? 0));
  const n = BLOC_COLORS.length;
  return new Map(sorted.map((good, i) => [good.good, BLOC_COLORS[i % n]]));
}

/** 実線の太さ（いちばん大きい向きの額で割って、既存の航路の太さの範囲に収める）。 */
export function routeWidth(amount, maxAmount) {
  if (!(maxAmount > 0) || amount == null) return 0.04;
  return Math.min(0.22, 0.04 + 0.18 * (amount / maxAmount));
}

/** 航路の線種: 禁輸・戦争の組は blocked、額が分かる向きは solid、残りは dashed。 */
export function pairLineKind(dir, blocked) {
  if (blocked) return "blocked";
  if (!dir) return null;
  return dir.amount != null ? "solid" : "dashed";
}

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
  const others = svg("g", { "data-routes": "others" });
  drawPairRoutes(data, islands, layer, others);
  layer.append(others);
  grid.append(layer);
  mountRoutesToggle(others, (data.pair_routes || []).filter((p) => !p.mine).length);
  describeCells(grid, data);
  if (section) writeLegend(section, data, colors);
}

function storage() {
  try {
    return window.localStorage;
  } catch {
    return null; // 保存できないブラウザでは、毎回既定（出さない）
  }
}

/** 他国の航路を出す・消すボタン（#world-routes-toggle）。他国の航路がなければボタンを隠す。 */
function mountRoutesToggle(others, count) {
  const button = document.getElementById("world-routes-toggle");
  const shown = storage()?.getItem(ROUTES_KEY) === "1";
  const apply = (on) => {
    others.style.display = on ? "" : "none";
    if (!button) return;
    button.setAttribute("aria-pressed", String(on));
    button.textContent = on ? "他国の航路を消す" : "他国の航路を出す";
  };
  apply(shown);
  if (!button) return;
  button.hidden = count === 0;
  button.addEventListener("click", () => {
    const on = button.getAttribute("aria-pressed") !== "true";
    apply(on);
    storage()?.setItem(ROUTES_KEY, on ? "1" : "0");
  });
}

/** 島の組ごとの航路を描く（自国の組は layer、他国の組は others に分ける）。 */
function drawPairRoutes(data, islands, layer, others) {
  const pairs = data.pair_routes || [];
  const maxAmount = Math.max(
    0,
    ...pairs.flatMap((p) => [p.ab?.amount || 0, p.ba?.amount || 0]),
  );
  const colors = goodColors(pairs);
  for (const pair of pairs) {
    const target = pair.mine ? layer : others;
    if (pairLineKind(null, pair.blocked) === "blocked") {
      drawBlockedLine(target, pair, islands);
      continue;
    }
    if (pair.ab) drawPairLine(target, pair.ab, islands.get(pair.a), islands.get(pair.b), pair.mine, maxAmount, colors);
    if (pair.ba) drawPairLine(target, pair.ba, islands.get(pair.b), islands.get(pair.a), pair.mine, maxAmount, colors);
  }
}

/** 禁輸・戦争の組: 区画の真ん中どうしを赤い点線で結ぶ（ずらしも矢じりも付けない）。 */
function drawBlockedLine(layer, pair, islands) {
  const a = islands.get(pair.a);
  const b = islands.get(pair.b);
  if (!a || !b) return;
  layer.append(svg("line", {
    x1: a.wx + 0.5, y1: a.wy + 0.5, x2: b.wx + 0.5, y2: b.wy + 0.5,
    stroke: BLOCKED_COLOR, "stroke-width": DASH_WIDTH, "stroke-linecap": "round",
    "stroke-dasharray": BLOCKED_DASH.join(" "), opacity: 0.9,
  }));
}

/** 航路を向きごとに1本描く（行きと帰りが重ならないように、向きの右側へ少しずらす）。 */
function drawPairLine(layer, dir, a, b, mine, maxAmount, colors) {
  if (!a || !b) return;
  const [x1, y1, x2, y2] = [a.wx + 0.5, a.wy + 0.5, b.wx + 0.5, b.wy + 0.5];
  const length = Math.hypot(x2 - x1, y2 - y1) || 1;
  const [nx, ny] = [-(y2 - y1) / length * 0.08, (x2 - x1) / length * 0.08];
  const kind = pairLineKind(dir, null);
  const color = kind === "solid" ? colors.get(dir.good?.good) || ROUTE : ROUTE;
  const attrs = {
    x1: x1 + nx, y1: y1 + ny, x2: x2 + nx, y2: y2 + ny,
    stroke: color,
    "stroke-width": kind === "solid" ? routeWidth(dir.amount, maxAmount) : DASH_WIDTH,
    "stroke-linecap": "round",
    opacity: mine ? 0.9 : 0.6,
  };
  if (kind === "dashed") attrs["stroke-dasharray"] = ROUTE_DASH.join(" ");
  layer.append(svg("line", attrs));
  // 貿易・荷の向かう先を指す矢じり（線の 6 割の所）
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
  // 主な品目の色の対応（地図に出ている財だけ。地図と同じ割り当て・並びは財の並び順）
  const goodsSlot = slot("goods");
  if (goodsSlot) {
    const colors = goodColors(data.pair_routes);
    const seen = new Map();
    for (const p of data.pair_routes || []) {
      for (const d of [p.ab, p.ba]) {
        if (d?.good && !seen.has(d.good.good)) seen.set(d.good.good, d.good);
      }
    }
    goodsSlot.innerHTML = [...seen.values()]
      .sort((x, y) => x.index - y.index)
      .map((g) => `<span class="route-good"><span class="swatch" style="background:${colors.get(g.good)}"></span>${escapeHtml(g.name)}</span>`)
      .join("");
  }
  // 島の組ごとの額（額の無い向きは額を出さない。掲示板 #5827）
  const pairsSlot = slot("pairs");
  if (pairsSlot) {
    const pairs = (data.pair_routes || []).map((p) => {
      const dirs = [];
      if (p.ab) dirs.push(pairDirText(p.a_name, p.b_name, p.ab));
      if (p.ba) dirs.push(pairDirText(p.b_name, p.a_name, p.ba));
      return `<li${p.blocked ? ' class="blocked"' : ""}>${dirs.join("<br>")}</li>`;
    });
    pairsSlot.innerHTML = pairs.length ? `<ul class="plain-list">${pairs.join("")}</ul>` : "";
  }
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
  section.hidden = !(data.blocs.length || data.routes.length || (data.pair_routes || []).length || data.islands.some((i) => i.port));
}

/** 組の向きの 1 行（額が分かる向きだけ額と主な品目を出す）。 */
function pairDirText(fromName, toName, dir) {
  const head = `${escapeHtml(fromName)} → ${escapeHtml(toName)}`;
  if (dir.amount_text == null) return head;
  const good = dir.good ? ` (${escapeHtml(dir.good.name)})` : "";
  return `${head}: ${escapeHtml(dir.amount_text)}${good}`;
}
