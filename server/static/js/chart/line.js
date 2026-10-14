// 小さな折れ線グラフ: 横軸をターンにして、1つ以上の系列を Canvas に描く（外部のライブラリは使わない）。
// 凡例は HTML で作り（狭い画面で折り返せるように）、押すとその線を隠す。グラフに指やマウスを乗せると、
// いちばん近いターンの値を出す。値のない所（null）は線を切る。

// 系列の色（並べた順に使う）
export const COLORS = ["#2f6db5", "#d1495b", "#2e8b57", "#e08a00", "#7d5ba6", "#00838f", "#8d6e63", "#607d8b"];

const HEIGHT = 200; // 描く範囲の高さ（CSS ピクセル）
const PAD = { top: 10, right: 12, bottom: 22 };
const FONT = "12px system-ui, sans-serif";

export class LineChart {
  /**
   * container: グラフを入れる要素（中に canvas・値の吹き出し・凡例を作る）
   * options: format（軸の目盛りの数を文字にする関数）、detail（吹き出しの値を文字にする関数。
   *          なければ format）、label（読み上げ用の名前）、zero（0 を必ず縦軸に入れる）、
   *          integer（目盛りを整数にする。お金など）
   */
  constructor(container, options = {}) {
    this.container = container;
    this.format = options.format || ((v) => String(v));
    this.detail = options.detail || this.format;
    this.zero = options.zero ?? false;
    this.integer = options.integer ?? false;
    this.turns = [];
    this.series = [];
    this.hidden = new Set();
    this.hover = null;
    this.loaded = false;

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
    this.legend.className = "chart-legend";
    container.append(this.box, this.legend);

    this.canvas.addEventListener("pointermove", (e) => this.pointAt(e));
    this.canvas.addEventListener("pointerdown", (e) => this.pointAt(e));
    // 指で触ったときは、離しても値を出したままにする（次に触った所へ移る）
    this.canvas.addEventListener("pointerleave", (e) => {
      if (e.pointerType !== "touch") this.leave();
    });
    this.legend.addEventListener("click", (e) => {
      const button = e.target.closest("button[data-series]");
      if (!button) return;
      const key = button.dataset.series;
      if (this.hidden.has(key)) this.hidden.delete(key);
      else this.hidden.add(key);
      button.setAttribute("aria-pressed", String(!this.hidden.has(key)));
      this.draw();
    });
    new ResizeObserver(() => this.draw()).observe(this.box);
  }

  /**
   * turns: ターンの並び。series: [{ key, label, values（turns と同じ長さ）, color? }]
   */
  setData(turns, series) {
    this.loaded = true;
    this.error = false;
    this.turns = turns;
    this.series = series.map((s, i) => ({ ...s, color: s.color || COLORS[i % COLORS.length] }));
    this.hover = null;
    this.tip.hidden = true;
    this.renderLegend();
    this.draw();
  }

  /** 読み込めなかったことを出す。 */
  fail() {
    this.setData([], []);
    this.error = true;
    this.draw();
  }

  renderLegend() {
    this.legend.innerHTML = "";
    if (this.series.length < 2) return; // 1本だけなら見出しで足りる
    for (const s of this.series) {
      const li = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.series = s.key;
      button.setAttribute("aria-pressed", String(!this.hidden.has(s.key)));
      const swatch = document.createElement("span");
      swatch.className = "swatch";
      swatch.style.background = s.color;
      button.append(swatch, document.createTextNode(s.label));
      li.append(button);
      this.legend.append(li);
    }
  }

  visible() {
    return this.series.filter((s) => !this.hidden.has(s.key));
  }

  /** 縦軸の範囲と目盛り。 */
  scale() {
    let lo = Infinity;
    let hi = -Infinity;
    for (const s of this.visible()) {
      for (const v of s.values) {
        if (v == null || !Number.isFinite(v)) continue;
        lo = Math.min(lo, v);
        hi = Math.max(hi, v);
      }
    }
    if (lo === Infinity) return null;
    if (this.zero) {
      lo = Math.min(lo, 0);
      hi = Math.max(hi, 0);
    }
    if (lo === hi && lo === 0) {
      hi = 1; // ずっと 0 なら 0 を下の端にする
    } else if (lo === hi) {
      const pad = Math.abs(lo) * 0.1 || 1;
      lo -= pad;
      hi += pad;
    }
    let step = niceStep((hi - lo) / 4);
    if (this.integer) step = Math.max(1, step);
    const ticks = [];
    // 一番下の目盛りから、一番上の値を超えるまで並べる
    for (let k = Math.floor(lo / step); ; k++) {
      ticks.push(roundTo(k * step, step));
      if (k * step >= hi - step * 1e-9) break;
    }
    return { lo: ticks[0], hi: ticks[ticks.length - 1], ticks };
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
    const grid = style.getPropertyValue("--chart-grid").trim() || "#e6e2da";

    const y = this.scale();
    if (!y || !this.turns.length) {
      ctx.fillStyle = muted;
      ctx.textAlign = "center";
      let text = this.series.length ? "表示する線がありません" : "記録がありません";
      if (!this.loaded) text = "読み込み中…";
      if (this.error) text = "読み込めませんでした";
      ctx.fillText(text, width / 2, HEIGHT / 2);
      this.layout = null;
      return;
    }
    const labels = y.ticks.map((v) => this.format(v));
    const left = Math.ceil(Math.max(...labels.map((t) => ctx.measureText(t).width))) + 10;
    const plot = { x0: left, x1: width - PAD.right, y0: PAD.top, y1: HEIGHT - PAD.bottom };
    const t0 = this.turns[0];
    const t1 = this.turns[this.turns.length - 1];
    const xOf = (t) => (t1 === t0 ? (plot.x0 + plot.x1) / 2 : plot.x0 + ((t - t0) / (t1 - t0)) * (plot.x1 - plot.x0));
    const yOf = (v) => plot.y1 - ((v - y.lo) / (y.hi - y.lo)) * (plot.y1 - plot.y0);
    this.layout = { plot, xOf, yOf };

    // 目盛りと格子
    ctx.lineWidth = 1;
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    y.ticks.forEach((v, i) => {
      const py = Math.round(yOf(v)) + 0.5;
      ctx.strokeStyle = v === 0 && y.lo < 0 ? muted : grid;
      ctx.beginPath();
      ctx.moveTo(plot.x0, py);
      ctx.lineTo(plot.x1, py);
      ctx.stroke();
      ctx.fillStyle = muted;
      ctx.fillText(labels[i], plot.x0 - 6, py);
    });
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    const span = t1 - t0;
    const every = span > 0 ? niceTurnStep(span / Math.max(1, Math.floor((plot.x1 - plot.x0) / 60))) : 1;
    for (let t = Math.ceil(t0 / every) * every; t <= t1; t += every) {
      ctx.fillText(String(t), xOf(t), plot.y1 + 5);
    }
    if (span === 0) ctx.fillText(String(t0), xOf(t0), plot.y1 + 5);

    // 線
    for (const s of this.visible()) {
      ctx.strokeStyle = s.color;
      ctx.fillStyle = s.color;
      ctx.lineWidth = 2;
      ctx.lineJoin = "round";
      ctx.beginPath();
      let open = false;
      s.values.forEach((v, i) => {
        if (v == null || !Number.isFinite(v)) {
          open = false;
          return;
        }
        const px = xOf(this.turns[i]);
        const py = yOf(v);
        if (open) ctx.lineTo(px, py);
        else ctx.moveTo(px, py);
        // 前後に値のない1点だけの値は点で描く
        const lonely = !open && (i + 1 >= s.values.length || s.values[i + 1] == null);
        if (lonely) ctx.fillRect(px - 2, py - 2, 4, 4);
        open = true;
      });
      ctx.stroke();
    }

    // 指を乗せたターン
    if (this.hover != null) {
      const px = Math.round(xOf(this.turns[this.hover])) + 0.5;
      ctx.strokeStyle = muted;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(px, plot.y0);
      ctx.lineTo(px, plot.y1);
      ctx.stroke();
      for (const s of this.visible()) {
        const v = s.values[this.hover];
        if (v == null || !Number.isFinite(v)) continue;
        ctx.fillStyle = s.color;
        ctx.beginPath();
        ctx.arc(px, yOf(v), 3.5, 0, Math.PI * 2);
        ctx.fill();
      }
    }
  }

  pointAt(e) {
    if (!this.layout || !this.turns.length) return;
    const r = this.canvas.getBoundingClientRect();
    const x = e.clientX - r.left;
    let best = 0;
    let dist = Infinity;
    this.turns.forEach((t, i) => {
      const d = Math.abs(this.layout.xOf(t) - x);
      if (d < dist) [best, dist] = [i, d];
    });
    if (this.hover !== best) {
      this.hover = best;
      this.draw();
    }
    this.showTip(x);
  }

  showTip(x) {
    const i = this.hover;
    const rows = this.visible()
      .map((s) => {
        const v = s.values[i];
        return `<li><span class="swatch" style="background:${s.color}"></span>${escape(s.label)}: <strong>${escape(this.detail(v))}</strong></li>`;
      })
      .join("");
    this.tip.innerHTML = `<div>ターン ${this.turns[i]}</div><ul>${rows}</ul>`;
    this.tip.hidden = false;
    // 吹き出しは指の反対側に出し、グラフの外にはみ出さないようにする
    const box = this.box.getBoundingClientRect();
    const w = this.tip.offsetWidth;
    const left = x > box.width / 2 ? Math.max(0, x - w - 12) : Math.min(box.width - w, x + 12);
    this.tip.style.left = `${left}px`;
  }

  leave() {
    this.hover = null;
    this.tip.hidden = true;
    this.draw();
  }
}

/** 目盛りの幅: 1・2・5 × 10 の累乗のうち、step 以上でいちばん小さいもの。 */
function niceStep(step) {
  if (!(step > 0)) return 1;
  const p = 10 ** Math.floor(Math.log10(step));
  for (const k of [1, 2, 5, 10]) if (k * p >= step) return k * p;
  return 10 * p;
}

/** ターンの目盛りの幅（1年 = 12 ターンの区切りを好む）。 */
function niceTurnStep(step) {
  for (const k of [1, 2, 3, 6, 12, 24, 60, 120, 240, 600, 1200]) if (k >= step) return k;
  return niceStep(step);
}

/** 目盛りの値の端数（0.1 + 0.2 のような）を落とす。 */
function roundTo(value, step) {
  const digits = Math.max(0, -Math.floor(Math.log10(step)) + 1);
  return Number(value.toFixed(Math.min(digits, 12)));
}

function escape(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}
