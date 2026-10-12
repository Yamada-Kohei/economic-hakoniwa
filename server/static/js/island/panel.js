// マスの情報の欄: 選んだマスと、そこにある施設の詳しい情報を出す。
// 島の持ち主には、そのマスへの命令（建設・撤去・名称・地名・優先度）と、
// 地図でマスを選んで出す命令（送電線・開発禁止区域）の入口も出す。

import { escapeHtml, getJson, sendJson } from "./api.js";

const MAX_TILES = 64; // 1つの命令で選べるマスの数（orders.yaml の maxItems と同じ）

// 地図でマスを選んで出す命令
const MARK_MODES = {
  power_line: { label: "送電線の敷設", kind: "build_power_line", payload: (tiles) => ({ tiles }) },
  no_dev_on: { label: "開発禁止区域の指定", kind: "set_no_development", payload: (tiles) => ({ tiles, value: true }) },
  no_dev_off: { label: "開発禁止区域の解除", kind: "set_no_development", payload: (tiles) => ({ tiles, value: false }) },
};

export class TilePanel {
  /**
   * el: 欄の要素（data-tile-url に「…/tiles/0,0」、data-check-url に命令の確認の URL）
   * options: orders（命令の列。持ち主でなければ null）、marked（印を付けたマスの Set）、onMarkedChange
   */
  constructor(el, defs, map, options = {}) {
    this.el = el;
    this.defs = defs;
    this.map = map;
    this.orders = options.orders || null;
    this.marked = options.marked || new Set();
    this.onMarkedChange = options.onMarkedChange || (() => {});
    this.mode = null;
    this.tile = null;
    this.info = null;
    this.request = 0;
    el.addEventListener("click", (e) => this.handleClick(e));
    el.addEventListener("change", (e) => this.handleChange(e));
    this.render();
  }

  /** 地図でマスを選んでいる途中か。 */
  get marking() {
    return this.mode !== null;
  }

  async show(tile) {
    this.tile = tile;
    this.info = null;
    if (!tile) {
      this.render();
      return;
    }
    const request = ++this.request;
    this.el.setAttribute("aria-busy", "true");
    try {
      const info = await getJson(this.el.dataset.tileUrl.replace(/0,0$/, `${tile.x},${tile.y}`));
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
    const key = `${tile.x},${tile.y}`;
    if (this.marked.has(key)) this.marked.delete(key);
    else if (this.marked.size < MAX_TILES) this.marked.add(key);
    this.onMarkedChange();
    this.render();
  }

  // --- 表示 -----------------------------------------------------------------

  render() {
    const parts = [];
    if (this.mode) parts.push(this.markingHtml());
    if (!this.tile) {
      parts.push(`<p class="muted">マスを選択すると、ここに詳細が表示されます。</p>`);
    } else if (!this.info) {
      parts.push(`<p class="muted">読み込み中…</p>`);
    } else if (this.info.error) {
      parts.push(`<p class="error">マスの情報を取得できませんでした。</p>`);
    } else {
      parts.push(this.tileHtml(this.info));
      if (this.info.facility) parts.push(this.facilityHtml(this.info.facility));
      if (this.orders) parts.push(this.actionsHtml(this.info));
    }
    if (this.orders && !this.mode) parts.push(this.markButtonsHtml());
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
    if (t.power_line) rows.push(["送電線", "あり"]);
    if (t.zone === "no_development") rows.push(["開発", "禁止区域（町が形成されません）"]);
    if (t.capital_buildings?.length) rows.push(["首都の建物", t.capital_buildings.join("・")]);
    const title = t.capital ? `首都 ${t.capital_name || ""}（${t.x}, ${t.y}）` : `(${t.x}, ${t.y})`;
    return `<h3>${escapeHtml(title)}</h3>${table(rows)}`;
  }

  facilityHtml(f) {
    if (f.level_name) {
      // 町（住宅地）には名前を付けない。段階と、次の段階に育つのに要る資源を出す
      const rows = [["段階", f.level_name], ["居住上限", `${f.capacity.toLocaleString()} 人`]];
      if (f.next_level_name) {
        const need = f.next_materials?.length ? goodsText(f.next_materials) : "なし";
        rows.push([`${f.next_level_name}への発展に必要な資源`, need]);
      }
      return `<h3>${escapeHtml(f.level_name)}</h3>${table(rows)}`;
    }
    const rows = [["種類", f.kind_name], ["所有", `${f.owner_name}・${f.accounting_name}`]];
    if (f.recipe_name) rows.push(["生産方式", f.recipe_name]);
    if (!f.built) {
      rows.push(["建設進捗", `${Math.round(f.progress * 100)}%（工期 ${f.turns} ターン）`]);
      rows.push(["建設労働者", `${f.workers.toLocaleString()} / ${f.workers_needed.toLocaleString()} 人`]);
      if (f.materials.length) rows.push(["建設資材", goodsText(f.materials)]);
    } else if (f.workers_needed != null) {
      rows.push(["労働者", `${f.workers.toLocaleString()} / ${f.workers_needed.toLocaleString()} 人`]);
      rows.push(["稼働率", `${Math.round(f.operating_rate * 100)}%`]);
      if (f.inputs?.length) rows.push(["投入（1ターン）", goodsText(f.inputs)]);
      if (f.outputs?.length) rows.push(["産出（1ターン）", goodsText(f.outputs)]);
    }
    if (f.priority != null) rows.push(["優先度", `${f.priority}${f.priority_changed ? "（変更済み）" : ""}`]);
    if (f.status_text) rows.push(["状態", f.status_text]);
    if (f.halted_until != null) rows.push(["停止期間", `ターン ${f.halted_until} まで`]);
    if (f.stock.length) rows.push(["施設在庫", goodsText(f.stock)]);
    return `<h3>${escapeHtml(f.name)}</h3>${table(rows)}`;
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
      const options = choices.map(([id, d]) => `<option value="${id}">${escapeHtml(d.name)}</option>`).join("");
      return `
        <form class="actions" data-form="build">
          <h4>建設</h4>
          <label>施設 <select name="facility">${options}</select></label>
          <span data-slot="recipe">${this.recipeSelect(choices[0][0])}</span>
          <label>名称（空欄なら自動） <input name="name" maxlength="32"></label>
          <p class="hint" data-slot="build-info">${this.buildInfo(choices[0][0])}</p>
          <p class="warning" data-slot="check" aria-live="polite"></p>
          <button type="submit">命令に追加</button>
        </form>${placeForm}`;
    }
    const isHousing = f.kind === this.defs.housing;
    const parts = [];
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
    if (!t.capital) {
      parts.push(`<p class="actions"><button type="button" data-action="demolish">撤去（命令に追加）</button></p>`);
    }
    parts.push(placeForm);
    return parts.join("");
  }

  /** この地形に建てられる施設。 */
  buildChoices(terrain) {
    return Object.entries(this.defs.facilities).filter(([, d]) => d.buildable && d.terrain.includes(terrain));
  }

  recipeSelect(facility) {
    const recipes = Object.entries(this.defs.facilities[facility]?.recipes || {});
    if (recipes.length <= 1) return "";
    const options = recipes.map(([id, r]) => `<option value="${id}">${escapeHtml(r.name)}</option>`).join("");
    return `<label>生産方式 <select name="recipe">${options}</select></label>`;
  }

  buildInfo(facility) {
    const d = this.defs.facilities[facility];
    if (!d) return "";
    const c = d.construction;
    const bits = [`工期 ${c.turns} ターン`, `労働者 ${d.workers.toLocaleString()} 人`];
    const materials = Object.entries(c.materials);
    if (materials.length) bits.push(`建設資材 ${materials.map(([g, q]) => `${this.goodName(g)} ${q} ${this.unitName(g)}`).join("・")}`);
    if (d.coastal) bits.push("海に面したマスのみ");
    if (d.near_capital != null) bits.push(`首都から ${d.near_capital} マス以内`);
    if (d.max_per_island != null) bits.push(`1国 ${d.max_per_island} か所まで`);
    return escapeHtml(bits.join(" ／ "));
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
    return `<div class="actions"><h4>地図でマスを選択して出す命令</h4>${buttons}</div>`;
  }

  markingHtml() {
    const m = MARK_MODES[this.mode];
    return `
      <div class="marking">
        <p><strong>${m.label}</strong>: 地図のマスを押して選択してください（再度押すと解除。${MAX_TILES} マスまで）。</p>
        <p>選択したマス: ${this.marked.size}</p>
        <button type="button" data-action="mark-done"${this.marked.size ? "" : " disabled"}>命令に追加</button>
        <button type="button" data-action="mark-cancel">取り消し</button>
      </div>`;
  }

  // --- 操作 -----------------------------------------------------------------

  handleClick(e) {
    const button = e.target.closest("button");
    if (!button || !this.el.contains(button)) return;
    if (button.type === "submit") {
      e.preventDefault();
      this.submit(button.form);
      return;
    }
    if (button.dataset.mark) {
      this.mode = button.dataset.mark;
      this.marked.clear();
      this.onMarkedChange();
      this.render();
    } else if (button.dataset.action === "mark-cancel") {
      this.endMarking();
    } else if (button.dataset.action === "mark-done") {
      const tiles = [...this.marked].map((k) => k.split(",").map(Number));
      const m = MARK_MODES[this.mode];
      this.orders.add(m.kind, m.payload(tiles));
      this.endMarking();
    } else if (button.dataset.action === "demolish") {
      const f = this.info.facility;
      if (confirm(`${f.name} を撤去する命令を追加しますか？`)) {
        this.orders.add("demolish", { x: this.tile.x, y: this.tile.y });
      }
    }
  }

  handleChange(e) {
    const form = e.target.form;
    if (form?.dataset.form !== "build") return;
    if (e.target.name === "facility") {
      form.querySelector("[data-slot=recipe]").innerHTML = this.recipeSelect(e.target.value);
      form.querySelector("[data-slot=build-info]").innerHTML = this.buildInfo(e.target.value);
    }
    this.checkBuild();
  }

  endMarking() {
    this.mode = null;
    this.marked.clear();
    this.onMarkedChange();
    this.render();
  }

  submit(form) {
    const { x, y } = this.tile;
    const data = new FormData(form);
    const name = String(data.get("name") ?? "").trim();
    if (form.dataset.form === "build") {
      const payload = { facility: data.get("facility"), x, y };
      if (data.get("recipe")) payload.recipe = data.get("recipe");
      if (name) payload.name = name;
      this.orders.add("build", payload);
    } else if (form.dataset.form === "rename") {
      this.orders.add("rename_facility", { x, y, name });
    } else if (form.dataset.form === "place") {
      if (name) this.orders.add("rename_place", { x, y, name });
    } else if (form.dataset.form === "priority") {
      this.orders.add("set_priority", { x, y, priority: Number(data.get("priority")) });
    }
  }

  buildPayload() {
    const form = this.el.querySelector("[data-form=build]");
    if (!form) return null;
    const data = new FormData(form);
    const payload = { facility: data.get("facility"), x: this.tile.x, y: this.tile.y };
    if (data.get("recipe")) payload.recipe = data.get("recipe");
    return payload;
  }

  /** 今の島で建てられるかをサーバーに聞き、建てられないなら理由を出す（それでも命令は足せる）。 */
  async checkBuild() {
    const payload = this.buildPayload();
    if (!payload) return;
    const request = this.request;
    const res = await sendJson(this.el.dataset.checkUrl, "POST", { kind: "build", payload });
    const slot = this.el.querySelector("[data-slot=check]");
    if (request !== this.request || !slot) return;
    slot.textContent = res.ok && !res.data.ok ? `現状では建設できません: ${res.data.text}` : "";
  }
}

function table(rows) {
  const body = rows.map(([k, v]) => `<tr><th>${escapeHtml(k)}</th><td>${escapeHtml(v)}</td></tr>`).join("");
  return `<table class="info">${body}</table>`;
}

function goodsText(list) {
  return list.map((g) => `${g.name} ${g.amount}${g.unit ? ` ${g.unit}` : ""}`).join("、");
}
