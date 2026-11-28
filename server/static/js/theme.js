// 画面の明暗の枠（台帳 P2 の 13）: html の data-theme 属性と、共通の色の変数
// （--bg-base・--text-base・--line-base。site.css）の土台だけを置く。
// 既定では OS に合わせない（明暗の両方の表示を確かめるまで切。会議 #5286 の締め）。
// ブラウザに「OS に合わせる」と覚えたときだけ、OS の明暗に合わせて属性を付ける。
// 全画面の配色を変数へ寄せるのは、この枠の上に別の枝で広げる。

export const THEME_KEY = "hakoniwa:theme";

/** 明暗の合わせ方。覚えていなければ null（合わせない）。 */
export function readThemePref(storage) {
  try {
    return storage?.getItem(THEME_KEY) === "os" ? "os" : null;
  } catch {
    return null;
  }
}

/** 合わせ方を覚える。覚えられなくても止まらない。 */
export function writeThemePref(storage, pref) {
  try {
    storage?.setItem(THEME_KEY, pref === "os" ? "os" : "");
  } catch {
    // プライベートの窓など
  }
}

/** 合わせ方と OS の明暗から、data-theme に付ける値。付けないときは null。 */
export function themeForPref(pref, osDark) {
  if (pref !== "os") return null;
  return osDark ? "dark" : "light";
}

/** data-theme を付ける（付けないときは外す）。付けた値を返す。 */
export function applyTheme(doc, storage, osDark) {
  const value = themeForPref(readThemePref(storage), osDark);
  if (value) doc.documentElement.setAttribute("data-theme", value);
  else doc.documentElement.removeAttribute("data-theme");
  return value;
}

if (typeof document !== "undefined") {
  let storage = null;
  try {
    storage = window.localStorage;
  } catch {
    storage = null; // 覚えられない窓では合わせない
  }
  const query =
    typeof window !== "undefined" && window.matchMedia
      ? window.matchMedia("(prefers-color-scheme: dark)")
      : null;
  applyTheme(document, storage, Boolean(query?.matches));
  query?.addEventListener?.("change", (e) => applyTheme(document, storage, e.matches));
}
