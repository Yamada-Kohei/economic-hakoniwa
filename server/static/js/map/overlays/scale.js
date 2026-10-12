// 0〜1 の値を、薄い色から濃い色への半透明の色にする。
export function heat(value, hue = 0) {
  const v = Math.max(0, Math.min(1, value));
  return `hsla(${hue}, 85%, ${70 - v * 35}%, ${0.25 + v * 0.55})`;
}

/** 悪い（0）は赤、良い（1）は緑。 */
export function goodBad(value) {
  const v = Math.max(0, Math.min(1, value));
  return `hsla(${v * 120}, 80%, 45%, 0.6)`;
}
