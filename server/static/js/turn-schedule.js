// 予定時刻を過ぎても、更新が始まったとは推測しない。
function refreshSchedules() {
  for (const el of document.querySelectorAll(".turn-schedule[data-next]")) {
    if (el.dataset.paused === "1" || el.dataset.updating || !el.dataset.next) continue;
    const next = Date.parse(el.dataset.next);
    if (!Number.isFinite(next)) continue;
    const late = Date.now() > next;
    el.classList.toggle("error", late);
    if (late) el.textContent = `更新予定（${new Date(next).toLocaleString("ja-JP")}）を過ぎています。更新の遅れ、または表示後に予定が変わった可能性があります。画面を読み直してください。`;
  }
}
refreshSchedules();
setInterval(refreshSchedules, 30000);
