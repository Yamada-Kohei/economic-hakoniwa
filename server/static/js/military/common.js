// 軍の画面・島・世界マップで共用する表示と、命令の中身を作る小さな関数。
import { escapeHtml, getJson, sendJson } from "../island/api.js";

export const words = () => document.getElementById("military-text")?.dataset || {};
export const coord = (row) => `(${row.x}, ${row.y})`;
export const armyName = (row, defs) => defs.army?.[row.army]?.name || row.name || row.army;
export const statusName = (row) => words()[row.status] || row.status;
export const percent = (value) => `${Math.round(Number(value) * 100)}%`;

/** 空欄を省き、座標選択欄を x・y に展開する。種類だけは文字列で送る。 */
export function payloadOf(entries) {
  const payload = {};
  for (const [key, value] of entries) {
    if (value === "") continue;
    if (key === "cell") {
      const [x, y] = String(value).split(",").map(Number);
      if (!Number.isInteger(x) || !Number.isInteger(y) || x < 0 || y < 0) {
        throw new Error("Invalid coordinate");
      }
      Object.assign(payload, { x, y });
    } else if (key === "army") payload[key] = String(value);
    else {
      const number = Number(value);
      if (!Number.isFinite(number)) throw new Error("Invalid number");
      payload[key] = number;
    }
  }
  return payload;
}

export function landAt(military, tile) {
  return (military.units || []).filter((u) => !u.aboard && !u.abroad && u.x === tile.x && u.y === tile.y);
}
export function portsOf(military) {
  return (military.bases || []).filter((b) => b.naval_base);
}
export function loadableUnits(military) {
  return (military.units || []).filter((u) => !u.aboard && !u.abroad &&
    portsOf(military).some((b) => b.x === u.x && b.y === u.y));
}
export function transports(military) {
  return (military.fleets || []).filter((f) => f.docked && f.capacity > 0);
}

export function options(rows) {
  return rows.map(([value, label]) => `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`).join("");
}
export function armyOptions(defs, arm) {
  return options(Object.entries(defs.army || {}).filter(([, d]) => d.arm === arm)
    .map(([id, d]) => [id, d.name || id]));
}
export function field(name, label, choices) {
  return `<label>${escapeHtml(label)} <select name="${name}" required>${choices}</select></label>`;
}
export function orderForm(kind, fields) {
  const w = words();
  return `<form class="actions military-form" data-order="${kind}">${fields}
    <button type="submit">${escapeHtml(w.add)}</button><p class="order-notice" role="status"></p></form>`;
}
export function equipmentText(row, defs) {
  const keys = new Set([...Object.keys(row.equipment || {}), ...Object.keys(row.equipment_full || {})]);
  return [...keys].map((key) => `${defs.goods?.[key]?.name || key}: ${row.equipment?.[key] ?? 0}` +
    (row.equipment_full?.[key] != null ? ` / ${row.equipment_full[key]}` : "")).join("、") || words().none;
}
export function orderText(order) {
  if (!order) return words().none;
  const w = words();
  const label = w[order.type] || order.type;
  if (order.x != null && order.y != null) return `${label} → ${coord(order)}`;
  if (order.left != null) return `${label}（${w.remaining}: ${order.left}）`;
  return label;
}
/** 呼び出し側は通常の値を渡す。リンクを含むセルだけ {html} を使う。 */
export function table(headings, rows) {
  const cell = (v) => v && typeof v === "object" && "html" in v ? v.html : escapeHtml(v);
  return `<div class="table-scroll"><table class="military-table"><thead><tr>${headings.map((h) =>
    `<th scope="col">${escapeHtml(h)}</th>`).join("")}</tr></thead><tbody>${rows.map((row) =>
    `<tr>${row.map((v) => `<td>${cell(v)}</td>`).join("")}</tr>`).join("") ||
    `<tr><td colspan="${headings.length}">${escapeHtml(words().empty)}</td></tr>`}</tbody></table></div>`;
}
export function transportForms(military, fleet) {
  if (!fleet.docked || fleet.capacity <= 0) return "";
  const w = words();
  const fixed = `<input type="hidden" name="fleet" value="${fleet.id}">`;
  const units = loadableUnits(military);
  const cargo = (military.units || []).filter((u) => u.aboard === fleet.id && fleet.cargo.includes(u.id));
  let html = "";
  if (units.length) html += `<h4>${escapeHtml(w.load)}</h4>` + orderForm("load_unit", fixed +
    field("unit", w.unit, options(units.map((u) => [u.id, `#${u.id} ${u.name || u.army} ${coord(u)}`]))));
  if (cargo.length && portsOf(military).length) html += `<h4>${escapeHtml(w.unload)}</h4>` +
    orderForm("unload_unit", fixed + field("unit", w.unit, options(cargo.map((u) => [u.id, `#${u.id} ${u.name || u.army}`]))) +
      field("cell", w.port, options(portsOf(military).map((b) => [`${b.x},${b.y}`, coord(b)]))));
  return html;
}

/** 動的に作った form[data-order] も拾い、送信中の二重操作を防ぐ。 */
export function bindOrderForms(root, add) {
  root.addEventListener("submit", async (event) => {
    const form = event.target.closest("form[data-order]");
    if (!form) return;
    event.preventDefault();
    if (form.dataset.busy || !form.reportValidity()) return;
    if (form.dataset.confirm && !confirm(form.dataset.confirm)) return;
    const notice = form.querySelector(".order-notice");
    form.dataset.busy = "true";
    const button = form.querySelector('[type="submit"]');
    if (button) button.disabled = true;
    try {
      const payload = payloadOf(new FormData(form));
      if (!Object.keys(payload).length) {
        if (notice) notice.textContent = words().emptyInput;
        return;
      }
      const message = await add(form.dataset.order, payload);
      if (notice) notice.textContent = message;
    } catch {
      if (notice) notice.textContent = words().saveError;
    } finally {
      delete form.dataset.busy;
      if (button) button.disabled = false;
    }
  });
}

/** 世界マップでは読み直しに失敗したら保存しない。手元の古い列で上書きしないため。 */
export async function appendOrder(url, kind, payload) {
  const current = await getJson(url);
  const orders = current.orders.map(({ kind, payload }) => ({ kind, payload }));
  const index = orders.length;
  orders.push({ kind, payload });
  const result = await sendJson(url, "PUT", { orders });
  if (!result.ok) {
    return result.data.error || result.data.errors?.map((e) => e.text).join(" ") || words().saveError;
  }
  const warning = result.data.warnings?.find((w) => w.index === index);
  return `${words().added}${warning ? ` ${warning.text}` : ""}`;
}
