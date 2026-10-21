// 世界マップの艦隊（フェーズ4-2 の③'）: 自分の艦隊と、見える範囲に入ったほかの国の艦隊を、区画の上に
// 置く。絵は横から見た船（sprites の fleet/<種類>。docs/map-art.md）。自国は黄色、見えている他国は赤、
// 前に見た場所に残した記録は薄く点線の下線で出す。区画の上に重ねる層は押せない（押すのは下の区画のまま）。
// 絵は SVG の <image> ではなく HTML の <img> で置く（小さな画像を区画の割合で縮めると描かれないブラウザがある）。

const WIDTH = 0.45; // 船の絵の幅（区画の 1 辺に対する割合）
const MINE = "#ffca28"; // 自国（site.css の自国の区画の縁の色）
const OTHER = "#ff5252";

function el(tag, css, attrs = {}) {
  const node = document.createElement(tag);
  node.style.cssText = css;
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}

const pct = (v, size) => `${(v / size) * 100}%`;

/** 世界マップ（grid: #world-grid）に艦隊を重ねる。data は /api/world/fleets の返事、sprites は loadSprites の Map。 */
export function drawFleets(grid, data, sprites) {
  const size = Number(grid.style.getPropertyValue("--grid")) || 1;
  const rows = [
    ...data.mine.map((f) => ({ ...f, side: "mine", current: true })),
    ...data.seen.map((f) => ({ ...f, side: "seen" })),
  ];
  if (!rows.length) return;
  // 区画の並び（padding 2px）の上にぴったり重ねる
  const layer = el("div", "position:absolute; inset:2px; pointer-events:none; z-index:3", { "aria-hidden": "true" });
  grid.style.position = "relative";
  const count = new Map();
  const inCell = (f) => rows.filter((g) => g.x === f.x && g.y === f.y).length;
  for (const f of rows) {
    const key = `${f.x},${f.y}`;
    const k = count.get(key) || 0;
    count.set(key, k + 1);
    // 区画の真ん中の少し下に、基準点（絵の下の真ん中）を置く。同じ区画の船は横にずらして真ん中に寄せる
    const cx = f.x + 0.5 + (k - (inCell(f) - 1) / 2) * 0.34;
    const cy = f.y + 0.6;
    const color = f.side === "mine" ? MINE : OTHER;
    const mark = el("div", `position:absolute; left:${pct(cx, size)}; top:${pct(cy, size)}; width:${pct(WIDTH, size)}; transform:translate(-50%, -100%); opacity:${f.current ? 1 : 0.5}`);
    const sprite = sprites.get(`fleet/${f.army}`);
    if (sprite) {
      mark.append(el("img", "display:block; width:100%; height:auto", { src: sprite.img.src, alt: "", title: `${f.name}${f.side === "mine" ? "" : "（他国）"}` }));
    }
    mark.append(el("div", `height:0; border-top:2px ${f.current ? "solid" : "dashed"} ${color}; margin-top:2px`));
    layer.append(mark);
  }
  grid.append(layer);
}
