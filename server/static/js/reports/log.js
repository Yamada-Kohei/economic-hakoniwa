import { getJson } from "../island/api.js";
import { turnDate } from "../calendar.js";

const form = document.getElementById("log-filter");
const output = document.getElementById("filtered-log");
const status = form.querySelector("[data-status]");
const fields = form.elements;
const params = new URLSearchParams(location.search);
const turn = Number(form.dataset.turn);
fields.from.value = params.get("from") ?? Math.max(0, turn - 23);
fields.to.value = params.get("to") ?? turn;
fields.x.value = params.get("x") ?? "";
fields.y.value = params.get("y") ?? "";
let entries = [];
let request = 0;
let loaded = false;
let loadedRange = [];

function render() {
  const rows = entries.filter((e) => (!fields.kind.value || e.kind === fields.kind.value) &&
    (fields.x.value === "" || (e.tile?.[0] === fields.x.valueAsNumber && e.tile?.[1] === fields.y.valueAsNumber)));
  output.replaceChildren();
  const groups = new Map();
  for (const entry of [...rows].reverse()) {
    if (!groups.has(entry.turn)) groups.set(entry.turn, []);
    groups.get(entry.turn).push(entry);
  }
  for (const [t, rows] of groups) {
    const section = document.createElement("section");
    section.className = "panel";
    const title = document.createElement("h2");
    title.textContent = turnDate(t);
    const ul = document.createElement("ul");
    ul.className = "log";
    for (const entry of rows.reverse()) {
      const li = document.createElement("li");
      if (entry.tile) {
        const link = document.createElement("a");
        link.href = `${form.dataset.islandUrl}?${new URLSearchParams({ x: entry.tile[0], y: entry.tile[1] })}`;
        link.textContent = entry.text;
        li.append(link);
      } else li.textContent = entry.text;
      ul.append(li);
    }
    section.append(title, ul);
    output.append(section);
  }
  status.textContent = `${loadedRange[0]}〜${loadedRange[1]} ターン: ${rows.length} / ${entries.length} 件${rows.length ? "" : "（該当する出来事はありません）"}`;
}

async function load() {
  if (!form.reportValidity()) return;
  const start = fields.from.valueAsNumber;
  const end = fields.to.valueAsNumber;
  if (end < start || end - start >= 24 || (fields.x.value === "") !== (fields.y.value === "")) {
    status.textContent = "範囲は最初≦最後の24ターン以内、施設のマスは X と Y の両方を指定してください。";
    return;
  }
  const serial = ++request;
  const oldKind = fields.kind.value || params.get("kind") || "";
  status.textContent = "読み込み中…";
  try {
    const data = await getJson(`${form.dataset.url}?${new URLSearchParams({ from: start, to: end })}`);
    if (serial !== request) return;
    entries = data.entries;
    loadedRange = [start, end];
    loaded = true;
    fields.kind.replaceChildren(new Option("すべて", ""));
    const kinds = new Map(entries.map((e) => [e.kind, e.text]));
    for (const [kind, text] of kinds) fields.kind.add(new Option(text, kind));
    fields.kind.value = kinds.has(oldKind) ? oldKind : "";
    output.hidden = false;
    document.getElementById("paged-log").hidden = true;
    render();
    const query = new URLSearchParams({ from: start, to: end });
    for (const key of ["kind", "x", "y"]) if (fields[key].value !== "") query.set(key, fields[key].value);
    history.replaceState(null, "", `?${query}`);
  } catch {
    if (serial === request) status.textContent = "読み込めませんでした。直前の表示はそのままです。範囲を確かめて読み直してください。";
  }
}
form.addEventListener("submit", (event) => { event.preventDefault(); load(); });
fields.kind.addEventListener("change", () => { if (loaded) render(); });
if (params.has("from") || params.has("to")) load();
