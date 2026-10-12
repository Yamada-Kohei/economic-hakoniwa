// 命令の列: 次のターンに上から実行する命令を並べる。行政力（1ターンに実行できる命令の数）を
// 超えた所から下は次のターンに回る。名前の変更は行政力を使わない。
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
    // 政策の欄を開いたままにする（列を描き直しても閉じないように）
    el.addEventListener("toggle", (e) => {
      if (e.target.dataset?.policy) this.policyOpen = e.target.open;
    }, true);
    this.policy = null;
    this.powerGood = "";
    this.policyOpen = false;
    this.load();
  }

  async load() {
    try {
      this.apply(await getJson(this.url));
    } catch {
      this.message = "命令一覧を取得できませんでした。";
    }
    this.render();
  }

  apply(data) {
    this.policy = data.policy || this.policy;
    this.powerGood = data.power_good || this.powerGood;
    this.items = data.orders.map((o) => ({ kind: o.kind, payload: o.payload, cost: o.cost, text: o.text }));
    this.recent = data.recent || this.recent;
    this.adminPower = data.admin_power;
    this.warnings = new Map((data.warnings || []).map((w) => [w.index, w.text]));
  }

  /** 命令を列の最後に足して保存する。 */
  add(kind, payload) {
    const cost = this.defs.orders[kind]?.admin_cost ?? 0;
    this.items.push({ kind, payload, cost, text: `${this.defs.orders[kind]?.name || kind}（保存中）` });
    this.save(`「${this.defs.orders[kind]?.name || kind}」を命令に追加しました。`);
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
        this.message = res.data.error || "保存に失敗しました。";
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
      const divider = late && !cut ? `<li class="divider">以下は行政力が不足するため、次のターン以降に実行されます</li>` : "";
      if (late) cut = true;
      const warning = this.warnings.get(i);
      return `${divider}
        <li class="${late ? "late" : ""}">
          <span class="order-text">${i + 1}. ${escapeHtml(o.text)} <small>（行政力 ${o.cost}）</small></span>
          ${warning ? `<span class="warning">現状では実行できません: ${escapeHtml(warning)}</span>` : ""}
          <span class="order-buttons">
            <button type="button" data-move="-1" data-index="${i}" aria-label="上へ"${i === 0 ? " disabled" : ""}>↑</button>
            <button type="button" data-move="1" data-index="${i}" aria-label="下へ"${i === this.items.length - 1 ? " disabled" : ""}>↓</button>
            <button type="button" data-remove="${i}" aria-label="消す">×</button>
          </span>
        </li>`;
    });
    const recent = this.recent.map((o) => {
      const mark = o.status === "done" ? "実行" : "失敗";
      const reason = o.reason ? `: ${escapeHtml(o.reason)}` : "";
      return `<li class="${o.status}">ターン ${o.turn ?? "-"} ${mark} ${escapeHtml(o.text)}${reason}</li>`;
    });
    const total = this.items.reduce((s, o) => s + o.cost, 0);
    this.el.innerHTML = `
      <h2>命令</h2>
      <p>次ターンの行政力（実行できる命令数）: <strong>${this.adminPower}</strong> ／ 予定の命令: ${total}</p>
      <ol class="order-list">${rows.join("") || `<li class="muted">命令はありません。地図でマスを選択して命令を追加してください。</li>`}</ol>
      <p class="message" aria-live="polite">${escapeHtml(this.message)}</p>
      <form class="actions" data-form="rename-island">
        <label>国名の変更 <input name="name" maxlength="32" required></label>
        <button type="submit">命令に追加</button>
      </form>
      ${this.policyForms()}
      <details>
        <summary>最近の命令の結果</summary>
        <ul class="recent">${recent.join("") || `<li class="muted">なし</li>`}</ul>
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

  /** 国の政策の命令（税率・予算・最低賃金と失業手当・国の倉庫の売買・公共料金の上限）の入力欄 */
  policyForms() {
    const p = this.policy;
    if (!p) return "";
    const num = (name, value, attrs = "") =>
      `<input name="${name}" type="number" min="0" ${attrs} placeholder="${value}">`;
    const budget = Object.entries(p.budget)
      .map(([item, b]) => `<label>${escapeHtml(b.name)} ${num(item, b.amount, 'step="1"')}</label>`)
      .join("");
    const goods = Object.entries(this.defs.goods)
      .filter(([g]) => g !== this.powerGood)
      .map(([g, d]) => `<option value="${g}">${escapeHtml(d.name)}（${escapeHtml(d.unit)}）</option>`)
      .join("");
    const power = this.defs.goods[this.powerGood];
    const cap = p.price_cap[this.powerGood] || 0;
    return `
      <details data-policy="1" ${this.policyOpen ? "open" : ""}>
        <summary>国の政策の命令</summary>
        <p class="muted">空欄の項目は変えません。欄の薄い数字が今の値です。</p>
        <form class="actions" data-form="set_taxes">
          <strong>税率（%）</strong>
          <label>所得税 ${num("income_tax", p.income_tax, 'max="100" step="0.1"')}</label>
          <label>法人税 ${num("corporate_tax", p.corporate_tax, 'max="100" step="0.1"')}</label>
          <label>消費税 ${num("consumption_tax", p.consumption_tax, 'max="100" step="0.1"')}</label>
          <button type="submit">命令に追加</button>
        </form>
        <form class="actions" data-form="set_budget">
          <strong>年の予算</strong>${budget}
          <button type="submit">命令に追加</button>
        </form>
        <form class="actions" data-form="set_labor_policy">
          <strong>賃金と失業手当</strong>
          <label>最低賃金（0 で決めない） ${num("minimum_wage", p.minimum_wage, 'step="1"')}</label>
          <label>失業手当（平均賃金の %） ${num("unemployment_benefit", p.unemployment_benefit, 'max="100" step="1"')}</label>
          <button type="submit">命令に追加</button>
        </form>
        <form class="actions" data-form="warehouse_trade">
          <strong>国の倉庫の売買</strong>
          <label>資源 <select name="good">${goods}</select></label>
          <label><select name="side"><option value="sell">売る</option><option value="buy">買う</option></select></label>
          <label>量 <input name="quantity" type="number" min="0.1" step="0.1" required></label>
          <label>指値（1単位、買いは必須） <input name="limit" type="number" min="0" step="1"></label>
          <button type="submit">命令に追加</button>
        </form>
        ${power ? `<form class="actions" data-form="set_price_cap">
          <strong>公共料金の上限</strong>
          <label>${escapeHtml(power.name)}（1${escapeHtml(power.unit)}あたり、0 で上限なし）
            <input name="price" type="number" min="0" step="1" placeholder="${cap}" required></label>
          <button type="submit">命令に追加</button>
        </form>` : ""}
      </details>`;
  }

  handleSubmit(e) {
    const kind = e.target.dataset.form;
    if (!kind) return;
    e.preventDefault();
    const data = new FormData(e.target);
    if (kind === "rename-island") {
      const name = data.get("name").trim();
      if (name) this.add("rename_island", { name });
      return;
    }
    const payload = {};
    for (const [key, value] of data.entries()) {
      if (value === "") continue;
      payload[key] = ["good", "side"].includes(key) ? value : Number(value);
    }
    if (kind === "set_price_cap") payload.good = this.powerGood;
    if (kind === "warehouse_trade" && payload.side === "buy" && !payload.limit) {
      this.message = "買うときは指値を入れてください。";
      this.render();
      return;
    }
    if (Object.keys(payload).length === 0) {
      this.message = "変える項目を入れてください。";
      this.render();
      return;
    }
    for (const key of ["minimum_wage", "limit", "price", ...Object.keys(this.policy?.budget || {})]) {
      if (key in payload) payload[key] = Math.round(payload[key]);
    }
    this.add(kind, payload);
  }
}
