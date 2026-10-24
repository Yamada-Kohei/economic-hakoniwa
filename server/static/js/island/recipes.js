// 生産方式の効果は施設の定義から表示する。一般の労働者と教員の倍率を区別する。
export const recipeWords = () => document.getElementById("recipe-text")?.dataset || {};

export function recipeLabel(id, recipe = {}, name = recipe.name || id) {
  const w = recipeWords();
  const parts = [name];
  if ((recipe.labor ?? 1) < 1) parts.push(w.labor.replace("{count}", Math.round(recipe.labor * 100) / 10));
  if (id === "digital" && recipe.education_scale != null) {
    parts.push(w.education.replace("{count}", Math.round((recipe.education_scale - 1) * 1000) / 10));
  }
  return parts.join("・");
}
