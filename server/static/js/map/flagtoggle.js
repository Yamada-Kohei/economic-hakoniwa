// 首都の旗（案23）の表示の切り替え（グラフィック強化 案44 の旗の分）。
// 旗だけの先行 PR として分けた枝に入れる。出すかどうかはブラウザに覚える
// （島によらず1つ。覚えられない窓では毎回出す＝今までの表示）。

export const FLAG_KEY = "hakoniwa:map-flag";

/** 首都の旗を出すか。覚えていなければ出す（今までの表示）。 */
export function readFlagShown(storage) {
  try {
    return storage?.getItem(FLAG_KEY) !== "off";
  } catch {
    return true;
  }
}

/** 旗を出すかどうかを覚える。覚えられなくても表示は切り替える。 */
export function writeFlagShown(storage, shown) {
  try {
    storage?.setItem(FLAG_KEY, shown ? "on" : "off");
  } catch {
    // プライベートの窓など
  }
}
