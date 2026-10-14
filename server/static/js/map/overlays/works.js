// 工事中: インフラの工事の列に並んだマス（橙）と、建設中の施設のマス（赤）。
export default {
  id: "works",
  label: "工事中",
  legend: "橙はインフラの工事の列に並んだマス、赤は建設中の施設のマスです。",
  color(map, i) {
    if (!map._works) {
      // 初めて塗るときに、マスの位置の組を作っておく（地図を読み直すと作り直す）
      const n = map.size;
      map._works = {
        queued: new Set((map.infra_queue || []).map(([, x, y]) => y * n + x)),
        sites: new Set((map.facilities || []).filter((f) => !f.built).map((f) => f.y * n + f.x)),
      };
    }
    if (map._works.sites.has(i)) return "hsla(0, 80%, 50%, 0.6)";
    if (map._works.queued.has(i)) return "hsla(30, 95%, 50%, 0.6)";
    return null;
  },
};
