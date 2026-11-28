// 地図の動きと表示の土台（グラフィック強化 第2波）:
// - 煙（案1）: 稼働中の工場・火力発電所の煙突から、煙がもくもくと湧き出して
//   昇っていく。止まると出ない。マスごとに位相をずらし、描き直しの流れ
//   （main.js の駆動）で動かす。色は湯気に見えないよう一段濃い灰色にする。
// - 変化マスの光（案3 の動き）: 初回表示で一度だけ、枠が広がりながら光る。
// - 軽量モード（案47）: 動きを止め、煙は静止した姿で出す。スマホの描画と電池の
//   負荷を抑えるための切り替えで、既定は画面の幅で決める（幅760px以下では入、
//   PCでは切。#6050 の確定仕様）。利用者が切り替えたら、その選択をブラウザに覚える。
// - ズーム別の詳しさ（案46）: 遠くでは飾り（煙の粒・変化の記号）を減らし、近くで
//   だけ細かく描く。色だけに頼らないよう、煙は無彩色にし、変化の印は記号と色の
//   併用のままにする（changes.js の CHANGE_COLORS と凡例が正）。
// ここは決まりと材料だけを持ち、描くのは renderer.js、駆動は main.js。

export const SMOKE_KEY = "hakoniwa:map-smoke";
export const LIGHT_KEY = "hakoniwa:map-light";

/** 煙を出すか。覚えていなければ出す（既定）。 */
export function readSmokeShown(storage) {
  try {
    return storage?.getItem(SMOKE_KEY) !== "off";
  } catch {
    return true;
  }
}

/** 煙を出すかどうかを覚える。 */
export function writeSmokeShown(storage, shown) {
  try {
    storage?.setItem(SMOKE_KEY, shown ? "on" : "off");
  } catch {
    // プライベートの窓など
  }
}

/**
 * 軽量モードか。利用者が切り替えたことがあればその選択、なければ既定
 * （defaultOn: 画面が狭い（幅760px以下）ときだけ入。#6050 の確定仕様）。
 */
export function readLightMode(storage, defaultOn) {
  try {
    const value = storage?.getItem(LIGHT_KEY);
    if (value === "on") return true;
    if (value === "off") return false;
  } catch {
    // 読めないときは下の既定に倒す
  }
  return Boolean(defaultOn);
}

/** 軽量モードかどうかを覚える。 */
export function writeLightMode(storage, on) {
  try {
    storage?.setItem(LIGHT_KEY, on ? "on" : "off");
  } catch {
    // プライベートの窓など
  }
}

/**
 * マスごとの位相（0〜1）。座標の整数の混ぜ合わせで決め、乱数は使わない
 * （地形の差分絵の選び方と同じ流儀。同じマスはいつも同じ位相）。
 */
export function phaseFor(x, y) {
  const hash = ((x * 73856093) ^ (y * 19349663) ^ 0x9e3779b9) >>> 0;
  return (hash % 1024) / 1024;
}

/**
 * 煙の出る施設か: 建っていて、止まっておらず、工場（産業の施設）か火力発電所。
 * 止まりかはサーバーが決めた f.stopped を読む（理由の表はここに写さない。W-34）。
 */
export function isSmokeSource(f, defs) {
  if (!f?.built || f.stopped) return false;
  return defs?.facilities?.[f.kind]?.category === "industry" || f.kind === "thermal_power_plant";
}

/** ズーム別の詳しさ。scale は地図の拡大率（controls.js の view.scale）。 */
export function detailLevel(scale) {
  if (scale >= 0.9) return "near";
  if (scale >= 0.5) return "mid";
  return "far";
}

export const SMOKE_CYCLE = 4.8; // 煙が1巡する秒数
export const SMOKE_PEAK_ALPHA = 0.78; // 煙の粒の濃さの上限（renderer が段の絵を作るときの物差し）
const PUFFS = { near: 6, mid: 4, far: 2 };

/** 煙の色（RGB の並び）。湯気に見えないよう、薄い青白でなく一段濃い灰色。 */
export const SMOKE_RGB = "105, 116, 125";

/**
 * 煙の粒の並び。t はアニメの時刻（秒）。軽量モードでは t に位相ぶんだけ進めた
 * 時刻を渡し、静止した姿にする。返すのは建物のてっぺんからの相対位置
 * （dx・dy・r はマスの幅・高さに対する割合）と濃さ。rise は 0〜1 で、
 * 煙突から連続して湧き出し、昇るほど少し広がって薄れていく（もくもく感）。
 * 粒は出発点では小さく（直径 6〜9px 級。#6090 の決め）、昇るにつれて広がる。
 * 昇りの速さは出発点で速く上ではゆっくり（湧き出してから漂う感じ）。
 * 横の揺れも位相で決まる決め打ちで、乱数は使わない。
 */
export function smokePuffs(f, t, detail) {
  const count = PUFFS[detail] ?? 2;
  const phase = phaseFor(f.x, f.y);
  const sway = phase - 0.5; // この煙突の横流れの向きと強さ（-0.5〜0.5）
  const puffs = [];
  for (let k = 0; k < count; k++) {
    const u = (((t / SMOKE_CYCLE + phase + k / count) % 1) + 1) % 1;
    const rise = 1 - (1 - u) * (1 - u); // 出発は速く、上ではゆっくり
    puffs.push({
      rise,
      dx: 0.03 * phase + 0.22 * rise * rise + 0.05 * sway * Math.sin(rise * 5 + phase * 6.28), // 風で流れて少し揺れる
      dy: -(0.25 + 1.5 * rise), // てっぺんから昇る
      r: 0.05 + 0.17 * rise * rise, // 出発は小さく、昇るほどもくもく広がる
      alpha: SMOKE_PEAK_ALPHA * Math.pow(Math.sin(Math.PI * Math.min(1, 0.12 + 0.88 * u)), 1.4), // 湧き出しからしっかり濃く、消え際だけ薄く
    });
  }
  return puffs;
}

export const GLOW_SECONDS = 2.4; // 変化マスの光が続く秒数

/** 変化マスの光の強さ（0〜1）。一度だけ、強くなってから消える。 */
export function glowAlpha(elapsed) {
  if (!(elapsed >= 0) || elapsed >= GLOW_SECONDS) return 0;
  const u = elapsed / GLOW_SECONDS;
  return Math.sin(Math.PI * Math.min(1, u * 1.15)) * (1 - u * 0.55);
}
