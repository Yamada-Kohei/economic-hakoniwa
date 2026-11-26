// 第4波の残り（案21・13・14・18・15・26）の、数から決める部分と表示の切り替え。
// どれも「数から描く」ための物差しだけを持ち、絵そのものは renderer.js が描く。
// 新しい絵は描かず、元の絵への効果（色を重ねる）と印（点・線）で表す
// （本人の決め 2026-11-26: 絵が変わる版は新しく描かず、そのマスだけ効果をかける形）。

// --- 案21 時代で変わる街並み -------------------------------------------------
// 住宅の段階（settlements）が、そのまま時代の進み方になっている: 村は木、町は
// 木と建材、都市は建材と鋼鉄、高層は鋼鉄と機械（settlements.yaml の materials）。
// 段階ごとに、元の絵へ重ねる色（source-atop）を変えて、木造→レンガ→
// コンクリート→高層の移り変わしが一目で分かるようにする。材料のない段階
// （定義に無い名前）は null（元の絵のまま）。

export const ERA_TINTS = {
  village: "rgba(122, 82, 40, 0.30)", // 木造: 暖かい茶
  town: "rgba(150, 64, 38, 0.28)", // レンガ: 赤茶
  city: "rgba(105, 110, 118, 0.25)", // コンクリート: 無彩色の灰
  metropolis: "rgba(70, 92, 122, 0.26)", // 近代: 冷たい青灰
  high_rise: "rgba(96, 118, 140, 0.22)", // 高層: 明るい鋼の色
  skyscraper: "rgba(120, 150, 175, 0.20)", // 超高層: ガラス寄りの淡い青
};

/** 住宅の段階 → 重ねる色。定義に無い段階・材料なしは null。 */
export function eraTint(level) {
  return ERA_TINTS[level] || null;
}

// 絵のない住宅（色の箱）の時代の色: 帯の目標色へ寄せる割合（作柄の箱と同じ考え方）。
export const ERA_BOX_MIX = {
  village: ["#8a6238", 0.45],
  town: ["#9c5138", 0.42],
  city: ["#8d9299", 0.38],
  metropolis: ["#66788e", 0.40],
  high_rise: ["#7d93a8", 0.36],
  skyscraper: ["#93aec4", 0.34],
};

// --- 案13 暮らしの気配 -------------------------------------------------------
// 住宅地の様子を数から決める。材料はマスの人口と、その段階に住める上限
// （settlements の capacity）。人影の数は、満ち具合の3段。
// 失業の多い地区・物価で変わる行列は、マスごとの材料が無いのでここでは扱わない
// （台帳への問いとして残す。人口と段階から決まる分だけを描く）。

export const LIFE_FULL = 0.67; // これ以上満ちていれば人影 3 つ
export const LIFE_SOME = 0.34; // これ以上で人影 2 つ、1 人でも住めば 1 つ

/** 人影の数（0〜3）。人口が無い・上限が読めないときは 0。 */
export function lifeDots(population, capacity) {
  if (!population || !capacity) return 0;
  const r = population / capacity;
  if (r >= LIFE_FULL) return 3;
  if (r >= LIFE_SOME) return 2;
  return 1;
}

/** 洗濯物を出す住宅か: 段が高い（豊かな）地区だけ。都市（city）以上。 */
export const LAUNDRY_LEVELS = new Set(["city", "metropolis", "high_rise", "skyscraper"]);

export function lifeLaundry(level) {
  return LAUNDRY_LEVELS.has(level);
}

// --- 案14 繁栄の可視化 -------------------------------------------------------
// 材料はマスごとの幸福度（地図の材料 happiness。0〜100、隠れたマスは null）。
// 高い住宅地は暖かい色を重ね、花の印を付ける。地価はマスごとの材料が無いので
// 扱わない（幸福度だけから描く）。

export const PROSPERITY_WARM = 70; // これ以上で暖色を重ねる
export const PROSPERITY_RICH = 85; // これ以上で花の印も付ける

/** 幸福度 → "warm"（暖色）・"rich"（暖色＋花）。材料なし・届かなければ null。 */
export function prosperityBand(happiness) {
  if (typeof happiness !== "number") return null;
  if (happiness >= PROSPERITY_RICH) return "rich";
  if (happiness >= PROSPERITY_WARM) return "warm";
  return null;
}

export const PROSPERITY_TINTS = {
  warm: "rgba(216, 164, 44, 0.22)",
  rich: "rgba(226, 158, 30, 0.34)",
};

// --- 案18 季節・気候に連動した色調 -------------------------------------------
// 1 ターン = 1 か月（hakoniwa_core/dates.py）。月はターンから、気候帯は島の
// 材料（climate。寒冷 cold・温帯 temperate・熱帯 tropical）から決める。
// 季節の色は地形の上面に薄く重ねるだけで、建物の絵にはかけない。

export function monthOfTurn(turn) {
  if (!turn || turn < 1) return 1;
  return ((turn - 1) % 12) + 1;
}

const SEASONS = {
  temperate: {
    snow: [12, 1, 2], spring: [3, 4, 5], summer: [6, 7, 8], autumn: [9, 10, 11],
  },
  cold: {
    snow: [11, 12, 1, 2, 3], spring: [4, 5], summer: [6, 7, 8], autumn: [9, 10],
  },
  tropical: {
    rainy: [5, 6, 7, 8, 9, 10], dry: [11, 12, 1, 2, 3, 4],
  },
};

/** 月と気候帯 → 季節の名前。材料が読めなければ null（色を変えない）。 */
export function seasonOf(month, climate) {
  const table = SEASONS[climate];
  if (!table) return null;
  for (const [name, months] of Object.entries(table)) {
    if (months.includes(month)) return name;
  }
  return null;
}

// 季節ごとの色（地形の上面に重ねる）。snow は陸に厚く、海の氷は寒冷の雪のときだけ。
export const SEASON_TINTS = {
  snow: { land: "rgba(235, 242, 250, 0.55)", sea: "rgba(200, 220, 240, 0.35)" },
  spring: { land: "rgba(120, 200, 110, 0.18)", sea: null },
  summer: { land: "rgba(40, 140, 60, 0.20)", sea: null },
  autumn: { land: "rgba(200, 130, 40, 0.22)", sea: null },
  rainy: { land: "rgba(70, 100, 130, 0.22)", sea: "rgba(70, 100, 130, 0.12)" },
  dry: { land: "rgba(230, 200, 120, 0.15)", sea: null },
};

/** 季節 → その土地に重ねる色。海の氷は寒冷の雪だけ（seasonOf が cold の snow のとき）。 */
export function seasonTint(season, land, climate) {
  const tints = SEASON_TINTS[season];
  if (!tints) return null;
  if (land) return tints.land;
  if (season === "snow") return climate === "cold" ? tints.sea : null;
  return tints.sea;
}

/** 材料の並び（[{x, y, kind, age}]）→ "x,y" → 印 の Map（renderer の材料）。
 * 同じマスに複数あれば後のものが残る（サーバー側で並べた順。お金の出来事では
 * crowd が bank より後に来るので、peg が崩れたときは行列より集まりが残る）。 */
export function marksByCell(rows) {
  const out = new Map();
  for (const r of rows || []) out.set(`${r.x},${r.y}`, { kind: r.kind, age: r.age });
  return out;
}

// --- 表示の切り替え（ブラウザに覚える） --------------------------------------
// 流儀は変わったマス・旗と同じ。季節だけは「世界の今の姿」なので既定で出す。

function readShown(storage, key, defaultOn) {
  try {
    const v = storage?.getItem(key);
    return defaultOn ? v !== "off" : v === "on";
  } catch {
    return defaultOn;
  }
}

function writeShown(storage, key, shown) {
  try {
    storage?.setItem(key, shown ? "on" : "off");
  } catch {
    // プライベートの窓など
  }
}

export const LIFE_KEY = "hakoniwa:map-life";
export const readLifeShown = (s) => readShown(s, LIFE_KEY, false);
export const writeLifeShown = (s, v) => writeShown(s, LIFE_KEY, v);

export const PROSPERITY_KEY = "hakoniwa:map-prosperity";
export const readProsperityShown = (s) => readShown(s, PROSPERITY_KEY, false);
export const writeProsperityShown = (s, v) => writeShown(s, PROSPERITY_KEY, v);

export const SEASON_KEY = "hakoniwa:map-season";
export const readSeasonShown = (s) => readShown(s, SEASON_KEY, true);
export const writeSeasonShown = (s, v) => writeShown(s, SEASON_KEY, v);

export const BATTLES_KEY = "hakoniwa:map-battles";
export const readBattlesShown = (s) => readShown(s, BATTLES_KEY, false);
export const writeBattlesShown = (s, v) => writeShown(s, BATTLES_KEY, v);

export const MONEY_KEY = "hakoniwa:map-money";
export const readMoneyShown = (s) => readShown(s, MONEY_KEY, false);
export const writeMoneyShown = (s, v) => writeShown(s, MONEY_KEY, v);
