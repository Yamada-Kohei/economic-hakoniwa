// 自国の基地から相手の島の地図へ渡り、押したマスを確認して命令に追加する。
// URL の基地番号は信用せず、自国の軍の API が返した基地だけを使う。
import { escapeHtml, getJson } from "./api.js";
import { words, coord, options, field, numberField, bindOrderForms, appendOrder } from "../military/common.js";

export class MissileTarget {
  constructor(root, defs, orders = null) {
    this.root = root;
    this.defs = defs;
    this.orders = orders;
    this.bases = [];
    this.base = null;
    this.tile = null;
    this.request = 0;
    this.saving = Promise.resolve();
    if (!root) return;
    bindOrderForms(root, (kind, payload) => {
      if (!this.tile || !this.base || payload.base !== this.base.facility) throw new Error("Target unavailable");
      const next = this.saving.then(async () => {
        if (orders) {
          await orders.add(kind, payload);
          return orders.message;
        }
        return appendOrder(root.dataset.orders, kind, payload);
      });
      this.saving = next.catch(() => {});
      return next;
    });
    root.addEventListener("change", (e) => {
      if (e.target.name === "base") {
        this.base = this.bases.find((b) => b.facility === Number(e.target.value));
        this.showStock();
      }
      if (e.target.name === "nuclear") {
        const form = e.target.form;
        form.classList.toggle("military-nuclear", e.target.checked);
        form.querySelector('[type="submit"]').textContent = e.target.checked ? words().nuclearFire : words().fire;
      }
    });
    root.addEventListener("submit", (e) => {
      const form = e.target.closest("form[data-target-map]");
      if (!form) return;
      e.preventDefault();
      if (!form.reportValidity() || !this.base) return;
      const island = Number(form.elements.island.value);
      const url = new URL(root.dataset.islandUrl.replace("/0/", `/${island}/`), location.href);
      url.searchParams.set("missile_base", this.base.facility);
      location.assign(url.href);
    });
    root.addEventListener("click", (e) => {
      if (e.target.closest("[data-missile-cancel]")) this.cancel();
    });
  }

  async start(id, targetMap = false) {
    if (!this.root) return;
    const request = ++this.request;
    this.root.hidden = false;
    const w = words();
    this.root.innerHTML = `<p role="status">${escapeHtml(w.loading)}</p>`;
    try {
      const [{ military }, world] = await Promise.all([
        getJson(this.root.dataset.military), getJson(this.root.dataset.world),
      ]);
      if (request !== this.request) return;
      this.bases = (military?.bases || []).filter((b) => b.missile_base != null);
      this.base = this.bases.find((b) => b.facility === id);
      if (!this.base) throw new Error("Base unavailable");
      const baseChoices = options(this.bases.map((b) => [b.facility,
        `#${b.facility} ${this.defs.facilities?.[b.kind]?.name || b.kind} ${coord(b)}`]))
        .replace(`value="${id}"`, `value="${id}" selected`);
      const header = `<h3>${escapeHtml(w.missile)}</h3><p class="hint">${escapeHtml(w.rangeHint)}</p>`;
      const baseField = field("base", w.facility, baseChoices);
      if (targetMap) {
        this.root.innerHTML = header + `<p>${escapeHtml(w.selectTarget)}</p>` +
          `<form class="actions military-form" data-order="fire_missile">${baseField}
            <p data-base-stock></p><input type="hidden" name="island" value="${Number(this.root.dataset.island)}">
            <p data-target-label>${escapeHtml(w.target)}: —</p>
            <input type="hidden" name="x"><input type="hidden" name="y">
            ${numberField("count", w.count, { min: 1, value: 1 })}
            <label><input type="checkbox" name="nuclear" value="true"> ${escapeHtml(w.nuclear)}</label>
            <p class="military-warning">${escapeHtml(w.nuclearConfirm)}</p>
            <button type="submit" disabled>${escapeHtml(w.fire)}</button><p class="order-notice" role="status"></p></form>`;
      } else {
        const targets = world.islands.filter((i) => !i.mine);
        this.root.innerHTML = header + `<form class="actions military-form" data-target-map>${baseField}
          <p data-base-stock></p>${field("island", w.targetIsland, options(targets.map((i) => [i.id, `${i.name} (#${i.id})`])))}
          <button type="submit"${targets.length ? "" : " disabled"}>${escapeHtml(w.targetMap)}</button></form>`;
      }
      this.root.insertAdjacentHTML("beforeend", `<button type="button" data-missile-cancel>${escapeHtml(w.cancel)}</button>`);
      this.showStock();
      if (targetMap && this.tile) this.select(this.tile);
      this.root.scrollIntoView({ block: "nearest" });
    } catch {
      if (request !== this.request) return;
      this.base = null;
      this.root.innerHTML = `<p class="error" role="alert">${escapeHtml(w.error)}</p>`;
    }
  }

  showStock() {
    const slot = this.root.querySelector("[data-base-stock]");
    if (slot && this.base) slot.textContent = `${words().stock}: ${this.base.stock} / ${this.base.missile_base}、${words().warheads}: ${this.base.warheads ?? 0} / ${this.base.warhead ?? 0}`;
  }

  select(tile) {
    this.tile = tile;
    const form = this.root?.querySelector('form[data-order="fire_missile"]');
    if (!form) return;
    form.elements.x.value = tile?.x ?? "";
    form.elements.y.value = tile?.y ?? "";
    form.querySelector("[data-target-label]").textContent = `${words().target}: ${tile ? coord(tile) : "—"}`;
    form.querySelector('[type="submit"]').disabled = !tile || !!form.dataset.busy;
  }

  cancel() {
    this.request++;
    this.base = null;
    this.tile = null;
    this.root.innerHTML = "";
    this.root.hidden = true;
    const url = new URL(location.href);
    url.searchParams.delete("missile_base");
    history.replaceState(null, "", url);
  }
}
