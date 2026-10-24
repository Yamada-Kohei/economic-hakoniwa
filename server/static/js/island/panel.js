// マスの情報の欄: 選んだマスと、そこにある施設の詳しい情報を出す（インフラの整備度と並んでいる工事、
// 施設の技術者、学校と大学の働きを含む）。
// 島の持ち主には、そのマスへの命令（建設・撤去・名称・地名・優先度・会計方式・国有化と民営化・
// 補助金・銀行への緊急の貸し出しと破綻処理）と、地図でマスを選んで出す命令（インフラの工事・送電線・
// 開発禁止区域・工業団地とオフィス街）の入口も出す。
// 欄は押したマスのそばに出す小さな窓の中にある（狭い画面では下から出す板。窓の開け閉めと位置は
// map/main.js）。地図でマスを選んで出す命令の入口と選んでいる途中の欄は、窓の外の地図の上（tools）に
// 出し、窓を閉じても続けられるようにする。命令を足した結果は地図のそばに出す。

import { turnDate } from "../calendar.js";
import { escapeHtml, getJson, sendJson } from "./api.js";
import { MilitaryTile } from "./military.js";
import { words } from "../military/common.js";
import { recipeLabel, recipeWords } from "./recipes.js";

const MAX_TILES = 64; // 1つの命令で選べるマスの数（orders.yaml の maxItems と同じ）

// 会計方式を変えたときに起きること（変えた先の会計方式ごと）
const ACCOUNTING_HINTS = {
  self_supporting: "政府が運転資金を出資し、施設が自分の預金で売上と費用を出し入れします。利益から法人税と国庫への納付を払います。",
  treasury: "施設の預金は国庫に戻り、売上も費用も国庫で出し入れします。",
};

// 国有化・民営化で起きること
const OWNERSHIP_HINTS = {
  nationalize: "純資産（資産 − 借入）の額で政府が買い取り、借入も引き継ぎます。差し押さえ中の施設は、銀行から売り値で買います。国庫が足りなければ実行できません。",
  privatize: "純資産の額で資本家階級に売ります。国庫直轄の施設は、先に運転資金を出資して独立採算にします。",
};

// 施設ごとの補助金の種類
const SUBSIDY_KINDS = {
  production: { name: "生産補助", hint: "売上の割合を、補助金の予算から毎ターン出します。" },
  bailout: { name: "救済補助", hint: "赤字の分を、補助金の予算から毎ターン埋めます（割合は 0 でやめる、ほかの値はどれも同じです）。" },
};

// 破綻した銀行の処理（hakoniwa_core/modules/failures）
const RESOLUTION_HINTS = {
  bailout: "政府が自己資本を規制の値まで（資金繰りの破綻なら足りない準備預金も）注入し、国営銀行として残します。",
  transfer: "預金・融資・国債・銀行間の貸し借りを、健全な銀行のうち預金の最も多い銀行に移します。損失は保護されない預金と、この銀行に貸していた銀行が同じ割合で負い、足りない分を政府が払います（預金保険）。",
  liquidation: "融資を額面より安く健全な銀行に売ったとして損失を数え、引き継ぎと同じく移します。損失は引き継ぎより大きくなります。",
};

// 地図でマスを選んで出す命令（infrastructure は、選んだインフラの種類 kind も送る）
const MARK_MODES = {
  infrastructure: { label: "インフラの工事", kind: "build_infrastructure", payload: (tiles, kind) => ({ kind, tiles }) },
  power_line: { label: "送電線の敷設", kind: "build_power_line", payload: (tiles) => ({ tiles }) },
  no_dev_on: { label: "開発禁止区域の指定", kind: "set_no_development", payload: (tiles) => ({ tiles, value: true }) },
  no_dev_off: { label: "開発禁止区域の解除", kind: "set_no_development", payload: (tiles) => ({ tiles, value: false }) },
  zone_industrial: { label: "工業団地の指定", kind: "set_zone", payload: (tiles) => ({ zone: "industrial", tiles }) },
  zone_office: { label: "オフィス街の指定", kind: "set_zone", payload: (tiles) => ({ zone: "office", tiles }) },
  zone_commercial: { label: "商業地の指定", kind: "set_zone", payload: (tiles) => ({ zone: "commercial", tiles }) },
  zone_none: { label: "区域の指定の解除", kind: "set_zone", payload: (tiles) => ({ zone: "none", tiles }) },
};

export class TilePanel {
  /**
   * el: 欄の要素（data-tile-url に「…/tiles/0,0」、data-check-url に命令の確認の URL、
   *     data-resources に島の地下資源、data-producible に島で生産できる資源。どちらもカンマ区切り）
   * options: orders（命令の列。持ち主でなければ null）、marked（印を付けたマスの Set）、onMarkedChange、
   *          tools（地図でマスを選んで出す命令の入口を出す、窓の外の要素）、
   *          notice（命令を足した結果を出す、地図のそばの要素）、
   *          onInfraKind（インフラの工事のマスを選び始めたときと、種類を変えたときに種類を渡す）
   */
  constructor(el, defs, map, options = {}) {
    this.el = el;
    this.defs = defs;
    this.map = map;
    this.resources = idSet(el.dataset.resources);
    this.producible = el.dataset.producible == null ? null : idSet(el.dataset.producible);
    this.orders = options.orders || null;
    this.marked = options.marked || new Set();
    this.onMarkedChange = options.onMarkedChange || (() => {});
    this.tools = options.tools || null;
    this.notice = options.notice || null;
    this.onInfraKind = options.onInfraKind || (() => {});
    this.infraKind = Object.keys(defs.infrastructure || {})[0] || null;
    this.mode = null;
    this.tile = null;
    this.info = null;
    this.request = 0;
    this.military = this.orders && el.dataset.militaryUrl ? new MilitaryTile(this) : null;
    for (const target of [el, this.tools]) {
      target?.addEventListener("click", (e) => this.handleClick(e));
      target?.addEventListener("change", (e) => this.handleChange(e));
    }
    this.render();
  }

  /** 地図でマスを選んでいる途中か。 */
  get marking() {
    return this.mode !== null || this.military?.picking != null;
  }

  async show(tile) {
    this.tile = tile;
    this.info = null;
    if (this.notice) this.notice.textContent = "";
    this.el.scrollTop = 0; // 新しいマスは窓の上の端から見せる
    this.render(); // 読み込む間は「読み込み中…」（前のマスの中身や命令の欄を残さない）
    if (!tile) return;
    const request = ++this.request;
    this.el.setAttribute("aria-busy", "true");
    try {
      const [info] = await Promise.all([
        getJson(this.el.dataset.tileUrl.replace(/0,0$/, `${tile.x},${tile.y}`)),
        this.military?.load(),
      ]);
      if (request !== this.request) return; // 待つ間に別のマスが選ばれた
      this.info = info;
    } catch {
      if (request !== this.request) return;
      this.info = { error: true };
    } finally {
      this.el.removeAttribute("aria-busy");
    }
    this.render();
  }

  toggleMark(tile) {
    if (this.military?.picking != null) {
      this.military.choose(tile);
      return;
    }
    const key = `${tile.x},${tile.y}`;
    if (this.marked.has(key)) this.marked.delete(key);
    else if (this.marked.size < MAX_TILES) this.marked.add(key);
    this.onMarkedChange();
    this.render();
  }

  // --- 表示 -----------------------------------------------------------------

  render() {
    if (this.tools) {
      // 中身が変わったときだけ書き換える（押したボタンのフォーカスと読み上げを保つ）
      const tools = this.military?.picking != null ? this.military.pickingHtml()
        : this.orders ? (this.mode ? this.markingHtml() : this.markButtonsHtml()) : "";
      if (tools !== this.toolsHtml) this.tools.innerHTML = this.toolsHtml = tools;
    }
    const parts = [];
    if (!this.tile) {
      parts.push(`<p class="muted">マスを選択すると、ここに詳細が表示されます。</p>`);
    } else if (!this.info) {
      parts.push(`<p class="muted">読み込み中…</p>`);
    } else if (this.info.error) {
      parts.push(`<p class="error">マスの情報を取得できませんでした。</p>`);
    } else {
      parts.push(this.tileHtml(this.info));
      const index = this.tile.y * this.map.size + this.tile.x;
      const w = words();
      if (this.map.fallout?.[index] > 0) parts.push(`<p class="military-warning">${escapeHtml(w.fallout)}: ${this.map.fallout[index]}</p>`);
      const occupier = this.map.occupied?.[index];
      if (occupier) parts.push(`<p class="military-warning">${escapeHtml(occupier === -1 ? w.rebelHeld : `${w.occupied}: ${occupier}`)}</p>`);
      if (this.military) parts.push(this.military.html(this.tile));
      if (this.info.facility) parts.push(this.facilityHtml(this.info.facility));
      if (this.orders) parts.push(this.actionsHtml(this.info));
    }
    this.el.innerHTML = parts.join("");
    if (this.orders && this.info && !this.info.facility) this.checkBuild();
  }

  tileHtml(t) {
    const rows = [["地形", t.terrain_name]];
    if (t.place) rows.unshift(["地名", t.place]);
    if (t.capital) rows.unshift(["首都", t.capital_name || "（名称なし）"]);
    if (t.population) rows.push(["人口", `${t.population.toLocaleString()} 人`]);
    if (t.employed) rows.push(["就業者", `${t.employed.toLocaleString()} 人`]);
    if (t.happiness != null && t.population) rows.push(["幸福度", Math.round(t.happiness)]);
    if (t.power != null && t.population) rows.push(["電力充足率", `${Math.round(t.power / 10)}%`]);
    if (t.pollution) rows.push(["汚染度", t.pollution]);
    if (t.fertility != null) rows.push(["肥沃度", t.fertility.toFixed(2)]);
    for (const i of t.infra || []) {
      const keep = i.keep > i.level ? `（維持の目標 ${i.keep}%）` : "";
      const grid = this.defs.infrastructure?.[i.kind]?.role === "power" ? (t.power_line ? "・電力を通します" : "・電力を通すには整備度が足りません") : "";
      rows.push([`${i.name}の整備度`, `${i.level}%${keep}${grid}`]);
    }
    if (t.power_line && !t.infra?.length) rows.push(["送電線", "あり"]);
    if (t.works?.length) {
      const list = t.works.map((w) => `${w.name}（${w.position} 番目）`).join("、");
      rows.push(["インフラの工事", `${list} ／ 列は全 ${t.works_total} マス`]);
      if (t.works_why) rows.push(["工事の状況", t.works_why]);
    }
    if (t.zone === "no_development") rows.push(["開発", "禁止区域（町が形成されません）"]);
    if (t.zone === "industrial") rows.push(["区域", "工業団地（民営の工場が採算を見て建ちます）"]);
    if (t.zone === "office") rows.push(["区域", "オフィス街（銀行が建ちます）"]);
    if (t.zone === "commercial") rows.push(["区域", "商業地（企業が採算を見て百貨店を建てます）"]);
    if (t.capital_buildings?.length) rows.push(["首都の建物", t.capital_buildings.join("・")]);
    const title = t.capital ? `首都 ${t.capital_name || ""}（${t.x}, ${t.y}）` : `(${t.x}, ${t.y})`;
    return `<h3>${escapeHtml(title)}</h3>${table(rows)}`;
  }

  facilityHtml(f) {
    if (f.level_name) {
      // 町（住宅地）には名前を付けない。段階と、次の段階に育つのに要る資源を出す
      const rows = [["段階", f.level_name], ["居住上限", `${f.capacity.toLocaleString()} 人`]];
      // 町の商店（回7）: 町の段階ごとに建てられる数が決まる
      if (f.shop_slots != null) rows.push(["商店", `${(f.shops || 0).toLocaleString()} / ${f.shop_slots.toLocaleString()} 軒`]);
      if (f.next_level_name) {
        const need = f.next_materials?.length ? goodsText(f.next_materials) : "なし";
        rows.push([`${f.next_level_name}への発展に必要な資源`, need]);
      }
      return `<h3>${escapeHtml(f.level_name)}</h3>${table(rows)}`;
    }
    const rows = [["種類", f.kind_name], ["所有", `${f.owner_name}・${f.accounting_name}`]];
    if (f.firm_name) rows.push(["企業", f.firm_name]);
    if (f.recipe_name) rows.push([recipeWords().method, recipeLabel(f.recipe,
      this.defs.facilities[f.kind]?.recipes?.[f.recipe], f.recipe_name)]);
    if (!f.built) {
      if (f.private_site) rows.push(["建設", "民間の資本が自分の資金で建設中"]);
      rows.push(["建設進捗", `${Math.round(f.progress * 100)}%（工期 ${f.turns} ターン）`]);
      rows.push(["建設労働者", `${f.workers.toLocaleString()} / ${f.workers_needed.toLocaleString()} 人`]);
      if (f.materials.length) rows.push(["建設資材", goodsText(f.materials)]);
    } else if (f.workers_needed != null && !f.monument) {
      rows.push(["従業者", `${f.workers.toLocaleString()} / ${f.workers_needed.toLocaleString()} 人`]);
      rows.push(["稼働率", `${Math.round(f.operating_rate * 100)}%`]);
      // 設備の傷み（回6）: 傷んだ分は作れない
      if (f.equipment != null) rows.push(["設備", `${Math.round(f.equipment * 100)}%${f.equipment < 1 ? "（傷んだ分は作れません）" : ""}`]);
      if (f.wage != null) rows.push(["賃金（1人1ターン）", f.wage.toLocaleString()]);
      if (f.engineers_needed) {
        const seats = f.engineers_wanted !== f.engineers_needed ? `（技術者の席 ${f.engineers_wanted.toLocaleString()}）` : "";
        rows.push(["うち技術者", `${f.engineers.toLocaleString()} / ${f.engineers_needed.toLocaleString()} 人${seats}`]);
      }
      if (f.engineer_wage != null) rows.push(["技術者の賃金（1人1ターン）", f.engineer_wage.toLocaleString()]);
      if (f.engineers_general) rows.push(["一般の仕事に就いた技術者", `${f.engineers_general.toLocaleString()} 人`]);
      if (f.education) rows.push(["教育", f.education]);
      if (f.inputs?.length) rows.push(["投入（1ターン）", goodsText(f.inputs)]);
      if (f.outputs?.length) rows.push(["産出（1ターン）", goodsText(f.outputs)]);
      // 畑を作り替えている途中: 前の作物の畑（要る原料）と、蒔いて育てている畑（収穫の始まる月）
      if (f.transition) rows.push(["作付けの切り替え", f.transition.text.replace(/^作り替えの途中: /, "")]);
    }
    if (f.priority != null) rows.push(["優先度", `${f.priority}${f.priority_changed ? "（変更済み）" : ""}`]);
    if (f.status_text) rows.push(["状態", f.status_text]);
    if (f.halted_until != null) rows.push(["停止期間", `${turnDate(f.halted_until)}まで`]);
    if (f.closing != null) rows.push(["閉鎖", `${turnDate(f.closing)}から閉鎖中（在庫を売り切ったら借入を返してマスを空けます）`]);
    if (f.foreclosed) {
      const s = f.foreclosed;
      rows.push(["差し押さえ", `${s.bank}が差し押さえ中。売り値 ${s.price}。${turnDate(s.until)}までに売れなければ取り壊します`]);
    }
    if (f.stock.length) rows.push(["施設在庫", goodsText(f.stock)]);
    const parts = [`<h3>${escapeHtml(f.name)}</h3>${table(rows)}`];
    if (f.owner === "private" && Object.keys(this.defs.facilities[f.kind]?.recipes || {}).length > 1) {
      parts.push(`<p class="hint">${escapeHtml(recipeWords().ownershipHint)}</p>`);
    }
    if (f.monument) parts.push(this.monumentHtml(f.monument, f.number));
    if (f.bank) parts.push(bankHtml(f.bank));
    parts.push(this.booksHtml(f.books));
    if (f.loans?.length) parts.push(loansHtml(f.loans));
    return parts.join("");
  }

  /** 記念碑の碑文（自動の文と、足した文）と、自作の記念碑の絵。 */
  monumentHtml(m, number) {
    const lines = [m.text, ...(m.note || [])].map((line) => escapeHtml(line)).join("<br>");
    const image = m.image ? `<img src="${escapeHtml(m.image)}" alt="" width="96" height="96" class="monument-image">`
      : `<span class="monument-image monument-placeholder" aria-hidden="true">▥</span>`;
    const built = m.built_turn != null && this.info.facility.built
      ? `<p class="hint">${escapeHtml(this.el.dataset.monumentBuilt)}: ${escapeHtml(turnDate(m.built_turn))}</p>` : "";
    const edit = this.orders && m.image && this.el.dataset.countryUrl
      ? `<a class="monument-edit-link" href="${escapeHtml(this.el.dataset.countryUrl)}#edit-monument-${encodeURIComponent(number)}">${escapeHtml(this.el.dataset.monumentEdit)}</a>` : "";
    return `<section class="monument"><h4>${escapeHtml(this.el.dataset.monumentInscription)}</h4>
      <div class="monument-body">${image}<div class="monument-copy">${built}<blockquote class="inscription">${lines}</blockquote></div></div>${edit}</section>`;
  }

  /** 記念碑を建てるときの、碑文に足す文の入力欄（記念碑でなければ空）。 */
  noteField(facility) {
    if (!this.defs.facilities[facility]?.monument) return "";
    return `<label>碑文に足す文（2 行まで・1 行 40 字まで。空欄なら自動の碑文だけ）<br>
      <textarea name="note" rows="2" maxlength="81" class="wide"></textarea></label>`;
  }

  /** 施設の収支（直近のターンの内訳と、直近の数ターンの損益の合計）と預金。 */
  booksHtml(b) {
    if (!b) return "";
    const rows = b.rows.map((r) => [r.label, r.text]);
    rows.push([`損益（直近 ${b.turns} ターンの合計）`, b.profit_total]);
    if (b.deposit != null) rows.push([b.deposit_label || "預金", b.deposit]);
    if (b.debt) rows.push(["借入", b.debt]);
    const result = b.profit < 0 ? "（赤字）" : "";
    return `<h4>収支${result} <small class="heading-date">${escapeHtml(turnDate(b.turn))}</small></h4>${table(rows)}`;
  }

  actionsHtml(t) {
    const placeForm = t.place
      ? `
        <form class="actions" data-form="place">
          <h4>地名の変更（この区画: ${escapeHtml(t.place)}）</h4>
          <input name="name" maxlength="16" required placeholder="新しい地名">
          <button type="submit">命令に追加</button>
        </form>`
      : "";
    const f = t.facility;
    if (!f) {
      if (!this.defs.terrain[t.terrain]?.land) return "";
      const choices = this.buildChoices(t.terrain);
      if (!choices.length) return `<p class="muted">この地形に建設できる施設はありません。</p>${placeForm}`;
      const options = choices
        .map(([id, d]) => {
          const note = this.facilityNote(id, d);
          return `<option value="${id}">${escapeHtml(d.name)}${note ? `（${note}）` : ""}</option>`;
        })
        .join("");
      return `
        <form class="actions" data-form="build">
          <h4>建設</h4>
          <label>施設 <select name="facility">${options}</select></label>
          <div data-slot="recipe">${this.recipeSelect(choices[0][0])}</div>
          <label>名称（空欄なら自動） <input name="name" maxlength="32"></label>
          <span data-slot="note">${this.noteField(choices[0][0])}</span>
          <div data-slot="hide">${this.hideField(choices[0][0])}</div>
          <p class="hint" data-slot="build-info">${this.buildInfo(choices[0][0])}</p>
          <p class="hint" data-slot="commute"></p>
          <p class="warning" data-slot="check" aria-live="polite"></p>
          <button type="submit">命令に追加</button>
        </form>${placeForm}`;
    }
    const isHousing = f.kind === this.defs.housing;
    const parts = [];
    if (isHousing && f.shop_slots) {
      // 町の商店（回7）: 町のマスを選んで建てる。入る数は町の段階で決まる
      const full = (f.shops || 0) >= f.shop_slots;
      parts.push(`
        <form class="actions" data-form="shop">
          <h4>商店の建設（この町に ${(f.shops || 0).toLocaleString()} / ${f.shop_slots.toLocaleString()} 軒）</h4>
          <label>名称（空欄なら自動） <input name="name" maxlength="32"></label>
          ${full ? `<p class="warning">この町には、もう商店が入りません（町が育つと増えます）。</p>` : ""}
          <button type="submit">命令に追加</button>
        </form>`);
    }
    if (!isHousing) {
      parts.push(`
        <form class="actions" data-form="rename">
          <h4>施設名の変更</h4>
          <input name="name" maxlength="32" value="${escapeHtml(f.custom_name || "")}" placeholder="空欄なら自動の名称">
          <button type="submit">命令に追加</button>
        </form>`);
    }
    if (f.priority != null && !isHousing) {
      const options = Array.from({ length: 10 }, (_, p) =>
        `<option value="${p}"${p === f.priority ? " selected" : ""}>${p}</option>`).join("");
      parts.push(`
        <form class="actions" data-form="priority">
          <h4>優先度（値の大きい施設から労働者・資材・電力を配分）</h4>
          <select name="priority">${options}</select>
          <button type="submit">命令に追加</button>
        </form>`);
    }
    if (f.accounting_target) {
      // 命令が通る施設（国営で、公務の施設でない）だけに出す。変えられる先は今と違う方だけ
      parts.push(`
        <form class="actions" data-form="accounting">
          <h4>会計方式の変更（現在: ${escapeHtml(f.accounting_name)}）</h4>
          <p class="hint">${escapeHtml(ACCOUNTING_HINTS[f.accounting_target] || "")}</p>
          <input type="hidden" name="accounting" value="${escapeHtml(f.accounting_target)}">
          <button type="submit">${escapeHtml(f.accounting_target_name)}に変更（命令に追加）</button>
        </form>`);
    }
    if (f.recipe_choices?.length || f.recipe_next_name) {
      // 生産方式の切り替え（国営の施設だけ。次のターンの市場の段で切り替わり、そのターンは稼働率が半分）
      const w = recipeWords();
      const amounts = (list) => list.map((g) => `${escapeHtml(g.name)} ${g.amount}`).join("・") || escapeHtml(w.none);
      const options = (f.recipe_choices || [])
        .map((r) => `<option value="${escapeHtml(r.recipe)}">${escapeHtml(recipeLabel(r.recipe,
          this.defs.facilities[f.kind]?.recipes?.[r.recipe], r.name))}（${amounts(r.inputs)} → ${amounts(r.outputs)}）</option>`)
        .join("");
      const pending = f.recipe_next_name
        ? `<p class="hint">${escapeHtml(w.next.replace("{name}", f.recipe_next_name))}</p>` : "";
      const form = options ? `
          <select class="recipe-select" name="recipe" aria-label="${escapeHtml(w.method)}">${options}</select>
          <p class="hint">${escapeHtml(w.transition)}</p>
          <button type="submit">${escapeHtml(w.add)}</button>` : "";
      parts.push(`
        <form class="actions" data-form="recipe">
          <h4>${escapeHtml(w.change)}（${escapeHtml(w.current)}: ${escapeHtml(f.recipe_name || "")}）</h4>
          ${this.recipeHint(f.kind)}${pending}${form}
        </form>`);
    }
    if (f.ownership_target) {
      // 命令が通る施設（民営なら国有化、民営にできる国営なら民営化）だけに出す
      parts.push(`
        <form class="actions" data-form="ownership">
          <h4>${escapeHtml(f.ownership_target_name)}（現在: ${escapeHtml(f.owner_name)}）</h4>
          <p class="hint">${escapeHtml(OWNERSHIP_HINTS[f.ownership_target] || "")}</p>
          <input type="hidden" name="kind" value="${escapeHtml(f.ownership_target)}">
          <button type="submit">${escapeHtml(f.ownership_target_name)}（命令に追加）</button>
        </form>`);
    }
    if (f.subsidy_target) parts.push(this.subsidyForm(f));
    if (f.emergency_loan) {
      const now = f.bank?.emergency ? `（今の上限: ${escapeHtml(f.bank.emergency)}）` : "";
      parts.push(`
        <form class="actions" data-form="emergency">
          <h4>緊急の貸し出し${now}</h4>
          <p class="hint">準備預金が足りないとき、中央銀行が担保の足りない分も毎ターンこの額まで貸します。0 で取り消します。</p>
          <label>上限額 <input name="amount" type="number" min="0" step="1" required></label>
          <button type="submit">命令に追加</button>
        </form>`);
    }
    if (f.resolve_methods) {
      const methods = Object.entries(f.resolve_methods)
        .map(([id, name]) => `
          <label><input type="radio" name="method" value="${escapeHtml(id)}"${id === "transfer" ? " checked" : ""}>
            ${escapeHtml(name)}: <span class="hint">${escapeHtml(RESOLUTION_HINTS[id] || "")}</span></label>`)
        .join("");
      parts.push(`
        <form class="actions" data-form="resolve">
          <h4>破綻した銀行の処理（行政力を使いません）</h4>
          <p class="hint">次のターンまでに選ばなければ「引き継ぎ」（引き継ぐ銀行がなければ「救済」）になります。</p>
          ${methods}
          <button type="submit">命令に追加</button>
        </form>`);
    }
    if (!t.capital) {
      parts.push(`<p class="actions"><button type="button" data-action="demolish">撤去（命令に追加）</button></p>`);
    }
    parts.push(placeForm);
    return parts.join("");
  }

  /** この施設への補助金（生産補助・救済補助）の入力欄。 */
  subsidyForm(f) {
    const current = f.subsidies?.length ? `<p class="hint">今の補助金: ${escapeHtml(f.subsidies.join("、"))}</p>` : "";
    const kinds = Object.entries(SUBSIDY_KINDS);
    const options = kinds.map(([id, k]) => `<option value="${id}">${k.name}</option>`).join("");
    const hints = kinds.map(([, k]) => `<li>${k.name}: ${k.hint}</li>`).join("");
    return `
      <form class="actions" data-form="subsidy">
        <h4>この施設への補助金</h4>
        ${current}
        <label>種類 <select name="kind">${options}</select></label>
        <ul class="hint">${hints}</ul>
        <label>割合（%、0 でやめる） <input name="rate" type="number" min="0" max="100" step="0.1" required></label>
        <label>期間（ターン） <input name="turns" type="number" min="1" max="1200" step="1" value="12" required></label>
        <button type="submit">命令に追加</button>
      </form>`;
  }

  /** この地形に建てられる施設。この島で使える生産方式があり、数の上限に達していない施設を先に並べる。 */
  buildChoices(terrain) {
    // 町の中に入る店（site: town）は空きマスには建たない（町のマスの欄から建てる）
    const choices = Object.entries(this.defs.facilities).filter(([, d]) => d.buildable && !d.site && d.terrain.includes(terrain));
    const usable = ([id, d]) => !this.facilityNote(id, d);
    return [...choices.filter(usable), ...choices.filter((c) => !usable(c))];
  }

  /** 島にある（工事中を含む）施設 kind の数。 */
  facilityCount(kind) {
    return (this.map.facilities || []).filter((f) => f.kind === kind).length;
  }

  /** この島で生産方式が使えないわけ（使えれば ""）。 */
  recipeProblem(recipe) {
    const resource = recipe.requires_resource;
    if (resource && !this.resources.has(resource)) return "地下資源なし";
    if (this.producible && Object.keys(recipe.inputs).some((g) => !this.producible.has(g))) return "原料なし";
    return "";
  }

  /** 建てられない施設に付ける注記（数の上限に達した、どの生産方式もこの島で使えない。建てられれば ""）。 */
  facilityNote(id, d) {
    if (d.max_per_island != null && this.facilityCount(id) >= d.max_per_island) return "上限に達しています";
    const problems = Object.values(d.recipes).map((r) => this.recipeProblem(r));
    if (problems.some((p) => !p)) return "";
    return problems.includes("原料なし") ? "原料なし" : "地下資源なし";
  }

  recipeSelect(facility) {
    const recipes = Object.entries(this.defs.facilities[facility]?.recipes || {});
    if (recipes.length <= 1) return "";
    // この島で使える生産方式を初めから選んでおく
    const chosen = recipes.find(([, r]) => !this.recipeProblem(r))?.[0];
    const options = recipes
      .map(([id, r]) => {
        const problem = this.recipeProblem(r);
        const selected = id === chosen ? " selected" : "";
        return `<option value="${escapeHtml(id)}"${selected}>${escapeHtml(recipeLabel(id, r))}${problem ? `（${problem}）` : ""}</option>`;
      })
      .join("");
    return `${this.recipeHint(facility)}<label>${escapeHtml(recipeWords().method)} <select class="recipe-select" name="recipe">${options}</select></label>`;
  }

  recipeHint(facility) {
    const w = recipeWords();
    const recipes = Object.values(this.defs.facilities[facility]?.recipes || {});
    const electronics = recipes.some((r) => Object.hasOwn(r.inputs || {}, "electronics"));
    return (electronics ? `<p class="hint">${escapeHtml(w.electronicsHint)}</p>` : "") +
      `<p class="hint">${escapeHtml(w.ownershipHint)}</p>`;
  }

  /** 選んでいる生産方式の原料をこの島で生産できないときの注意（なければ ""）。 */
  inputWarning(payload) {
    const recipes = this.defs.facilities[payload.facility]?.recipes || {};
    const recipe = recipes[payload.recipe ?? Object.keys(recipes)[0]];
    if (!recipe || !this.producible) return "";
    const missing = Object.keys(recipe.inputs).filter((g) => !this.producible.has(g));
    if (!missing.length) return "";
    const names = missing.map((g) => this.goodName(g)).join("・");
    return `原料の${names}をこの島では生産できないため、建てても操業できない見込みです。`;
  }

  buildInfo(facility) {
    const d = this.defs.facilities[facility];
    if (!d) return "";
    const c = d.construction;
    const bits = [`工期 ${c.turns} ターン`];
    if (c.labor) bits.push(`建設労働者 ${c.labor.toLocaleString()} 人`);
    if (!d.monument) bits.push(`完成後の従業者 ${d.workers.toLocaleString()} 人`);
    const materials = Object.entries(c.materials);
    if (materials.length) bits.push(`建設資材 ${materials.map(([g, q]) => `${this.goodName(g)} ${q} ${this.unitName(g)}`).join("・")}`);
    if (d.coastal) bits.push("海に面したマスのみ");
    if (d.near_capital != null) bits.push(`首都から ${d.near_capital} マス以内`);
    if (d.max_per_island != null) bits.push(`1国 ${d.max_per_island} か所まで`);
    return escapeHtml(bits.join(" ／ "));
  }

  /** 地下建設の数値は定義の hide からだけ読む。API にない間は指定を送らない。 */
  hideField(kind) {
    const w = words();
    const hide = this.defs.facilities[kind]?.hide;
    if (!hide) return ["missile_base", "nuclear_facility"].includes(kind)
      ? `<p class="hint">${escapeHtml(w.hideUnavailable)}</p>` : "";
    return `<label><input type="checkbox" name="hidden" value="true"> ${escapeHtml(w.hide)}</label>
      <p class="hint">${escapeHtml(w.hideHint)} ${escapeHtml(w.hideTurns)}: ${escapeHtml(hide.turns)} ／ ${escapeHtml(w.hideMaterials)}: ${escapeHtml(hide.materials_factor)}</p>`;
  }

  goodName(good) {
    return this.defs.goods[good]?.name || good;
  }

  unitName(good) {
    return this.defs.goods[good]?.unit || "";
  }

  markButtonsHtml() {
    const buttons = Object.entries(MARK_MODES)
      .map(([id, m]) => `<button type="button" data-mark="${id}">${m.label}</button>`)
      .join("");
    return `<div class="mark-buttons"><span class="hint">地図でマスを選択して出す命令:</span> ${buttons}</div>`;
  }

  markingHtml() {
    const m = MARK_MODES[this.mode];
    return `
      <div class="marking">
        <p><strong>${m.label}</strong>: 地図のマスを押して選択してください（再度押すと解除。${MAX_TILES} マスまで）。</p>
        ${this.mode === "infrastructure" ? this.infraChoiceHtml() : ""}
        <p>選択したマス: ${this.marked.size}</p>
        <button type="button" data-action="mark-done"${this.marked.size ? "" : " disabled"}>命令に追加</button>
        <button type="button" data-action="mark-cancel">取り消し</button>
      </div>`;
  }

  /** インフラの工事の種類を選ぶ欄と、1マスの費用の説明。 */
  infraChoiceHtml() {
    const kinds = this.defs.infrastructure || {};
    const options = Object.entries(kinds)
      .map(([id, d]) => `<option value="${id}"${id === this.infraKind ? " selected" : ""}>${escapeHtml(d.name)}</option>`)
      .join("");
    const d = kinds[this.infraKind];
    const materials = d ? Object.entries(d.materials).map(([g, q]) => `${this.goodName(g)} ${q} ${this.unitName(g)}`).join("・") : "";
    const cost = d
      ? `1マスを 0% から 100% にするのに 資材 ${materials || "なし"}、作業員 ${d.labor.toLocaleString()} 人・ターン（工期 ${d.turns} ターン以上）。` +
        "工事の列に並び、公共事業費で失業者を雇って順に工事します。海のマスと整備度 100% のマスは並びません。"
      : "";
    return `
      <label>種類 <select data-infra-kind>${options}</select></label>
      <p class="hint">${escapeHtml(cost)}</p>`;
  }

  // --- 操作 -----------------------------------------------------------------

  handleClick(e) {
    const button = e.target.closest("button");
    if (!button || !(this.el.contains(button) || this.tools?.contains(button))) return;
    if (button.type === "submit") {
      if (button.form?.dataset.order) return;
      e.preventDefault();
      this.submit(button.form);
      return;
    }
    if (button.dataset.mark) {
      this.mode = button.dataset.mark;
      this.marked.clear();
      this.onMarkedChange();
      if (this.mode === "infrastructure" && this.infraKind) this.onInfraKind(this.infraKind);
      this.render();
    } else if (button.dataset.action === "mark-cancel") {
      this.endMarking();
    } else if (button.dataset.action === "mark-done") {
      const tiles = [...this.marked].map((k) => k.split(",").map(Number));
      const m = MARK_MODES[this.mode];
      this.addOrder(m.kind, m.payload(tiles, this.infraKind));
      this.endMarking();
    } else if (button.dataset.action === "demolish") {
      const f = this.info.facility;
      if (confirm(`${f.name} を撤去する命令を追加しますか？`)) {
        this.addOrder("demolish", { x: this.tile.x, y: this.tile.y });
      }
    }
  }

  handleChange(e) {
    if (e.target.matches("[data-infra-kind]")) {
      this.infraKind = e.target.value;
      this.onInfraKind(this.infraKind);
      this.render();
      return;
    }
    const form = e.target.form;
    if (form?.dataset.form !== "build") return;
    if (e.target.name === "facility") {
      form.querySelector("[data-slot=recipe]").innerHTML = this.recipeSelect(e.target.value);
      form.querySelector("[data-slot=build-info]").innerHTML = this.buildInfo(e.target.value);
      form.querySelector("[data-slot=note]").innerHTML = this.noteField(e.target.value);
      form.querySelector("[data-slot=hide]").innerHTML = this.hideField(e.target.value);
    }
    this.checkBuild();
  }

  endMarking() {
    this.mode = null;
    if (this.military) this.military.picking = null;
    this.marked.clear();
    this.onMarkedChange();
    this.render();
  }

  submit(form) {
    if (!form.reportValidity()) return; // 入力の決まり（required・min など）を確かめる
    const { x, y } = this.tile;
    const data = new FormData(form);
    const name = String(data.get("name") ?? "").trim();
    if (form.dataset.form === "build") {
      const payload = { facility: data.get("facility"), x, y };
      if (data.get("recipe")) payload.recipe = data.get("recipe");
      if (this.defs.facilities[payload.facility]?.hide && data.get("hidden") === "true") payload.hidden = true;
      if (name) payload.name = name;
      const note = String(data.get("note") ?? "").trim();
      if (note) payload.note = note;
      this.addOrder("build", payload);
    } else if (form.dataset.form === "shop") {
      const payload = { facility: "shop", x, y };
      if (name) payload.name = name;
      this.addOrder("build", payload);
    } else if (form.dataset.form === "rename") {
      this.addOrder("rename_facility", { x, y, name });
    } else if (form.dataset.form === "place") {
      if (name) this.addOrder("rename_place", { x, y, name });
    } else if (form.dataset.form === "priority") {
      this.addOrder("set_priority", { x, y, priority: Number(data.get("priority")) });
    } else if (form.dataset.form === "accounting") {
      this.addOrder("set_accounting", { x, y, accounting: data.get("accounting") });
    } else if (form.dataset.form === "recipe") {
      this.addOrder("change_recipe", { x, y, recipe: data.get("recipe") });
    } else if (form.dataset.form === "ownership") {
      this.addOrder(data.get("kind"), { x, y });
    } else if (form.dataset.form === "subsidy") {
      const payload = { kind: data.get("kind"), x, y, rate: Number(data.get("rate")) };
      this.addOrder("set_subsidy", { ...payload, turns: Math.round(Number(data.get("turns"))) });
    } else if (form.dataset.form === "emergency") {
      this.addOrder("emergency_loan", { x, y, amount: Math.round(Number(data.get("amount"))) });
    } else if (form.dataset.form === "resolve") {
      this.addOrder("resolve_bank", { x, y, method: data.get("method") });
    }
  }

  /** 命令の列に足し、保存の結果を地図のそばに出す（命令の欄まで見に行かなくてよいように）。 */
  async addOrder(kind, payload) {
    await this.orders.add(kind, payload);
    if (this.notice) this.notice.textContent = this.orders.message;
  }

  buildPayload() {
    const form = this.el.querySelector("[data-form=build]");
    if (!form) return null;
    const data = new FormData(form);
    const payload = { facility: data.get("facility"), x: this.tile.x, y: this.tile.y };
    if (data.get("recipe")) payload.recipe = data.get("recipe");
    if (this.defs.facilities[payload.facility]?.hide && data.get("hidden") === "true") payload.hidden = true;
    const note = String(data.get("note") ?? "").trim();
    if (note) payload.note = note;
    return payload;
  }

  /**
   * 今の島で建てられるかをサーバーに聞き、建てられないなら理由を出す（それでも命令は足せる）。
   * 通勤圏の労働者の数も出し、住民がいなければ工事が進まないことを知らせる。
   */
  async checkBuild() {
    const payload = this.buildPayload();
    if (!payload) return;
    const request = this.request;
    const res = await sendJson(this.el.dataset.checkUrl, "POST", { kind: "build", payload });
    const slot = this.el.querySelector("[data-slot=check]");
    const commuteSlot = this.el.querySelector("[data-slot=commute]");
    if (request !== this.request || !slot) return;
    const commute = res.ok ? res.data.commute : null;
    if (commuteSlot) {
      commuteSlot.textContent = commute
        ? `通勤圏（${commute.radius} マス以内）の労働者 ${commute.labor.toLocaleString()} 人` +
          `（うち就業者 ${commute.employed.toLocaleString()} 人）`
        : "";
    }
    if (res.ok && !res.data.ok) slot.textContent = `現状では建設できません: ${res.data.text}`;
    else if (commute && commute.labor === 0) slot.textContent = "通勤圏に住民がいないため、労働者を雇えず工事も操業も進みません。";
    else {
      // 建てられても、送電網や働き手で止まりそうなら知らせる（確かめの notes）
      const notes = (res.ok && res.data.notes) || [];
      slot.textContent = [...notes, this.inputWarning(payload)].filter(Boolean).join(" ");
    }
  }
}

function idSet(text) {
  return new Set((text || "").split(",").filter(Boolean));
}

function table(rows) {
  const body = rows.map(([k, v]) => `<tr><th>${escapeHtml(k)}</th><td>${escapeHtml(v)}</td></tr>`).join("");
  return `<div class="table-scroll"><table class="info">${body}</table></div>`;
}

/** 銀行の帳簿（直近の統計）と預金金利・破綻。 */
function bankHtml(b) {
  const rows = [];
  if (b.failed != null) {
    const why = b.failed_reason === "liquidity" ? "資金繰りに行き詰まり" : "債務超過で";
    rows.push(["破綻", `${turnDate(b.failed)}に${why}破綻しました（新しい融資と預金の受け入れを止めています）`]);
  }
  if (b.crunch_until != null) {
    rows.push(["融資の停止", `国債の債務不履行で損をしたため、${turnDate(b.crunch_until)}まで新しい融資を止めています（返す元金の借り換えだけ）`]);
  }
  if (b.deposits != null) rows.push(["預金", b.deposits]);
  if (b.loans != null) rows.push(["融資", b.loans]);
  if (b.net_worth != null) rows.push(["自己資本", b.net_worth]);
  if (b.capital_ratio != null) {
    rows.push(["自己資本比率", `${b.capital_ratio || "融資なし"}（規制 ${b.regulatory_ratio} 以上）`]);
  }
  rows.push(["預金金利（年）", b.deposit_rate]);
  if (b.emergency) rows.push(["緊急の貸し出しの上限", b.emergency]);
  const turn = b.turn != null ? ` <small class="heading-date">${escapeHtml(turnDate(b.turn))}</small>` : "";
  return `<h4>銀行の帳簿${turn}</h4>${table(rows)}`;
}

/** 施設の借入（融資ごと）。 */
function loansHtml(loans) {
  const rows = loans.map((l) => {
    const late = l.arrears ? `・返済の遅れ ${l.arrears} 回` : "";
    return [`${l.lender}・${l.purpose}`, `${l.balance}（年利 ${l.rate}${late}）`];
  });
  return `<h4>借入</h4>${table(rows)}`;
}

function goodsText(list) {
  return list.map((g) => `${g.name} ${g.amount}${g.unit ? ` ${g.unit}` : ""}`).join("、");
}
