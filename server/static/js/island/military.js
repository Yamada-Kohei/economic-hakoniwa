// マスの欄の軍の情報と、部隊の移動先を選ぶ状態。描画側のスプライトには触らない。
import { escapeHtml, getJson } from "./api.js";
import {
  words, armyName, statusName, orderText, table, landAt, transports,
  armyOptions, options, field, orderForm, bindOrderForms,
} from "../military/common.js";

export class MilitaryTile {
  constructor(panel) {
    this.panel = panel;
    this.data = null;
    this.error = false;
    this.picking = null;
    this.request = 0;
    bindOrderForms(panel.el, async (kind, payload) => {
      await panel.addOrder(kind, payload);
      return panel.orders.message;
    });
    for (const root of [panel.el, panel.tools]) root?.addEventListener("click", (event) => {
      const move = event.target.closest("[data-unit-move]");
      const cancel = event.target.closest("[data-military-cancel]");
      if (move) this.start(Number(move.dataset.unitMove));
      if (cancel) panel.endMarking();
    });
  }

  async load() {
    const request = ++this.request;
    try {
      const { military } = await getJson(this.panel.el.dataset.militaryUrl);
      if (request !== this.request) return;
      this.data = military;
      this.error = false;
    } catch {
      if (request !== this.request) return;
      this.data = null;
      this.error = true;
    }
  }

  html(tile) {
    const w = words();
    if (this.error) return `<p class="error">${escapeHtml(w.error)}</p>`;
    if (!this.data) return `<p>${escapeHtml(w.loading)}</p>`;
    const p = this.panel;
    const units = landAt(this.data, tile);
    const parts = units.map((u) => {
      const choices = field("unit", w.unit, options([[u.id, `#${u.id}`]]));
      const commands = ["dig_trench", "stop_unit", "disband_unit"].map((kind) => {
        const label = { dig_trench: w.dig, stop_unit: w.stop, disband_unit: w.disband }[kind];
        const confirm = kind === "disband_unit" ? ` data-confirm="${escapeHtml(w.confirm)}"` : "";
        return `<form class="actions military-form" data-order="${kind}"${confirm}>
          <input type="hidden" name="unit" value="${u.id}"><button type="submit">${escapeHtml(label)}</button>
          <p class="order-notice" role="status"></p></form>`;
      }).join("");
      const port = this.data.bases.some((b) => b.naval_base && b.x === tile.x && b.y === tile.y);
      const fleets = port ? transports(this.data).filter((f) => f.docked) : [];
      const load = fleets.length ? orderForm("load_unit", choices + field("fleet", w.transport,
        options(fleets.map((f) => [f.id, `#${f.id} ${armyName(f, p.defs)}`])))) : "";
      return `<section class="military-card"><h4>#${u.id} ${escapeHtml(armyName(u, p.defs))}</h4>` +
        table([w.soldiers, w.status, w.order], [[`${u.soldiers} / ${u.soldiers_full ?? "—"}`,
          statusName(u), orderText(u.order)]]) +
        `<button type="button" data-unit-move="${u.id}"${u.status === "ready" ? "" : " disabled"}>${escapeHtml(w.destination)}</button>` +
        commands + (load ? `<h4>${escapeHtml(w.load)}</h4>${load}` : "") + "</section>";
    });
    const barracks = this.data.bases.some((b) => b.barracks && b.x === tile.x && b.y === tile.y);
    const choices = armyOptions(p.defs, "land");
    if (barracks && choices) parts.push(`<h4>${escapeHtml(w.formUnit)}</h4>` + orderForm("form_unit",
      `<input type="hidden" name="x" value="${tile.x}"><input type="hidden" name="y" value="${tile.y}">` +
      field("army", w.kind, choices)));
    const missile = this.data.bases.find((b) => b.missile_base != null && b.x === tile.x && b.y === tile.y);
    if (missile) parts.push(`<h4>${escapeHtml(w.missile)}</h4>` +
      table([w.stock, w.warheads], [[`${missile.stock} / ${missile.missile_base}`, `${missile.warheads ?? 0} / ${missile.warhead ?? 0}`]]) +
      `<button type="button" data-missile-base="${missile.facility}">${escapeHtml(w.targetMap)}</button>`);
    return parts.length ? `<div class="military-page">${parts.join("")}</div>` : "";
  }

  start(id) {
    const unit = this.data?.units.find((u) => u.id === id && !u.aboard && !u.abroad && u.status === "ready");
    if (!unit) return;
    this.panel.endMarking();
    this.picking = id;
    this.panel.marked.add(`${unit.x},${unit.y}`);
    this.panel.onMarkedChange();
    this.panel.render();
  }

  pickingHtml() {
    const w = words();
    return `<div class="military-picking"><p><strong>#${this.picking}</strong> ${escapeHtml(w.picking)}</p>
      <button type="button" data-military-cancel>${escapeHtml(w.cancel)}</button></div>`;
  }

  choose(tile) {
    const unit = this.picking;
    if (unit == null) return;
    this.panel.endMarking();
    this.panel.addOrder("move_unit", { unit, x: tile.x, y: tile.y });
  }
}
