// 描画: 奥のマスから手前のマスへ（x + y の小さい順に）描き、高いものが奥を自然に隠すようにする。

import { lift, tileToWorld } from "./projection.js";
import { drawSprite } from "./sprites.js";

/**
 * 島を描く。
 * options: selected（選んだマス {x, y}）、marked（印を付けるマスの Set "x,y"）、
 *          overlay（重ねて表示する情報。overlays/ のモジュール）、
 *          sprites（sprites.js の loadSprites が返す絵。ないものは色の箱で描く）
 */
export function drawIsland(ctx, map, defs, view, options = {}) {
  const theme = defs.theme;
  const { width, height } = ctx.canvas;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, width, height);
  ctx.setTransform(view.scale * view.dpr, 0, 0, view.scale * view.dpr, view.offsetX * view.dpr, view.offsetY * view.dpr);

  const sprites = options.sprites || new Map();
  const n = map.size;
  const facilityAt = new Map();
  for (const f of map.facilities || []) facilityAt.set(f.y * n + f.x, f);

  for (let s = 0; s <= 2 * (n - 1); s++) {
    for (let x = Math.max(0, s - n + 1); x <= Math.min(s, n - 1); x++) {
      const y = s - x;
      const i = y * n + x;
      const terrain = map.terrain[i];
      const colors = theme.terrain[terrain] || theme.fallback;
      const h = lift(defs.terrain[terrain]?.height ?? 0, theme);
      const { px, py } = tileToWorld(x, y, theme);
      const top = py - h;
      const f = facilityAt.get(i);
      // 施設のあるマスは地形の絵（木や山）を描かず、上面の色だけにする。施設の絵と重ならないように
      drawBlock(ctx, px, py, h, colors, theme, f ? null : sprites.get(`terrain/${terrain}`));
      if (options.overlay) {
        const color = options.overlay.color(map, i, defs);
        if (color) {
          ctx.fillStyle = color;
          diamondPath(ctx, px, top, theme.tile_width / 2, theme.tile_height / 2);
          ctx.fill();
        }
      }
      if (map.zone?.[i] === "no_development") drawHatch(ctx, px, top, theme);
      if (map.power_line?.[i] && !facilityAt.has(i)) drawPowerLine(ctx, map, x, y, px, top, theme, facilityAt);
      if (f) drawFacility(ctx, f, map, defs, px, top, theme, sprites);
      if (options.marked?.has(`${x},${y}`)) outlineTop(ctx, px, top, theme, "#ff5722");
      if (options.selected && options.selected.x === x && options.selected.y === y) {
        outlineTop(ctx, px, top, theme, "#ffeb3b");
      }
    }
  }
}

function drawBlock(ctx, px, py, h, colors, theme, sprite) {
  const hw = theme.tile_width / 2;
  const hh = theme.tile_height / 2;
  const top = py - h;
  if (h > 0) sides(ctx, px, top, py, hw, hh, colors.left, colors.right);
  if (sprite) {
    // 地形の絵は上面（とそこから上に出る木や岩）。側面は色で描く
    drawSprite(ctx, sprite, px, top + 2 * hh);
    return;
  }
  ctx.fillStyle = colors.top;
  diamondPath(ctx, px, top, hw, hh);
  ctx.fill();
}

/** 施設の絵の名前。住宅は住宅地の段階ごと、首都の建物は capital/ の絵があればそれ。 */
function facilitySprite(f, defs, isCapital, sprites) {
  if (!f.built) return sprites.get("construction");
  if (f.kind === defs.housing) {
    const level = defs.settlements[f.level] ? f.level : Object.keys(defs.settlements)[0];
    return sprites.get(`housing/${level}`);
  }
  const name = defs.facilities[f.kind]?.sprite || f.kind;
  return (isCapital && sprites.get(`capital/${name}`)) || sprites.get(`facility/${name}`);
}

/** 施設: 絵があれば絵、なければ色付きの箱。住宅の高さは住宅地の段階、工事中は枠だけ。 */
function drawFacility(ctx, f, map, defs, px, ground, theme, sprites) {
  const isCapital = map.capital && map.capital[0] === f.x && map.capital[1] === f.y;
  const sprite = facilitySprite(f, defs, isCapital, sprites);
  if (sprite) {
    drawSprite(ctx, sprite, px, ground + theme.tile_height);
    if (f.built) drawStopped(ctx, f, defs, px, ground, theme.tile_height / 2);
    return;
  }
  const style = theme.facilities?.[f.kind] || theme.facility_fallback;
  let height = style.height;
  if (f.kind === defs.housing) {
    const level = defs.settlements[f.level] || Object.values(defs.settlements)[0];
    height = level?.height ?? 1;
  }
  const unit = theme.facility_unit;
  // 箱はマスより一回り小さくする
  const k = f.kind === defs.housing ? 0.7 : 0.8;
  const hw = (theme.tile_width / 2) * k;
  const hh = (theme.tile_height / 2) * k;
  const base = ground + (theme.tile_height / 2) * (1 - k);
  const top = base - height * unit;
  if (!f.built) {
    ctx.strokeStyle = theme.construction;
    ctx.lineWidth = 1.5;
    ctx.setLineDash([3, 3]);
    diamondPath(ctx, px, top, hw, hh);
    ctx.stroke();
    ctx.beginPath();
    for (const [dx, dy] of [[-hw, hh], [hw, hh], [0, 2 * hh]]) {
      ctx.moveTo(px + dx, top + dy);
      ctx.lineTo(px + dx, base + dy);
    }
    ctx.stroke();
    ctx.setLineDash([]);
    return;
  }
  const color = style.color;
  sides(ctx, px, top, base, hw, hh, shade(color, 0.78), shade(color, 0.62));
  ctx.fillStyle = isCapital ? theme.capital_roof : color;
  diamondPath(ctx, px, top, hw, hh);
  ctx.fill();
  drawStopped(ctx, f, defs, px, top, hh);
}

/** 止まっている施設には赤い印（絵の上にも描く）。top は印を置くひし形の上の頂点。 */
function drawStopped(ctx, f, defs, px, top, hh) {
  if (f.kind === defs.housing || f.rate !== 0 || !f.status) return;
  ctx.fillStyle = "#e53935";
  ctx.beginPath();
  ctx.arc(px, top + hh, Math.max(2, hh * 0.35), 0, Math.PI * 2);
  ctx.fill();
}

/** 送電線: 隣の送電網のマスへ向けて線を引く。 */
function drawPowerLine(ctx, map, x, y, px, top, theme, facilityAt) {
  const n = map.size;
  const hw = theme.tile_width / 2;
  const hh = theme.tile_height / 2;
  const cx = px;
  const cy = top + hh;
  ctx.strokeStyle = theme.power_line;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  let linked = false;
  for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
    const nx = x + dx;
    const ny = y + dy;
    if (nx < 0 || ny < 0 || nx >= n || ny >= n) continue;
    const j = ny * n + nx;
    if (!map.power_line[j] && !facilityAt.has(j)) continue;
    // x が増えると右下、y が増えると左下
    const ex = cx + (dx - dy) * hw * 0.5;
    const ey = cy + (dx + dy) * hh * 0.5;
    ctx.moveTo(cx, cy);
    ctx.lineTo(ex, ey);
    linked = true;
  }
  ctx.stroke();
  if (!linked) {
    ctx.fillStyle = theme.power_line;
    ctx.fillRect(cx - 1.5, cy - 1.5, 3, 3);
  }
}

function drawHatch(ctx, px, top, theme) {
  const hw = theme.tile_width / 2;
  const hh = theme.tile_height / 2;
  ctx.save();
  diamondPath(ctx, px, top, hw, hh);
  ctx.clip();
  ctx.strokeStyle = theme.no_development;
  ctx.lineWidth = 2;
  ctx.beginPath();
  for (let d = -hw * 2; d <= hw * 2; d += 8) {
    ctx.moveTo(px + d, top);
    ctx.lineTo(px + d + hh * 2, top + hh * 2);
  }
  ctx.stroke();
  ctx.restore();
}

function sides(ctx, px, top, bottom, hw, hh, left, right) {
  ctx.fillStyle = left;
  ctx.beginPath();
  ctx.moveTo(px - hw, top + hh);
  ctx.lineTo(px, top + 2 * hh);
  ctx.lineTo(px, bottom + 2 * hh);
  ctx.lineTo(px - hw, bottom + hh);
  ctx.closePath();
  ctx.fill();
  ctx.fillStyle = right;
  ctx.beginPath();
  ctx.moveTo(px + hw, top + hh);
  ctx.lineTo(px, top + 2 * hh);
  ctx.lineTo(px, bottom + 2 * hh);
  ctx.lineTo(px + hw, bottom + hh);
  ctx.closePath();
  ctx.fill();
}

function outlineTop(ctx, px, top, theme, color) {
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  diamondPath(ctx, px, top, theme.tile_width / 2, theme.tile_height / 2);
  ctx.stroke();
}

function diamondPath(ctx, px, top, hw, hh) {
  ctx.beginPath();
  ctx.moveTo(px, top);
  ctx.lineTo(px + hw, top + hh);
  ctx.lineTo(px, top + 2 * hh);
  ctx.lineTo(px - hw, top + hh);
  ctx.closePath();
}

/** "#rrggbb" の色を factor 倍に暗くする。 */
export function shade(hex, factor) {
  const v = parseInt(hex.slice(1), 16);
  const r = Math.round(((v >> 16) & 255) * factor);
  const g = Math.round(((v >> 8) & 255) * factor);
  const b = Math.round((v & 255) * factor);
  return `rgb(${r}, ${g}, ${b})`;
}
