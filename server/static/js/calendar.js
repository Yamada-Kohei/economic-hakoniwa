// 暦: ある時点のターンを「箱庭暦32年11月（ターン 383）」と書く。暦の名前・最初の年・1年のターン数と文面は、
// サーバーがページに埋め込んだ設定（#hakoniwa-calendar。hakoniwa_core/dates.py と messages の calendar_*）を
// 使い、数え方も dates.py と同じ（ターン1 が最初の年の1月、ターン 0 は暦の始まる前）。
// 設定のないページ（絵の見本のページなど）では「ターン 383」と書く。

let settings;

function load() {
  if (settings === undefined) {
    try {
      const el = document.getElementById("hakoniwa-calendar");
      settings = el ? JSON.parse(el.textContent) : null;
    } catch {
      settings = null;
    }
  }
  return settings;
}

function fill(template, values) {
  return template.replace(/\{(\w+)\}/g, (all, key) => (key in values ? String(values[key]) : all));
}

/** 1年のターン数。設定がなければ null。 */
export function turnsPerYear() {
  return load()?.turns_per_year ?? null;
}

/** ターン turn の { year, month }。暦の始まる前（ターン 0）や、設定のないときは null。 */
export function dateOf(turn) {
  const c = load();
  if (!c || !Number.isInteger(turn) || turn < 1) return null;
  return {
    year: c.first_year + Math.floor((turn - 1) / c.turns_per_year),
    month: ((turn - 1) % c.turns_per_year) + 1,
  };
}

/** ある時点のターンの書き方: 「箱庭暦32年11月（ターン 383）」。設定がなければ「ターン 383」。 */
export function turnDate(turn) {
  if (turn == null) return "";
  const c = load();
  if (!c || !Number.isInteger(turn)) return `ターン ${turn}`;
  const d = dateOf(turn);
  const date = d
    ? fill(c.date, { calendar: c.name, year: d.year, month: d.month })
    : fill(c.before, { calendar: c.name });
  return fill(c.with_turn, { date, turn });
}

/**
 * グラフの横軸の目盛りの文字: 1月のターンは年（「33年」）、ほかは月（「4月」）。full なら年と月
 * （「32年4月」）。暦の始まる前や、設定のないときはターンの数。
 */
export function axisLabel(turn, full = false) {
  const d = dateOf(turn);
  if (!d) return String(turn);
  if (full && d.month !== 1) return `${d.year}年${d.month}月`;
  return d.month === 1 ? `${d.year}年` : `${d.month}月`;
}
