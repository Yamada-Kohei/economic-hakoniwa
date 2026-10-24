for (const label of document.querySelectorAll("[data-ranking-count]")) {
  const root = label.closest("section");
  const compared = root.querySelectorAll('[data-ranked="1"]').length;
  const excluded = root.querySelectorAll('[data-ranked="0"]').length;
  label.textContent = `比べた国: ${compared}、換算できない国: ${excluded}（下の表に表示）。統計がまだない国は表に含みません。`;
}
