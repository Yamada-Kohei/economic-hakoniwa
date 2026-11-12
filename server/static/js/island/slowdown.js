// 生産調整中・生産停止中（買い注文不足）のわけと打てる手（W-250・UI-84）。中身はサーバーが組み立てた
// slowdown（server/web/slowdown.py。JSON API・MCP の道具・国の要約と同じもの）で、ここは並べるだけ。

import { escapeHtml } from "./api.js";

/** わけの見出し・目標・わけの箇条・打てる手の HTML。 */
export function slowdownHtml(s) {
  if (!s) return "";
  const title = s.stopped ? "生産を止めたわけ" : "生産調整中のわけ";
  const good = s.good_name ? `（${escapeHtml(s.good_name)}）` : "";
  const items = (s.reasons || []).map((r) => `<li>${escapeHtml(r.text)}</li>`).join("");
  const hint = s.hint ? `<p class="hint"><strong>打てる手:</strong> ${escapeHtml(s.hint)}</p>` : "";
  return `<section class="slowdown"><h4>${title}${good} <small class="muted">操業目標 ${escapeHtml(s.target)}%</small></h4>
    <ul class="slowdown-reasons">${items}</ul>${hint}</section>`;
}
