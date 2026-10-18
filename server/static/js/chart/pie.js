// 小さな円グラフ: 全体を分けた割合を、ある1つのターンで見る（外部のライブラリは使わない）。
// 時間の流れは折れ線（line.js）で見て、こちらは内訳を見る。切れが多いと読めないので、大きい順に
// MAX_SLICES - 1 個まで出し、残りは「そのほか」にまとめる。0 以下の値は切れにしない。
// 凡例は HTML で作り、名前・値・割合を並べる。円に指やマウスを乗せると、その切れの値と割合を出す。

import { COLORS } from "./line.js";

const HEIGHT = 200; // 描く範囲の高さ（CSS ピクセル）
const MAX_SLICES = 7; // 切れの数の上限（「そのほか」を含む）
const OTHER_COLOR = "#9e9e9e";
const FONT = "12px system-ui, sans-serif";

export class PieChart {
  /**
   * container: グラフを入れる要素（中に canvas・値の吹き出し・凡例を作る）
   * options: format（値を文字にする関数）、label（読み上げ用の名前）
   */
  constructor(container, options = {}) {
    this.container = container;
    this.format = options.format || ((v) => String(v));
    this.slices = [];
    this.total = 0;
    this.hover = null;
    this.loaded = false;
    this.error = false;

    this.box = document.createElement("div");
    this.box.className = "chart-box";
    this.canvas = document.createElement("canvas");
    this.canvas.setAttribute("role", "img");
    if (options.label) this.canvas.setAttribute("aria-label", options.label);
    this.tip = document.createElement("div");
    this.tip.className = "chart-tip";
    this.tip.hidden = true;
    this.box.append(this.canvas, this.tip);
    this.legend = document.createElement("ul");
    this.legend.className = "pie-legend";
    container.append(this.box, this.legend);

    this.canvas.addEventListener("pointermove", (e) => this.pointAt(e));
    this.canvas.addEventListener("pointerdown", (e) => this.pointAt(e));
    this.canvas.addEventListener("pointerleave", (e) => {
      if (e.pointerType !== "touch") this.leave();
    });
    new ResizeObserver(() => this.draw()).observe(this.box);
  }

  /** items: [{ key, label, value }]。0 以下と値のないものは除き、多すぎる分は「そのほか」にまとめる。 */
  setData(items) {
    this.loaded = true;
    this.error = false;
    const positive = items.filter((s) => s.value != null && Number.isFinite(s.value) && s.value > 0);
    positive.sort((a, b) => b.value - a.value);
    let slices = positive;
    if (positive.length > MAX_SLICES) {
      const rest = positive.slice(MAX_SLICES - 1);
      slices = positive.slice(0, MAX_SLICES - 1);
      slices.push({ key: "_other", label: `そのほか（${rest.length} 項目）`, value: rest.reduce((t, s) => t + s.value, 0) });
    }
    this.slices = slices.map((s, i) => ({ ...s, color: s.key === "_other" ? OTHER_COLOR : COLORS[i % COLORS.length] }));
    this.total = this.slices.reduce((t, s) => t + s.value, 0);
    this.hover = null;
    this.tip.hidden = true;
    this.renderLegend();
    this.draw();
  }

  /** 読み込めなかったことを出す。 */
  fail() {
    this.setData([]);
    this.error = true;
    this.draw();
  }

  share(value) {
    return this.total > 0 ? `${((value / this.total) * 100).toFixed(1)}%` : "-";
  }

  renderLegend() {
    this.legend.innerHTML = "";
    for (const s of this.slices) {
      const li = document.createElement("li");
      const swatch = document.createElement("span");
      swatch.className = "swatch";
      swatch.style.background = s.color;
      const name = document.createElement("span");
      name.className = "pie-name";
      name.append(swatch, document.createTextNode(s.label));
      const value = document.createElement("span");
      value.className = "pie-value";
      value.textContent = `${this.format(s.value)}（${this.share(s.value)}）`;
      li.append(name, value);
      this.legend.append(li);
    }
  }

  draw() {
    const rect = this.box.getBoundingClientRect();
    const width = Math.max(120, rect.width);
    const dpr = window.devicePixelRatio || 1;
    this.canvas.style.height = `${HEIGHT}px`;
    if (this.canvas.width !== Math.round(width * dpr) || this.canvas.height !== Math.round(HEIGHT * dpr)) {
      this.canvas.width = Math.round(width * dpr);
      this.canvas.height = Math.round(HEIGHT * dpr);
    }
    const ctx = this.canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, HEIGHT);
    ctx.font = FONT;
    const style = getComputedStyle(this.container);
    const muted = style.getPropertyValue("--chart-muted").trim() || "#666";

    if (!this.slices.length) {
      ctx.fillStyle = muted;
      ctx.textAlign = "center";
      let text = "記録がありません";
      if (!this.loaded) text = "読み込み中…";
      if (this.error) text = "読み込めませんでした";
      ctx.fillText(text, width / 2, HEIGHT / 2);
      this.layout = null;
      return;
    }
    const r = HEIGHT / 2 - 8;
    const cx = width / 2;
    const cy = HEIGHT / 2;
    this.layout = { cx, cy, r, arcs: [] };
    let start = -Math.PI / 2; // 12 時の向きから時計回り
    this.slices.forEach((s, i) => {
      const end = start + (s.value / this.total) * Math.PI * 2;
      this.layout.arcs.push({ start, end });
      const out = i === this.hover ? 6 : 0; // 指した切れは少し外へずらす
      const mid = (start + end) / 2;
      const ox = Math.cos(mid) * out;
      const oy = Math.sin(mid) * out;
      ctx.beginPath();
      ctx.moveTo(cx + ox, cy + oy);
      ctx.arc(cx + ox, cy + oy, r, start, end);
      ctx.closePath();
      ctx.fillStyle = s.color;
      ctx.fill();
      ctx.strokeStyle = "#fff";
      ctx.lineWidth = 1.5;
      ctx.stroke();
      start = end;
    });
  }

  pointAt(e) {
    if (!this.layout) return;
    const rect = this.canvas.getBoundingClientRect();
    const x = e.clientX - rect.left - this.layout.cx;
    const y = e.clientY - rect.top - this.layout.cy;
    if (Math.hypot(x, y) > this.layout.r) {
      this.leave();
      return;
    }
    let angle = Math.atan2(y, x);
    if (angle < -Math.PI / 2) angle += Math.PI * 2; // 12 時の向きから測る
    const index = this.layout.arcs.findIndex((a) => angle >= a.start && angle < a.end);
    if (index < 0) return;
    if (index !== this.hover) {
      this.hover = index;
      this.draw();
    }
    const s = this.slices[index];
    this.tip.textContent = `${s.label}: ${this.format(s.value)}（${this.share(s.value)}）`;
    this.tip.hidden = false;
    const left = e.clientX - rect.left + 12;
    this.tip.style.left = `${Math.min(left, rect.width - this.tip.offsetWidth - 4)}px`;
  }

  leave() {
    this.tip.hidden = true;
    if (this.hover !== null) {
      this.hover = null;
      this.draw();
    }
  }
}
