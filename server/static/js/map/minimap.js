// 小さな地図: 島を真上から1マス数ピクセルで描き、押した所へ大きな地図を動かす。

export function drawMinimap(canvas, map, defs, highlight) {
  const ctx = canvas.getContext("2d");
  const n = map.size;
  const cell = Math.floor(canvas.width / n);
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const facilityAt = new Map();
  for (const f of map.facilities) facilityAt.set(f.y * n + f.x, f);
  for (let y = 0; y < n; y++) {
    for (let x = 0; x < n; x++) {
      const i = y * n + x;
      const f = facilityAt.get(i);
      const style = f && (defs.theme.facilities?.[f.kind] || defs.theme.facility_fallback);
      ctx.fillStyle = style ? style.color : (defs.theme.terrain[map.terrain[i]] || defs.theme.fallback).top;
      ctx.fillRect(x * cell, y * cell, cell, cell);
    }
  }
  if (highlight) {
    ctx.strokeStyle = "#ffeb3b";
    ctx.lineWidth = 2;
    ctx.strokeRect(highlight.x * cell, highlight.y * cell, cell, cell);
  }
}

/** 小さな地図の上の位置 (sx, sy) にあるマス。 */
export function minimapTile(canvas, map, sx, sy) {
  const r = canvas.getBoundingClientRect();
  const n = map.size;
  const x = Math.floor(((sx - r.left) / r.width) * n);
  const y = Math.floor(((sy - r.top) / r.height) * n);
  if (x < 0 || y < 0 || x >= n || y >= n) return null;
  return { x, y };
}
