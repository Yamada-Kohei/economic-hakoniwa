// 命令の列: 次のターンに上から実行する命令を並べる。行政力が足りなくなった所から下は次のターンに回る。
// 列を変えるたびに、列を丸ごとサーバーに送って保存する。

import { escapeHtml, getJson, sendJson } from "./api.js";

export class OrderQueue {
  /** el: 欄の要素（data-url に命令の列の API の URL） */
  constructor(el, defs, map) {
    this.el = el;
    this.defs = defs;
    this.map = map;
    this.url = el.dataset.url;
    this.items = [];
    this.recent = [];
    this.adminPower = 0;
    this.warnings = new Map(); // 列の位置 → 文
    this.message = "";
    this.saving = Promise.resolve();
    el.addEventListener("click", (e) => this.handleClick(e));
    el.addEventListener("submit", (e) => this.handleSubmit(e));
    this.load();
  }

  async load() {
    try {
      this.apply(await getJson(this.url));
    } catch {
      this.message = "命令の列を読めませんでした。";
    }
    this.render();
  }

  apply(data) {
    this.items = data.orders.map((o) => ({ kind: o.kind, payload: o.payload, cost: o.cost, text: o.text }));
    this.recent = data.recent || this.recent;
    this.adminPower = data.admin_power;
    this.warnings = new Map((data.warnings || []).map((w) => [w.index, w.text]));
  }

  /** 命令を列の最後に足して保存する。 */
  add(kind, payload) {
    const cost = this.defs.orders[kind]?.admin_cost ?? 0;
    this.items.push({ kind, payload, cost, text: `${this.defs.orders[kind]?.name || kind}（保存中）` });
    this.save(`「${this.defs.orders[kind]?.name || kind}」を列に足しました。`);
  }

  save(done) {
    // 保存は1つずつ順に送る（先に送った列が後で上書きしないように）
    const items = this.items.map(({ kind, payload }) => ({ kind, payload }));
    this.render();
    this.saving = this.saving.then(async () => {
      const res = await sendJson(this.url, "PUT", { orders: items });
      if (res.ok) {
        this.apply(res.data);
        this.message = done || "保存しました。";
      } else if (res.data.errors) {
        this.message = res.data.errors.map((e) => `${e.index + 1} 番目: ${e.text}`).join(" ");
        await this.load();
      } else {
        this.message = res.data.error || "保存できませんでした。";
        await this.load();
      }
      this.render();
    });
  }

  render() {
    let used = 0;
    let cut = false;
    const rows = this.items.map((o, i) => {
      used += o.cost;
      const late = used > this.adminPower;
      const divider = late && !cut ? `<li class="divider">ここから下は行政力が足りず、次のターン以降に回ります</li>` : "";
      if (late) cut = true;
      const warning = this.warnings.get(i);
      return `${divider}
        <li class="${late ? "late" : ""}">
          <span class="order-text">${i + 1}. ${escapeHtml(o.text)} <small>（行政力 ${o.cost}）</small></span>
          ${warning ? `<span class="warning">今のままでは実行できません: ${escapeHtml(warning)}</span>` : ""}
          <span class="order-buttons">
            <button type="button" data-move="-1" data-index="${i}" aria-label="上へ"${i === 0 ? " disabled" : ""}>↑</button>
            <button type="button" data-move="1" data-index="${i}" aria-label="下へ"${i === this.items.length - 1 ? " disabled" : ""}>↓</button>
            <button type="button" data-remove="${i}" aria-label="消す">×</button>
          </span>
        </li>`;
    });
    const recent = this.recent.map((o) => {
      const mark = o.status === "done" ? "済" : "失敗";
      const reason = o.reason ? `: ${escapeHtml(o.reason)}` : "";
      return `<li class="${o.status}">ターン ${o.turn ?? "-"} ${mark} ${escapeHtml(o.text)}${reason}</li>`;
    });
    const total = this.items.reduce((s, o) => s + o.cost, 0);
    this.el.innerHTML = `
      <h2>命令の列</h2>
      <p>次のターンの行政力: <strong>${this.adminPower}</strong> ／ 並べた命令: ${total}</p>
      <ol class="order-list">${rows.join("") || `<li class="muted">命令はまだありません。地図でマスを選んで命令を足してください。</li>`}</ol>
      <p class="message" aria-live="polite">${escapeHtml(this.message)}</p>
      <form class="actions" data-form="rename-island">
        <label>島の名前を変える <input name="name" maxlength="32" required></label>
        <button type="submit">命令の列に足す</button>
      </form>
      <details>
        <summary>最近の命令の結果</summary>
        <ul class="recent">${recent.join("") || `<li class="muted">まだありません。</li>`}</ul>
      </details>`;
  }

  handleClick(e) {
    const button = e.target.closest("button");
    if (!button) return;
    if (button.dataset.move) {
      const i = Number(button.dataset.index);
      const j = i + Number(button.dataset.move);
      if (j < 0 || j >= this.items.length) return;
      [this.items[i], this.items[j]] = [this.items[j], this.items[i]];
      this.save("順番を変えました。");
    } else if (button.dataset.remove) {
      this.items.splice(Number(button.dataset.remove), 1);
      this.save("命令を消しました。");
    }
  }

  handleSubmit(e) {
    if (e.target.dataset.form !== "rename-island") return;
    e.preventDefault();
    const name = new FormData(e.target).get("name").trim();
    if (name) this.add("rename_island", { name });
  }
}
