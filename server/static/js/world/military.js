// 世界マップから自国の艦隊に命令を出す。選択中は島へのリンクを止め、海にもキーボードで移動できる。
import { escapeHtml, getJson } from "../island/api.js";
import {
  words, coord, armyName, statusName, orderText, table, options,
  transportForms, bindOrderForms, appendOrder,
  dutyName, fleetDutyForm, goodsText,
} from "../military/common.js";

export async function mountFleetCommands(grid, defsReady, fleetsReady) {
  const root = document.getElementById("world-military");
  if (!root) return;
  const w = words();
  const controls = root.querySelector("[data-fleet-controls]");
  const notice = root.querySelector("[data-fleet-notice]");
  const pickingPanel = root.querySelector("[data-fleet-picking]");
  let picking = null;
  let saving = Promise.resolve();
  // この画面の連続操作も順番に保存する。毎回 GET してから PUT する。
  const add = (kind, payload) => {
    const next = saving.then(() => appendOrder(root.dataset.orders, kind, payload));
    saving = next.catch(() => {});
    return next;
  };
  bindOrderForms(root, add);

  function cancel() {
    picking = null;
    pickingPanel.hidden = true;
    grid.classList.remove("fleet-picking");
    for (const sea of grid.querySelectorAll(".sea")) {
      sea.removeAttribute("tabindex");
      sea.removeAttribute("role");
      sea.removeAttribute("aria-label");
      sea.setAttribute("aria-hidden", "true");
    }
    controls.querySelector("[data-sail]")?.focus();
  }
  root.querySelector("[data-fleet-cancel]").addEventListener("click", cancel);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && picking != null) cancel();
  });
  async function choose(event) {
    if (picking == null) return;
    const cell = event.target.closest(".cell[data-x][data-y]");
    if (!cell || !grid.contains(cell)) return;
    event.preventDefault();
    event.stopPropagation();
    const fleet = picking;
    const x = Number(cell.dataset.x);
    const y = Number(cell.dataset.y);
    cancel();
    notice.textContent = w.loading;
    try {
      notice.textContent = await add("sail_fleet", { fleet, x, y });
    } catch {
      notice.textContent = w.saveError;
    }
  }
  grid.addEventListener("click", choose, true);
  grid.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") choose(event);
  });

  try {
    const [defs, fleets, response] = await Promise.all([
      defsReady, fleetsReady, getJson(root.dataset.military),
    ]);
    const military = { ...response.military, fleets: fleets.mine };
    const selectHtml = options(fleets.mine.map((f) => [f.id, `#${f.id} ${armyName(f, defs)} ${coord(f)}`]));
    if (!fleets.mine.length) {
      controls.innerHTML = `<p class="hint">${escapeHtml(w.empty)}</p>`;
      return;
    }
    controls.innerHTML = `<label>${escapeHtml(w.fleet)} <select data-fleet>${selectHtml}</select></label>
      <div data-fleet-details></div><button type="button" data-sail>${escapeHtml(w.sailDestination)}</button>
      <div data-transport></div>`;
    const select = controls.querySelector("[data-fleet]");
    const selected = () => fleets.mine.find((f) => f.id === Number(select.value));
    function show() {
      if (picking != null) cancel();
      const f = selected();
      controls.querySelector("[data-fleet-details]").innerHTML = table(
        [w.kind, w.ships, w.location, w.docked, w.crew, w.status, w.order, w.duty, w.steps, w.cargo, w.stores, w.capacity],
        [[armyName(f, defs), f.ships, coord(f), f.docked ? w.yes : w.no,
          `${f.soldiers} / ${f.soldiers_full}`, statusName(f), orderText(f.order), dutyName(f), f.steps_left,
          f.capacity > 0 ? f.cargo.join(", ") || w.none : "—", goodsText(f.goods, defs), f.capacity > 0 ? f.capacity : "—"]]);
      controls.querySelector("[data-sail]").disabled = f.status !== "ready";
      controls.querySelector("[data-transport]").innerHTML = fleetDutyForm(f) + transportForms(military, f);
    }
    select.addEventListener("change", show);
    controls.querySelector("[data-sail]").addEventListener("click", () => {
      picking = selected().id;
      pickingPanel.hidden = false;
      root.querySelector("[data-fleet-picking-label]").textContent = `#${picking} ${w.picking}`;
      notice.textContent = "";
      grid.classList.add("fleet-picking");
      for (const sea of grid.querySelectorAll(".sea")) {
        sea.removeAttribute("aria-hidden");
        sea.tabIndex = 0;
        sea.setAttribute("role", "button");
        sea.setAttribute("aria-label", `${w.location} (${sea.dataset.x}, ${sea.dataset.y})`);
      }
      grid.querySelector(`.cell[data-x="${selected().x}"][data-y="${selected().y}"]`)?.focus();
    });
    show();
  } catch {
    controls.innerHTML = `<p class="error">${escapeHtml(w.error)}</p>`;
  }
}
