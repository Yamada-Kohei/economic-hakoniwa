// 命令の列: 次のターンに上から実行する命令を並べる。行政力（1ターンに実行できる命令の数）を
// 超えた所から下は次のターンに回る。名前の変更は行政力を使わない。
// 列を変えるたびに、列を丸ごとサーバーに送って保存する。命令を足すときは、ほかの画面（別のタブの
// 財政・金融の画面など）で変えた分を消さないように、今の列をサーバーから読み直して後ろに足す。
// 島の画面では政策と金融の命令の入力欄をすべて出し、財政・金融の画面では options.forms で選んだ
// 入力欄だけを出す。

import { turnDate } from "../calendar.js";
import { escapeHtml, getJson, sendJson } from "./api.js";

// 政策の命令の入力欄の値の形（ほかの欄は数）
const TEXT_FIELDS = new Set(["good", "side", "kind", "unsold", "issue_term", "underwrite_term", "entry", "large_store"]);
const FLAG_FIELDS = new Set(["independent", "stop", "approve", "auto"]); // "true" / "false"
// 指値と値段は見せる単位で小数も書ける（W-30。サーバーが計算の単位に直して丸める）
const INT_FIELDS = ["minimum_wage", "bond_trade", "issue", "underwrite", "deposit_insurance", "turns"];
// 中身のない命令（項目を入れなくても足せる）
const EMPTY_OK = new Set(["grant_bank_license"]);

/** 予算の入力欄に添える目安（去年に使った額・いちばん使った月の年換算・使い切れない額の目安）。 */
function budgetHint(guide) {
  if (!guide) return "";
  const limit = guide.limit === "—" ? "" : `・使い切れない額の目安 ${guide.limit}`;
  const title = guide.note ? ` title="${escapeHtml(guide.note)}"` : "";
  return `<small class="budget-hint"${title}>去年 ${escapeHtml(guide.last_year)}・最大の月の年換算 ${escapeHtml(guide.peak_year)}${escapeHtml(limit)}</small>`;
}

export class OrderQueue {
  /**
   * el: 欄の要素（data-url に命令の列の API の URL）
   * options: forms（出す入力欄の命令の種類の並び。なければ島の画面の並び）、heading（見出し）
   */
  constructor(el, defs, map, options = {}) {
    this.el = el;
    this.defs = defs;
    this.map = map;
    this.url = el.dataset.url;
    this.forms = options.forms || null;
    this.heading = options.heading || "命令";
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

  /**
   * 命令を列の最後に足して保存する。保存が済むと解決する Promise を返す（結果は this.message）。
   * 今の列をサーバーから読み直して後ろに足し、足した命令が今の島で実行できなければ、そのわけも
   * this.message に入れる（列を保存するときに、島の画面と同じ確かめをサーバーがする）。
   */
  add(kind, payload) {
    const name = this.defs.orders[kind]?.name || kind;
    const cost = this.defs.orders[kind]?.admin_cost ?? 0;
    this.items.push({ kind, payload, cost, text: `${name}（保存中）` });
    this.render();
    const shown = this.items.slice(0, -1).map(({ kind, payload }) => ({ kind, payload }));
    return this.send(async () => {
      let current = shown;
      try {
        current = (await getJson(this.url)).orders.map(({ kind, payload }) => ({ kind, payload }));
      } catch {
        // 読み直せなければ、画面に出ている列の後ろに足す
      }
      return { items: [...current, { kind, payload }], done: `「${name}」を命令に追加しました。`, added: current.length };
    });
  }

  /** 画面の列（並べ替え・消した後）を保存する。 */
  save(done) {
    const items = this.items.map(({ kind, payload }) => ({ kind, payload }));
    this.render();
    return this.send(async () => ({ items, done }));
  }

  /** 列を送る。保存は1つずつ順に送る（先に送った列が後で上書きしないように）。 */
  send(prepare) {
    this.saving = this.saving
      .then(async () => {
        const { items, done, added } = await prepare();
        const res = await sendJson(this.url, "PUT", { orders: items });
        if (res.ok) {
          this.apply(res.data);
          this.message = done || "保存しました。";
          const warning = added != null ? this.warnings.get(added) : null;
          if (warning) this.message += `（現状では実行できません: ${warning}）`;
        } else if (res.data.errors) {
          this.message = res.data.errors.map((e) => `${e.index + 1} 番目: ${e.text}`).join(" ");
          await this.load();
        } else {
          this.message = res.data.error || "保存に失敗しました。";
          await this.load();
        }
        this.render();
      })
      .catch(() => {
        this.message = "保存に失敗しました。通信を確かめてください。";
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
      const when = o.turn != null ? escapeHtml(turnDate(o.turn)) : "-";
      return `<li class="${o.status}">${when} ${mark} ${escapeHtml(o.text)}${reason}</li>`;
    });
    const total = this.items.reduce((s, o) => s + o.cost, 0);
    const empty = this.forms ? "命令はありません。" : "命令はありません。地図でマスを選択して命令を追加してください。";
    this.el.innerHTML = `
      <h2>${escapeHtml(this.heading)}</h2>
      <p>次ターンの行政力（実行できる命令数）: <strong>${this.adminPower}</strong> ／ 予定の命令: ${total}</p>
      <ol class="order-list">${rows.join("") || `<li class="muted">${empty}</li>`}</ol>
      <p class="message" aria-live="polite">${escapeHtml(this.message)}</p>
      ${this.forms ? this.chosenForms() : this.islandForms()}
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

  /** 島の画面の入力欄: 国名の変更と、政策の命令・金融の命令（それぞれ開け閉めできる欄）。 */
  islandForms() {
    const finance = Object.values(this.financeForms()).join("");
    return `
      <form class="actions" data-form="rename-island">
        <label>国名の変更 <input name="name" maxlength="32" required></label>
        <button type="submit">命令に追加</button>
      </form>
      ${this.section("policy", "国の政策の命令", Object.values(this.policyForms()).join(""))}
      ${finance ? this.section("finance", "金融の命令", finance) : ""}`;
  }

  section(key, title, body) {
    if (!body) return "";
    return `
      <details data-policy="${key}" ${this.open[key] ? "open" : ""}>
        <summary>${title}</summary>
        <p class="muted">空欄の項目は変えません。欄の薄い数字が今の値です。</p>
        ${body}
      </details>`;
  }

  /** 財政・金融の画面の入力欄: options.forms で選んだ命令の入力欄だけを、その順に。 */
  chosenForms() {
    const all = { ...this.policyForms(), ...this.financeForms() };
    const body = this.forms.map((kind) => all[kind] || "").join("");
    if (!body) return "";
    return `<p class="muted">空欄の項目は変えません。欄の薄い数字が今の値です。</p>${body}`;
  }

  /**
   * 国の政策の命令（税率・予算・最低賃金と失業手当・国の倉庫の売買・公共料金の上限）の入力欄。
   * 命令の種類 → 入力欄の HTML。
   */
  policyForms() {
    const p = this.policy;
    if (!p) return {};
    const num = (name, value, attrs = "") =>
      `<input name="${name}" type="number" min="0" ${attrs} placeholder="${value}">`;
    const budget = Object.entries(p.budget)
      .map(([item, b]) => `<label>${escapeHtml(b.name)} ${num(item, b.amount, 'step="1"')}${budgetHint(b.guide)}</label>`)
      .join("");
    const goods = Object.entries(this.defs.goods)
      .filter(([g]) => g !== this.powerGood)
      .map(([g, d]) => `<option value="${g}">${escapeHtml(d.name)}（${escapeHtml(d.unit)}）</option>`)
      .join("");
    const power = this.defs.goods[this.powerGood];
    const cap = p.price_cap[this.powerGood] || 0;
    return {
      set_taxes: `
        <form class="actions" data-form="set_taxes">
          <strong>税率（%）</strong>
          <label>所得税 ${num("income_tax", p.income_tax, 'max="100" step="0.1"')}</label>
          <label>法人税 ${num("corporate_tax", p.corporate_tax, 'max="100" step="0.1"')}</label>
          <label>消費税 ${num("consumption_tax", p.consumption_tax, 'max="100" step="0.1"')}</label>
          <button type="submit">命令に追加</button>
        </form>`,
      set_budget: `
        <form class="actions" data-form="set_budget">
          <strong>年の予算</strong>${budget}
          <button type="submit">命令に追加</button>
        </form>`,
      set_public_works: `
        <form class="actions" data-form="set_public_works">
          <strong>役所の決まった事業（今: ${p.public_works_auto ? "動いている" : "止めている"}）</strong>
          <p class="hint">命令の工事がなくても、役所が公共事業費の月割りの残りで、上下水道の足りない町と、施設と町のまわりの道路の工事を1ターンに1か所分ずつ並べます。資材は国の倉庫から使わずに市場で買い、命令で並べた工事の後に進めます。止めると、命令で並べた工事と手入れだけになります。</p>
          <label><select name="auto"><option value="true">始める</option><option value="false">止める</option></select></label>
          <button type="submit">命令に追加</button>
        </form>`,
      set_labor_policy: `
        <form class="actions" data-form="set_labor_policy">
          <strong>賃金と失業手当</strong>
          <label>最低賃金（0 で決めない） ${num("minimum_wage", p.minimum_wage, 'step="1"')}</label>
          <label>失業手当（平均賃金の %） ${num("unemployment_benefit", p.unemployment_benefit, 'max="100" step="1"')}</label>
          <button type="submit">命令に追加</button>
        </form>`,
      warehouse_trade: `
        <form class="actions" data-form="warehouse_trade">
          <strong>国の倉庫の売買</strong>
          <label>資源 <select name="good">${goods}</select></label>
          <label><select name="side"><option value="sell">売る</option><option value="buy">買う</option></select></label>
          <label>量 <input name="quantity" type="number" min="0.1" step="0.1" required></label>
          <label>指値（1単位、買いは必須） <input name="limit" type="number" min="0" step="any"></label>
          <button type="submit">命令に追加</button>
        </form>`,
      set_price_cap: power ? `
        <form class="actions" data-form="set_price_cap">
          <strong>公共料金の上限</strong>
          <label>${escapeHtml(power.name)}（1${escapeHtml(power.unit)}あたり、0 で上限なし）
            <input name="price" type="number" min="0" step="any" placeholder="${cap}" required></label>
          <button type="submit">命令に追加</button>
        </form>` : "",
    };
  }

  /**
   * 金融の命令（金融政策・中央銀行・国債・銀行・資源ごとの補助金）の入力欄。命令の種類 → HTML。
   * お金の経済が始まる前の島では出さない。命令が通らない島でも、サーバーが警告を返す。
   */
  financeForms() {
    const p = this.policy;
    if (!p?.has_economy) return {};
    const num = (name, value, attrs = "") => `<input name="${name}" type="number" ${attrs} placeholder="${value}">`;
    const options = (choices, blank = "") =>
      (blank ? `<option value="">${escapeHtml(blank)}</option>` : "") +
      Object.entries(choices).map(([id, name]) => `<option value="${id}">${escapeHtml(name)}</option>`).join("");
    const independent = p.cb_independent;
    const terms = Object.fromEntries(Object.entries(p.bond_terms).map(([id, t]) => [id, t.name]));
    const forms = {};

    if (independent) {
      forms.set_monetary_policy = `<p class="hint">中央銀行が独立しているため、政策金利（今 ${p.policy_rate}%）は中央銀行が物価と失業率を見て決めます。国債の売買・直接の引き受け・銀行への緊急の貸し出しも政府は決められません。</p>`;
    } else {
      forms.set_monetary_policy = `<form class="actions" data-form="set_monetary_policy">
          <strong>金融政策</strong>
          <label>政策金利（%） ${num("policy_rate", p.policy_rate, 'min="0" max="20" step="0.05"')}</label>
          <label>中央銀行が毎ターン国債を買う額（マイナスで売る。今: ${escapeHtml(p.cb_bond_trade_text)}） ${num("bond_trade", p.cb_bond_trade, 'step="1"')}</label>
          <button type="submit">命令に追加</button>
        </form>`;
    }
    const lock = p.cb_unlock_turn != null ? `${escapeHtml(turnDate(p.cb_unlock_turn))}までは切り替えられません。` : `切り替えた後 ${p.cb_lock_turns} ターンは戻せません。`;
    forms.set_central_bank_independence = `<form class="actions" data-form="set_central_bank_independence">
        <strong>中央銀行の独立（今: ${independent ? "独立" : "政府の下"}）</strong>
        <p class="hint">${independent ? "政府の下に戻すと、政策金利と国債の売買を命令で決められます。" : "独立させると、中央銀行が物価と失業率を見て政策金利を決め、国債の売買と売れ残りの引き受けをやめます。"}${lock}</p>
        <input type="hidden" name="independent" value="${independent ? "false" : "true"}">
        <button type="submit">${independent ? "政府の下に戻す" : "独立させる"}（命令に追加）</button>
      </form>`;

    const unsold = Object.fromEntries(Object.entries(p.bond_unsold_choices).filter(([id]) => !(independent && id === "underwrite")));
    const shares = Object.entries(p.bond_terms)
      .map(([id, t]) => `<label>${escapeHtml(t.name)} ${num(`term:${id}`, t.share, 'min="0" step="1"')}</label>`)
      .join("");
    forms.set_bond_policy = `<form class="actions" data-form="set_bond_policy">
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
      </form>`;
    forms.set_bond_payment = `<form class="actions" data-form="set_bond_payment">
        <strong>国債の利払いと償還</strong>
        ${p.default_turn != null ? `<p class="warning">${escapeHtml(turnDate(p.default_turn))}に債務不履行になりました。${p.bonds_closed_until != null ? `${escapeHtml(turnDate(p.bonds_closed_until))}までは銀行が国債を買わず、入札では売れません。` : ""}</p>` : ""}
        <p class="hint">止めると、そのターンにすべての国債が債務不履行になります。額面が切り下がって持っている銀行が損をし、国債の利回りに ${p.default_premium}% が上乗せされます（${p.default_decay} ターンかけて減ります）。${p.default_exclusion} ターンは銀行が国債を買わず、入札では売れません（売れ残りは中央銀行が引き受けるか、その分の支出を減らします）。損をした銀行は ${p.default_crunch} ターン新しい融資を止めるので、民営工場の参入と新しい運転資金の借り入れが止まります。</p>
        <label><select name="stop"><option value="true">止める（債務不履行）</option><option value="false">止める命令を取り消す</option></select></label>
        <button type="submit">命令に追加</button>
      </form>`;

    const entryNow = p.bank_entry || p.bank_entry_default;
    const entryName = `${p.bank_entry_choices[entryNow] || entryNow}${p.bank_entry ? "" : "・経済体制の既定"}`;
    const entryChoices = p.planned ? { closed: p.bank_entry_choices.closed } : p.bank_entry_choices;
    forms.set_bank_policy = `<form class="actions" data-form="set_bank_policy">
        <strong>銀行</strong>
        <label>民間銀行の参入（今: ${escapeHtml(entryName)}） <select name="entry">${options(entryChoices, "変えない")}</select></label>
        <label>預金保護の上限（1人あたり、-1 で無制限。今: ${escapeHtml(p.deposit_insurance_text)}） ${num("deposit_insurance", p.deposit_insurance, 'min="-1" step="1"')}</label>
        <button type="submit">命令に追加</button>
      </form>`;
    if (!p.planned && entryNow === "license") {
      forms.grant_bank_license = `<form class="actions" data-form="grant_bank_license">
          <strong>民間銀行の免許</strong>
          <p class="hint">${p.bank_license ? "免許を出して、開業を待っています。" : "免許を出すと、空いたオフィス街のマスがあれば民間銀行が開業します。"}</p>
          <button type="submit">免許を出す（命令に追加）</button>
        </form>`;
    }

    // 小売りの政策（回7）: 百貨店の出店の規制と、許可制のときの申請を許す
    const app = p.store_application;
    const waiting = app && !app.approved
      ? `<label><input type="checkbox" name="approve" value="true"> ${escapeHtml(turnDate(app.turn))}に出た申請（(${app.x}, ${app.y}) の${escapeHtml(app.name)}）を許す</label>`
      : app?.approved ? `<p class="hint">(${app.x}, ${app.y}) の${escapeHtml(app.name)}の申請を許しました。企業が建てるのを待っています。</p>` : "";
    forms.set_retail_policy = `<form class="actions" data-form="set_retail_policy">
        <strong>小売り（大きな店の出店）</strong>
        <p class="hint">百貨店は商業地のマスに企業が採算を見て建てます。許可制にすると企業が申請を出し、許すまで建ちません。禁止すると建ちません（町の商店はどれでも建ちます）。</p>
        <label>百貨店の出店（今: ${escapeHtml(p.large_store_choices[p.large_store] || p.large_store)}） <select name="large_store">${options(p.large_store_choices, "変えない")}</select></label>
        ${waiting}
        <button type="submit">命令に追加</button>
      </form>`;

    const goods = Object.entries(this.defs.goods)
      .map(([g, d]) => `<option value="${g}">${escapeHtml(d.name)}</option>`)
      .join("");
    const current = p.subsidies
      .map((s, i) => `<li>${escapeHtml(s.text)} <button type="button" data-stop-subsidy="${i}">やめる（命令に追加）</button></li>`)
      .join("");
    forms.set_subsidy = `<form class="actions" data-form="set_subsidy">
        <strong>資源ごとの補助金</strong>
        <p class="hint">生産補助はその資源を作る施設に売上の割合を、設備投資補助はその資源を作る民営工場が新しく建つときに建設費の割合を、補助金の予算から出します。施設ごとの補助金は、地図で施設を選んで出します。</p>
        ${current ? `<ul>${current}</ul>` : ""}
        <label>種類 <select name="kind">${options(p.subsidy_kinds)}</select></label>
        <label>資源 <select name="good">${goods}</select></label>
        <label>割合（%、0 でやめる） <input name="rate" type="number" min="0" max="100" step="0.1" required></label>
        <label>期間（ターン） <input name="turns" type="number" min="1" max="1200" step="1" value="12" required></label>
        <button type="submit">命令に追加</button>
      </form>`;

    return forms;
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
