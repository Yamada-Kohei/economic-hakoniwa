// 世界マップの島の形: 島の1マスを1ピクセルとして、地形と町・施設の色で描く。

/** 記号の意味（"terrain:plain" など）から色を決める。 */
function colorOf(key, defs) {
  const theme = defs.theme;
  const [kind, id] = key.split(":");
  if (kind === "terrain") return (theme.terrain[id] || theme.fallback).top;
  if (kind === "capital") return theme.capital_roof;
  if (kind === "construction") return theme.construction;
  if (kind === "settlement") {
    // 町は大きく育つほど濃く描く
    const levels = Object.keys(defs.settlements);
    const step = Math.max(1, levels.length - 1);
    return mix(theme.facilities?.[defs.housing]?.color || "#d9826b", "#5a1f12", levels.indexOf(id) / step * 0.6);
  }
  if (kind === "facility") return (theme.facilities?.[id] || theme.facility_fallback).color;
  return theme.fallback.top;
}

function mix(a, b, t) {
  const pa = parseInt(a.slice(1), 16);
  const pb = parseInt(b.slice(1), 16);
  const channel = (shift) => Math.round(((pa >> shift) & 255) * (1 - t) + ((pb >> shift) & 255) * t);
  return `rgb(${channel(16)}, ${channel(8)}, ${channel(0)})`;
}

/** 島 { size, cells } を canvas に描く。codes は記号 → 意味の表。 */
export function drawIslandPixels(canvas, island, codes, defs) {
  const n = island.size;
  canvas.width = n;
  canvas.height = n;
  const ctx = canvas.getContext("2d");
  const colors = {};
  for (const [symbol, key] of Object.entries(codes)) colors[symbol] = colorOf(key, defs);
  for (let i = 0; i < n * n; i++) {
    ctx.fillStyle = colors[island.cells[i]] || defs.theme.fallback.top;
    ctx.fillRect(i % n, Math.floor(i / n), 1, 1);
  }
}

/** container の中の canvas[data-island] に、それぞれの島の形を描く。 */
export function drawAllIslands(container, shapes, defs) {
  const byId = new Map(shapes.islands.map((i) => [String(i.id), i]));
  for (const canvas of container.querySelectorAll("canvas[data-island]")) {
    const island = byId.get(canvas.dataset.island);
    if (island) drawIslandPixels(canvas, island, shapes.codes, defs);
  }
}
