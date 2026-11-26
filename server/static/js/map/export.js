// 地図の書き出し基盤（案30）: UI の無いきれいな一枚絵として島の地図を描く。
// 国のカード・絵はがき（季節の色）・ターンの新聞の地図の切り抜きは、みなこの基盤に乗る。
// 描く物は renderer.js の drawIsland そのもの（重ね表示・選択・マスの窓は入れない）。

import { drawIsland } from "./renderer.js";
import { lift, tileToWorld } from "./projection.js";

// 施設の絵は地面より上に伸びるので、上の余白は大きめに取る（絵の高さの上限の代わり）
export const EXPORT_MARGIN = { top: 150, side: 24, bottom: 24 };
export const EXPORT_SCALE = 0.5; // 32 マスで幅およそ 1000 ピクセル

/** 島全体が収まる範囲（拡大前の座標で {minX, minY, width, height}）。 */
export function mapBounds(map, defs) {
  const theme = defs.theme;
  const n = map.size;
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (let y = 0; y < n; y++) {
    for (let x = 0; x < n; x++) {
      const h = lift(defs.terrain[map.terrain[y * n + x]]?.height ?? 0, theme);
      const { px, py } = tileToWorld(x, y, theme);
      minX = Math.min(minX, px - theme.tile_width / 2);
      maxX = Math.max(maxX, px + theme.tile_width / 2);
      minY = Math.min(minY, py - h);
      maxY = Math.max(maxY, py + theme.tile_height);
    }
  }
  return {
    minX: minX - EXPORT_MARGIN.side,
    minY: minY - EXPORT_MARGIN.top,
    width: maxX - minX + EXPORT_MARGIN.side * 2,
    height: maxY - minY + EXPORT_MARGIN.top + EXPORT_MARGIN.bottom,
  };
}

/**
 * 島の地図だけを描いた canvas を作る。
 * options: sprites（sprites.js の Map）、flag（首都の旗。loadFlag の返り値）、
 *          season（季節の色調を入れるか。絵はがき用）、scale（書き出しの倍率）
 * 背景は海の上面の色で塗る（透過のままでは見る道具によって島が浮いて見える）。
 */
export function renderMapCanvas(map, defs, options = {}) {
  const scale = options.scale ?? EXPORT_SCALE;
  const b = mapBounds(map, defs);
  const inner = document.createElement("canvas");
  inner.width = Math.ceil(b.width * scale);
  inner.height = Math.ceil(b.height * scale);
  const ictx = inner.getContext("2d");
  const view = { scale, offsetX: -b.minX * scale, offsetY: -b.minY * scale, dpr: 1 };
  drawIsland(ictx, map, defs, view, {
    sprites: options.sprites || new Map(),
    flag: options.flag || null,
    season: Boolean(options.season),
    smoke: false,
    light: true,
  });
  const canvas = document.createElement("canvas");
  canvas.width = inner.width;
  canvas.height = inner.height;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = defs.theme.terrain.sea?.top || "#2f6db5";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(inner, 0, 0);
  return canvas;
}

/** canvas を PNG のファイルとして保存させる。 */
export function downloadCanvas(canvas, filename) {
  canvas.toBlob((blob) => {
    if (!blob) return;
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  }, "image/png");
}

// --- 国のカード（案30） -------------------------------------------------------
// 旗・国名・首都・元首・人口・GDP・節目と地図を 1 枚に収める。文言は全部
// サーバーから渡す（card.py の labels と lines）。ここは置き場所だけを持つ。

export const CARD = {
  width: 1200,
  height: 760,
  pad: 48,
  mapSize: 592,
  paper: "#faf6ec",
  ink: "#26303b",
  sub: "#5a6672",
  frame: "#c9c2b2",
  defaultAccent: "#2f6db5",
};

/** カードの置き場所（map は右、文字は左）。 */
export function cardLayout() {
  const mapX = CARD.width - CARD.pad - CARD.mapSize;
  return {
    accentBar: { x: 0, y: 0, w: CARD.width, h: 10 },
    flag: { x: CARD.pad, y: CARD.pad, h: 96 },
    title: { x: CARD.pad, y: CARD.pad + 128 },
    lines: { x: CARD.pad, y: CARD.pad + 190, width: mapX - CARD.pad * 2 },
    map: { x: mapX, y: CARD.pad, w: CARD.mapSize, h: CARD.mapSize },
    mapDate: { x: mapX, y: CARD.pad + CARD.mapSize + 34 },
    foot: { x: CARD.pad, y: CARD.height - 30 },
  };
}

/** 幅に収まるよう、文字を 1 字ずつ折り返す（日本語は単語の区切りが無い）。 */
export function wrapLines(ctx, text, maxWidth, maxLines = Infinity) {
  const out = [];
  let line = "";
  for (const ch of String(text)) {
    if (ch === "\n") {
      out.push(line);
      line = "";
      continue;
    }
    if (line && ctx.measureText(line + ch).width > maxWidth) {
      out.push(line);
      line = ch;
      if (out.length >= maxLines) return out;
    } else {
      line += ch;
    }
  }
  if (line || !out.length) out.push(line);
  return out.slice(0, maxLines);
}

/**
 * 国のカードを描く。
 * data: { title, lines: [{label, value}], milestones: [{date, text}],
 *         milestonesLabel, dateText, foot, accent }
 * flagImg は読み込めた旗の Image（なければ null で仮の枠だけ）。
 */
export function drawCountryCard(data, mapCanvas, flagImg) {
  const canvas = document.createElement("canvas");
  canvas.width = CARD.width;
  canvas.height = CARD.height;
  const ctx = canvas.getContext("2d");
  const L = cardLayout();
  ctx.fillStyle = CARD.paper;
  ctx.fillRect(0, 0, CARD.width, CARD.height);
  ctx.fillStyle = data.accent || CARD.defaultAccent;
  ctx.fillRect(L.accentBar.x, L.accentBar.y, L.accentBar.w, L.accentBar.h);

  // 地図（右）。縦横比を保って枠に収める
  const scale = Math.min(L.map.w / mapCanvas.width, L.map.h / mapCanvas.height);
  const mw = Math.floor(mapCanvas.width * scale);
  const mh = Math.floor(mapCanvas.height * scale);
  ctx.drawImage(mapCanvas, L.map.x + (L.map.w - mw) / 2, L.map.y + (L.map.h - mh) / 2, mw, mh);
  ctx.strokeStyle = CARD.frame;
  ctx.strokeRect(L.map.x, L.map.y, L.map.w, L.map.h);
  ctx.fillStyle = CARD.sub;
  ctx.font = "20px sans-serif";
  ctx.textAlign = "center";
  ctx.fillText(data.dateText || "", L.map.x + L.map.w / 2, L.mapDate.y);
  ctx.textAlign = "left";

  // 旗（左上）。幅は高さに合わせ、上限を置く
  let titleX = L.title.x;
  if (flagImg) {
    const fw = Math.min(200, Math.floor((flagImg.naturalWidth / flagImg.naturalHeight) * L.flag.h));
    ctx.drawImage(flagImg, L.flag.x, L.flag.y, fw, L.flag.h);
    ctx.strokeStyle = CARD.frame;
    ctx.strokeRect(L.flag.x, L.flag.y, fw, L.flag.h);
    titleX = L.flag.x + fw + 24;
  }
  ctx.fillStyle = CARD.ink;
  ctx.font = "bold 42px sans-serif";
  ctx.fillText(data.title || "", titleX, L.title.y + 30, CARD.width - titleX - CARD.pad);

  // 要目（首都・元首・人口・GDP など）
  let y = L.lines.y;
  ctx.font = "24px sans-serif";
  for (const row of data.lines || []) {
    ctx.fillStyle = CARD.sub;
    ctx.fillText(row.label, L.lines.x, y);
    ctx.fillStyle = CARD.ink;
    ctx.fillText(row.value, L.lines.x + 130, y, L.lines.width - 130);
    y += 38;
  }

  // 節目
  y += 18;
  ctx.fillStyle = data.accent || CARD.defaultAccent;
  ctx.font = "bold 24px sans-serif";
  ctx.fillText(data.milestonesLabel || "", L.lines.x, y);
  y += 34;
  ctx.font = "20px sans-serif";
  for (const m of data.milestones || []) {
    ctx.fillStyle = CARD.sub;
    ctx.fillText(m.date, L.lines.x, y);
    ctx.fillStyle = CARD.ink;
    const lines = wrapLines(ctx, m.text, L.lines.width - 170, 2);
    ctx.fillText(lines[0] || "", L.lines.x + 170, y);
    if (lines[1]) {
      y += 26;
      ctx.fillText(lines[1], L.lines.x + 170, y);
    }
    y += 32;
    if (y > CARD.height - 70) break;
  }

  ctx.fillStyle = CARD.sub;
  ctx.font = "18px sans-serif";
  ctx.fillText(data.foot || "", L.foot.x, L.foot.y);
  return canvas;
}
