// 軍の画面。テンプレートに渡された範囲だけを表示し、軍の API で補完しない。
import { mountOrders } from "./orders.js";
import { escapeHtml, getJson } from "../island/api.js";
import {
  words, coord, armyName, statusName, percent, equipmentText, orderText, table,
  armyOptions, options, field, orderForm, transports, transportForms, bindOrderForms,
  dutyName, goodsText, fleetDutyForm,
} from "../military/common.js";

const root = document.getElementById("military-page");
const military = JSON.parse(document.getElementById("military-data").textContent);
const ordersReady = mountOrders();
// フォームは defs の読み込み前にも通常のページ送信をさせない。
bindOrderForms(root, async (kind, payload) => {
  const orders = await ordersReady;
  if (!orders) throw new Error("Orders unavailable");
  await orders.add(kind, payload);
  return orders.message;
});

const w = words();
for (const el of root.querySelectorAll("[data-percent]")) el.textContent = percent(el.dataset.percent);

function mapLink(row) {
  const url = row.island ? root.dataset.islandUrl.replace("/0/", `/${row.island}/`) : root.dataset.map;
  return { html: `<a href="${escapeHtml(url)}?x=${row.x}&amp;y=${row.y}">${escapeHtml(coord(row))}</a>` };
}
function showTables(defs) {
  const set = (name, headings, rows) => {
    const slot = root.querySelector(`[data-table="${name}"]`);
    if (slot) slot.innerHTML = table(headings, rows);
  };
  set("units", [w.id, w.kind, w.island, w.location, w.soldiers, w.equipment, w.status, w.order, w.aboard,
    w.role, w.fought, w.stores, w.hungry, w.requisition],
    (military.units || []).map((u) => [u.id, armyName(u, defs), `${u.island ?? "—"}${u.abroad ? ` (${w.abroad})` : ""}`, u.aboard ? "—" : mapLink(u),
      `${u.soldiers} / ${u.soldiers_full ?? "—"}`, equipmentText(u, defs), statusName(u),
      orderText(u.order), u.aboard ?? w.none, w[u.role] || w.none, u.fought ?? 0, goodsText(u.stores, defs),
      u.hungry ? w.yes : w.no, u.requisition ? w.yes : w.no]));
  set("fleets", [w.id, w.kind, w.ships, w.location, w.docked, w.crew, w.status, w.order, w.duty, w.steps, w.cargo, w.stores, w.capacity],
    (military.fleets || []).map((f) => [f.id, armyName(f, defs), f.ships, coord(f),
      f.docked ? w.yes : w.no, `${f.soldiers} / ${f.soldiers_full}`, statusName(f),
      orderText(f.order), dutyName(f), f.steps_left, f.capacity > 0 ? f.cargo.join(", ") || w.none : "—", goodsText(f.goods, defs),
      f.capacity > 0 ? f.capacity : "—"]));
  set("seen", [w.id, w.kind, w.location, w.status, w.turn],
    (military.fleets || []).map((f) => [f.id, armyName(f, defs), coord(f),
      f.current ? w.current : w.previous, f.turn]));
  set("bases", [w.facility, w.kind, w.location, w.details, w.stock, w.warheads], (military.bases || []).map((b) => {
    const details = [];
    for (const key of ["barracks", "radar", "fortress"]) if (b[key] != null) details.push(`${w[key]}: ${b[key]}`);
    if (b.naval_base) details.push(w.navalBase);
    return [b.facility, defs.facilities?.[b.kind]?.name || b.kind, mapLink(b), details.join("、"),
      b.stock != null ? `${b.stock} / ${b.missile_base ?? b.air_defense}` : "—",
      b.warhead != null ? `${b.warheads ?? 0} / ${b.warhead}` : "—"];
  }));
  set("reserve", [w.good, w.tons], Object.entries(military.reserve || {}).map(([g, q]) => [defs.goods?.[g]?.name || w[g] || g, q]));
  set("occupied", [w.location, w.owner, w.since], (military.occupied || []).map((t) => [mapLink(t), t.by === -1 ? w.rebels : t.by, t.turn]));
  set("occupying", [w.island, w.location, w.since], (military.occupying || []).map((t) => [t.island, mapLink(t), t.turn]));
  set("prisoners", [w.owner, w.people], Object.entries(military.prisoners?.by_island || {}).map(([i, n]) => [i, n]));
  set("enemy", [w.id, w.owner, w.kind, w.location, w.status, w.turn], (military.enemy_units || []).map((u) =>
    [u.id, u.owner, armyName(u, defs), mapLink(u), u.current ? w.current : w.previous, u.turn]));
}

try {
  const orders = await ordersReady;
  const defs = orders?.defs || await getJson(root.dataset.defs);
  showTables(defs);
  for (const select of root.querySelectorAll("select[data-arm]")) {
    select.innerHTML = armyOptions(defs, select.dataset.arm);
  }
  for (const select of root.querySelectorAll("select[data-axis]")) {
    select.innerHTML = options(Object.entries(defs.axes?.[select.dataset.axis]?.choices || {}));
  }
  for (const form of root.querySelectorAll("form[data-order]")) {
    const missing = [...form.querySelectorAll("select[required]")].some((s) => !s.options.length);
    form.querySelector('[type="submit"]').disabled = missing;
  }
  const units = (military.units || []).filter((u) => !u.aboard);
  const slot = document.getElementById("unit-commands");
  if (slot) slot.innerHTML = (units.length ? ["dig_trench", "stop_unit", "disband_unit"].map((kind) => {
    const label = { dig_trench: w.dig, stop_unit: w.stop, disband_unit: w.disband }[kind];
    const html = orderForm(kind, `<h3>${escapeHtml(label)}</h3>` + field("unit", w.unit,
      options(units.map((u) => [u.id, `#${u.id} ${armyName(u, defs)} ${coord(u)}`]))));
    return kind === "disband_unit" ? html.replace('data-order="disband_unit"',
      `data-order="disband_unit" data-confirm="${escapeHtml(w.confirm)}"`) : html;
  }).join("") : `<p class="hint">${escapeHtml(w.empty)}</p>`) + ((military.units || []).length ?
    `<h3>${escapeHtml(w.requisition)}</h3>` + orderForm("set_requisition",
      field("unit", w.unit, options(military.units.map((u) => [u.id, `#${u.id} ${armyName(u, defs)}`]))) +
      field("enabled", w.requisition, options([["true", w.yes], ["false", w.no]]))) : "");
  const cargoSlot = document.getElementById("transport-commands");
  if (cargoSlot) cargoSlot.innerHTML = transports(military).map((f) =>
    `<div class="military-card"><h3>#${f.id} ${escapeHtml(armyName(f, defs))}</h3>${transportForms(military, f)}</div>`
  ).join("") || `<p class="hint">${escapeHtml(w.empty)}</p>`;
  const dutySlot = document.getElementById("fleet-duty-commands");
  if (dutySlot) dutySlot.innerHTML = (military.fleets || []).map((f) =>
    `<div class="military-card"><h3>#${f.id} ${escapeHtml(armyName(f, defs))}</h3>${fleetDutyForm(f)}</div>`
  ).join("") || `<p class="hint">${escapeHtml(w.empty)}</p>`;
} catch {
  // 定義を読めなくても、文脈にある種類の名前と数は残す。
  showTables({});
  const notice = document.createElement("p");
  notice.className = "error";
  notice.textContent = w.error;
  root.prepend(notice);
}
