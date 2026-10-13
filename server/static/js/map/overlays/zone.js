// 区域: 工業団地とオフィス街のマスを塗る（色はテーマの industrial・office）。
export default {
  id: "zone",
  label: "工業団地・オフィス街",
  color(map, i, defs) {
    const zone = map.zone?.[i];
    if (zone !== "industrial" && zone !== "office") return null;
    return defs.theme[zone] || null;
  },
};
