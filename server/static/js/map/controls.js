// 操作: ドラッグで移動、ホイールとピンチで拡大縮小、タップでマスを選ぶ。

const MIN_SCALE = 0.25;
const MAX_SCALE = 4;
const TAP_SLOP = 6; // これより動いたらタップではなくドラッグとみなす（ピクセル）

export function attachControls(canvas, view, { onChange, onTap }) {
  const pointers = new Map();
  let moved = 0;
  let pinchStart = null;

  function zoomAt(factor, cx, cy) {
    const next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, view.scale * factor));
    const k = next / view.scale;
    view.offsetX = cx - (cx - view.offsetX) * k;
    view.offsetY = cy - (cy - view.offsetY) * k;
    view.scale = next;
    onChange();
  }

  function local(e) {
    const r = canvas.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }

  canvas.addEventListener("pointerdown", (e) => {
    canvas.setPointerCapture(e.pointerId);
    pointers.set(e.pointerId, local(e));
    moved = 0;
    if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      pinchStart = { dist: Math.hypot(a.x - b.x, a.y - b.y), scale: view.scale };
    }
  });

  canvas.addEventListener("pointermove", (e) => {
    if (!pointers.has(e.pointerId)) return;
    const prev = pointers.get(e.pointerId);
    const cur = local(e);
    pointers.set(e.pointerId, cur);
    if (pointers.size === 1) {
      view.offsetX += cur.x - prev.x;
      view.offsetY += cur.y - prev.y;
      moved += Math.abs(cur.x - prev.x) + Math.abs(cur.y - prev.y);
      onChange();
    } else if (pointers.size === 2 && pinchStart) {
      const [a, b] = [...pointers.values()];
      const dist = Math.hypot(a.x - b.x, a.y - b.y);
      const target = pinchStart.scale * (dist / pinchStart.dist);
      moved = Infinity;
      zoomAt(target / view.scale, (a.x + b.x) / 2, (a.y + b.y) / 2);
    }
  });

  function release(e) {
    if (!pointers.has(e.pointerId)) return;
    const p = pointers.get(e.pointerId);
    pointers.delete(e.pointerId);
    if (pointers.size < 2) pinchStart = null;
    if (pointers.size === 0 && moved < TAP_SLOP) onTap(p.x, p.y);
  }
  canvas.addEventListener("pointerup", release);
  canvas.addEventListener("pointercancel", (e) => {
    pointers.delete(e.pointerId);
    pinchStart = null;
  });

  canvas.addEventListener(
    "wheel",
    (e) => {
      e.preventDefault();
      const p = local(e);
      zoomAt(e.deltaY < 0 ? 1.1 : 1 / 1.1, p.x, p.y);
    },
    { passive: false },
  );

  return { zoomAt };
}
