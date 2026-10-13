// 命令の列: 次のターンに上から実行する命令を並べる。行政力（1ターンに実行できる命令の数）を
// 超えた所から下は次のターンに回る。名前の変更は行政力を使わない。
// 列を変えるたびに、列を丸ごとサーバーに送って保存する。

import { escapeHtml, getJson, sendJson } from "./api.js";

// 政策の命令の入力欄の値の形（ほかの欄は数）
const TEXT_FIELDS = new Set(["good", "side", "kind", "unsold", "issue_term", "underwrite_term", "entry"]);
const FLAG_FIELDS = new Set(["independent", "stop"]); // "true" / "false"
const INT_FIELDS = ["minimum_wage", "limit", "price", "bond_trade", "issue", "underwrite", "deposit_insurance", "turns"];
// 中身のない命令（項目を入れなくても足せる）
const EMPTY_OK = new Set(["grant_bank_license"]);

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
      const key = e.target.dataset?.policy;
      if (key) this.open[key] = e.target.open;
    }, true);
    this.policy = null;
    this.powerGood = "";
    this.open = {}; // 欄（data-policy）→ 開いているか
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

  /** 命令を列の最後に足して保存する。保存が済むと解決する Promise を返す（結果は this.message）。 */
  add(kind, payload) {
    const cost = this.defs.orders[kind]?.admin_cost ?? 0;
    this.items.push({ kind, payload, cost, text: `${this.defs.orders[kind]?.name || kind}（保存中）` });
    return this.save(`「${this.defs.orders[kind]?.name || kind}」を命令に追加しました。`);
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
    return this.saving;
  }

  render() {
    let used = 0;
    let cut = false;
    const rows = this.items.map((o, i) => {
      // 1ターンの行政力を超える命令も、先頭ならそのターンの行政力をすべて使って実行される
      const cost = used === 0 ? Math.min(o.cost, this.adminPower) : o.cost;
      used += cost;
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
      ${this.financeForms()}
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
    } else if (button.dataset.stopSubsidy) {
      const s = this.policy?.subsidies?.[Number(button.dataset.stopSubsidy)];
      if (s) this.add("set_subsidy", s.stop);
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
      <details data-policy="policy" ${this.open.policy ? "open" : ""}>
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

  /**
   * 金融の命令（金融政策・中央銀行・国債・銀行・資源ごとの補助金）の入力欄。
   * お金の経済が始まる前の島では出さない。命令が通らない島でも、サーバーが警告を返す。
   */
  financeForms() {
    const p = this.policy;
    if (!p?.has_economy) return "";
    const num = (name, value, attrs = "") => `<input name="${name}" type="number" ${attrs} placeholder="${value}">`;
    const options = (choices, blank = "") =>
      (blank ? `<option value="">${escapeHtml(blank)}</option>` : "") +
      Object.entries(choices).map(([id, name]) => `<option value="${id}">${escapeHtml(name)}</option>`).join("");
    const independent = p.cb_independent;
    const terms = Object.fromEntries(Object.entries(p.bond_terms).map(([id, t]) => [id, t.name]));
    const forms = [];

    if (independent) {
      forms.push(`<p class="hint">中央銀行が独立しているため、政策金利（今 ${p.policy_rate}%）は中央銀行が物価と失業率を見て決めます。国債の売買・直接の引き受け・銀行への緊急の貸し出しも政府は決められません。</p>`);
    } else {
      forms.push(`<form class="actions" data-form="set_monetary_policy">
          <strong>金融政策</strong>
          <label>政策金利（%） ${num("policy_rate", p.policy_rate, 'min="0" max="20" step="0.05"')}</label>
          <label>中央銀行が毎ターン国債を買う額（マイナスで売る。今: ${escapeHtml(p.cb_bond_trade_text)}） ${num("bond_trade", p.cb_bond_trade, 'step="1"')}</label>
          <button type="submit">命令に追加</button>
        </form>`);
    }
    const lock = p.cb_unlock_turn != null ? `ターン ${p.cb_unlock_turn} までは切り替えられません。` : `切り替えた後 ${p.cb_lock_turns} ターンは戻せません。`;
    forms.push(`<form class="actions" data-form="set_central_bank_independence">
        <strong>中央銀行の独立（今: ${independent ? "独立" : "政府の下"}）</strong>
        <p class="hint">${independent ? "政府の下に戻すと、政策金利と国債の売買を命令で決められます。" : "独立させると、中央銀行が物価と失業率を見て政策金利を決め、国債の売買と売れ残りの引き受けをやめます。"}${lock}</p>
        <input type="hidden" name="independent" value="${independent ? "false" : "true"}">
        <button type="submit">${independent ? "政府の下に戻す" : "独立させる"}（命令に追加）</button>
      </form>`);

    const unsold = Object.fromEntries(Object.entries(p.bond_unsold_choices).filter(([id]) => !(independent && id === "underwrite")));
    const shares = Object.entries(p.bond_terms)
      .map(([id, t]) => `<label>${escapeHtml(t.name)} ${num(`term:${id}`, t.share, 'min="0" step="1"')}</label>`)
      .join("");
    forms.push(`<form class="actions" data-form="set_bond_policy">
        <strong>国債（${p.bonds ? `${p.bonds} 銘柄` : "まだ発行していません"}）</strong>
        <p class="hint">国庫が足りなくなる分は、毎ターン入札で発行して銀行に買ってもらいます。</p>
        <label>入札の売れ残り（今: ${escapeHtml(p.bond_unsold_choices[p.bond_unsold] || p.bond_unsold)}） <select name="unsold">${options(unsold, "変えない")}</select></label>
        <p class="hint">自動で発行する期間の割合（%。入れた期間だけで割合を決め直します）</p>
        ${shares}
        <label>このターンに追加で入札に出す額 ${num("issue", "", 'min="0" step="1"')}</label>
        <label>その期間 <select name="issue_term">${options(terms)}</select></label>
        ${independent ? "" : `<label>このターンに中央銀行に直接引き受けさせる額 ${num("underwrite", "", 'min="0" step="1"')}</label>
        <label>その期間 <select name="underwrite_term">${options(terms)}</select></label>`}
        <button type="submit">命令に追加</button>
      </form>`);
    forms.push(`<form class="actions" data-form="set_bond_payment">
        <strong>国債の利払いと償還</strong>
        ${p.default_turn != null ? `<p class="warning">ターン ${p.default_turn} に債務不履行になりました。</p>` : ""}
        <p class="hint">止めると、そのターンにすべての国債が債務不履行になります（額面が切り下がって持っている銀行が損をし、国債の利回りがしばらく跳ね上がります）。</p>
        <label><select name="stop"><option value="true">止める（債務不履行）</option><option value="false">止める命令を取り消す</option></select></label>
        <button type="submit">命令に追加</button>
      </form>`);

    const entryNow = p.bank_entry || p.bank_entry_default;
    const entryName = `${p.bank_entry_choices[entryNow] || entryNow}${p.bank_entry ? "" : "・経済体制の既定"}`;
    const entryChoices = p.planned ? { closed: p.bank_entry_choices.closed } : p.bank_entry_choices;
    forms.push(`<form class="actions" data-form="set_bank_policy">
        <strong>銀行</strong>
        <label>民間銀行の参入（今: ${escapeHtml(entryName)}） <select name="entry">${options(entryChoices, "変えない")}</select></label>
        <label>預金保護の上限（1人あたり、-1 で無制限。今: ${escapeHtml(p.deposit_insurance_text)}） ${num("deposit_insurance", p.deposit_insurance, 'min="-1" step="1"')}</label>
        <button type="submit">命令に追加</button>
      </form>`);
    if (!p.planned && entryNow === "license") {
      forms.push(`<form class="actions" data-form="grant_bank_license">
          <strong>民間銀行の免許</strong>
          <p class="hint">${p.bank_license ? "免許を出して、開業を待っています。" : "免許を出すと、空いたオフィス街のマスがあれば民間銀行が開業します。"}</p>
          <button type="submit">免許を出す（命令に追加）</button>
        </form>`);
    }

    const goods = Object.entries(this.defs.goods)
      .map(([g, d]) => `<option value="${g}">${escapeHtml(d.name)}</option>`)
      .join("");
    const current = p.subsidies
      .map((s, i) => `<li>${escapeHtml(s.text)} <button type="button" data-stop-subsidy="${i}">やめる（命令に追加）</button></li>`)
      .join("");
    forms.push(`<form class="actions" data-form="set_subsidy">
        <strong>資源ごとの補助金</strong>
        <p class="hint">生産補助はその資源を作る施設に売上の割合を、設備投資補助はその資源を作る民営工場が新しく建つときに建設費の割合を、補助金の予算から出します。施設ごとの補助金は、地図で施設を選んで出します。</p>
        ${current ? `<ul>${current}</ul>` : ""}
        <label>種類 <select name="kind">${options(p.subsidy_kinds)}</select></label>
        <label>資源 <select name="good">${goods}</select></label>
        <label>割合（%、0 でやめる） <input name="rate" type="number" min="0" max="100" step="0.1" required></label>
        <label>期間（ターン） <input name="turns" type="number" min="1" max="1200" step="1" value="12" required></label>
        <button type="submit">命令に追加</button>
      </form>`);

    return `
      <details data-policy="finance" ${this.open.finance ? "open" : ""}>
        <summary>金融の命令</summary>
        <p class="muted">空欄の項目は変えません。欄の薄い数字が今の値です。</p>
        ${forms.join("")}
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
      if (key.startsWith("term:")) {
        // 国債の期間ごとの割合はまとめて terms に入れる
        payload.terms = { ...payload.terms, [key.slice(5)]: Number(value) };
      } else if (TEXT_FIELDS.has(key)) {
        payload[key] = value;
      } else if (FLAG_FIELDS.has(key)) {
        payload[key] = value === "true";
      } else {
        payload[key] = Number(value);
      }
    }
    if (kind === "set_price_cap") payload.good = this.powerGood;
    if (kind === "set_bond_policy") {
      // 期間は額を入れたときだけ送る
      for (const key of ["issue", "underwrite"]) {
        if (!payload[key]) {
          delete payload[key];
          delete payload[`${key}_term`];
        }
      }
      if (payload.terms && !Object.values(payload.terms).some((v) => v > 0)) {
        this.message = "期間の割合は、どれか1つを 0 より大きくしてください。";
        this.render();
        return;
      }
    }
    if (kind === "warehouse_trade" && payload.side === "buy" && !payload.limit) {
      this.message = "買うときは指値を入れてください。";
      this.render();
      return;
    }
    if (Object.keys(payload).length === 0 && !EMPTY_OK.has(kind)) {
      this.message = "変える項目を入れてください。";
      this.render();
      return;
    }
    if (kind === "set_bond_payment" && payload.stop) {
      const ok = confirm("国債の利払いと償還を止めると、そのターンにすべての国債が債務不履行になります。命令に追加しますか？");
      if (!ok) return;
    }
    for (const key of [...INT_FIELDS, ...Object.keys(this.policy?.budget || {})]) {
      if (key in payload) payload[key] = Math.round(payload[key]);
    }
    this.add(kind, payload);
  }
}
