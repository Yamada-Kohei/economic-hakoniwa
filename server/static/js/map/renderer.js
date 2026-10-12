// 描画: 奥のマスから手前のマスへ（x + y の小さい順に）描き、高いものが奥を自然に隠すようにする。

import { lift, tileToWorld } from "./projection.js";

export function drawIsland(ctx, map, defs, view, selected) {
  const theme = defs.theme;
  const { width, height } = ctx.canvas;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, width, height);
  ctx.setTransform(view.scale * view.dpr, 0, 0, view.scale * view.dpr, view.offsetX * view.dpr, view.offsetY * view.dpr);

  const n = map.size;
  for (let s = 0; s <= 2 * (n - 1); s++) {
    for (let x = Math.max(0, s - n + 1); x <= Math.min(s, n - 1); x++) {
      const y = s - x;
      const terrain = map.terrain[y * n + x];
      const colors = theme.terrain[terrain] || theme.fallback;
      const h = lift(defs.terrain[terrain]?.height ?? 0, theme);
      const { px, py } = tileToWorld(x, y, theme);
      drawBlock(ctx, px, py, h, colors, theme);
      if (selected && selected.x === x && selected.y === y) {
        outlineTop(ctx, px, py - h, theme);
      }
    }
  }
}

function drawBlock(ctx, px, py, h, colors, theme) {
  const hw = theme.tile_width / 2;
  const hh = theme.tile_height / 2;
  const top = py - h;
  if (h > 0) {
    // 左の側面
    ctx.fillStyle = colors.left;
    ctx.beginPath();
    ctx.moveTo(px - hw, top + hh);
    ctx.lineTo(px, top + 2 * hh);
    ctx.lineTo(px, py + 2 * hh);
    ctx.lineTo(px - hw, py + hh);
    ctx.closePath();
    ctx.fill();
    // 右の側面
    ctx.fillStyle = colors.right;
    ctx.beginPath();
    ctx.moveTo(px + hw, top + hh);
    ctx.lineTo(px, top + 2 * hh);
    ctx.lineTo(px, py + 2 * hh);
    ctx.lineTo(px + hw, py + hh);
    ctx.closePath();
    ctx.fill();
  }
  // 上面
  ctx.fillStyle = colors.top;
  diamondPath(ctx, px, top, hw, hh);
  ctx.fill();
}

function outlineTop(ctx, px, top, theme) {
  ctx.strokeStyle = "#ffeb3b";
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
