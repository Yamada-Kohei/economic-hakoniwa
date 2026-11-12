// 地図の縁の目盛りと、指（カーソル）の下のマスの座標（UI-85、掲示板 #25 の 45 番）。
// 目盛りは島の奥の2辺（y = 0 の辺に x、x = 0 の辺に y）の外に、STEP マスごとに番号を書く。
// 拡大して辺が地図の外に出たら、その列（行）に沿って地図の縁の内側まで寄せて書く。
// 字の大きさは拡大縮小によらず同じにする（画面の座標で描く）。

import { tileToWorld } from "./projection.js";

export const STEP = 5;
const OUTSIDE = 0.7; // 辺からマスの何個分外に書くか

/** 拡大前の位置を、地図の画面の位置（CSS ピクセル）に。 */
function toScreen(p, view) {
  return { x: p.px * view.scale + view.offsetX, y: p.py * view.scale + view.offsetY };
}

const MARGIN = 14; // 地図の縁から字の中心までの最小の間（CSS ピクセル）

export function drawRuler(ctx, map, view, theme) {
  const n = map.size;
  const width = ctx.canvas.width / view.dpr;
  const height = ctx.canvas.height / view.dpr;
  const inside = (s) => s.x >= MARGIN && s.x <= width - MARGIN && s.y >= MARGIN && s.y <= height - MARGIN;
  /** 列（行）に沿って辺の外から内へ進み、初めて地図の縁の内側に入る位置。入らなければ null。 */
  const along = (at) => {
    for (let t = -OUTSIDE; t <= n; t += 0.25) {
      const p = at(t);
      if (inside(toScreen(p, view))) return p;
    }
    return null;
  };
  ctx.save();
  ctx.setTransform(view.dpr, 0, 0, view.dpr, 0, 0);
  ctx.font = "12px sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.lineWidth = 3;
  ctx.strokeStyle = "rgba(10, 30, 60, 0.85)";
  const label = (text, p, color) => {
    if (!p) return;
    const s = toScreen(p, view);
    ctx.fillStyle = color;
    ctx.strokeText(text, s.x, s.y);
    ctx.fillText(text, s.x, s.y);
  };
  const xColor = "#ffd75e";
  const yColor = "#8ff0ff";
  for (let i = 0; i < n; i += STEP) {
    label(`x${i}`, along((t) => tileToWorld(i + 0.5, t, theme)), xColor);
    label(`y${i}`, along((t) => tileToWorld(t, i + 0.5, theme)), yColor);
  }
  ctx.restore();
}
