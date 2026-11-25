// 絵（スプライト）: テーマの sprites に並べた画像を読み込む。
// 絵がない・読めないものは、これまでどおり色付きのひし形と箱で描く（renderer.js）。
// 絵の決まり（大きさ・基準点・名前）は docs/map-art.md にまとめています。

/**
 * テーマの sprites を読み込み、名前 → { img, w, h, ax, ay } の Map を返す。
 * w, h は描くときの大きさ、(ax, ay) は基準点（マスの上面のひし形の下の頂点に合わせる点）。
 * どれも拡大前のピクセルで、倍率（項目の scale か、テーマの sprite_scale）を掛けた値。
 *
 * options:
 * - into: 読み込んだ絵を入れる Map。渡すと、読み込みが終わる前から読めた絵がこの Map に
 *   順に入る（呼び出し側はこの Map をそのまま描画に使える）。渡さなければ新しい Map を作る。
 * - priority: 先に読み始める絵の名前の Set。残りはそのあとに読み始める。
 * - onLoad: 絵が 1 枚読めるごとに呼ばれる。描き直しの合図に使う。
 * 返り値の Promise は全部の絵の読み込みが終わったら解決する（読めない絵があっても解決する）。
 */
export async function loadSprites(theme, options = {}) {
  const sprites = options.into || new Map();
  const entries = Object.entries(theme.sprites || {});
  const priority = options.priority;
  const ordered = priority
    ? [...entries.filter(([key]) => priority.has(key)), ...entries.filter(([key]) => !priority.has(key))]
    : entries;
  await Promise.all(
    ordered.map(
      ([key, spec]) =>
        new Promise((resolve) => {
          const s = typeof spec === "string" ? { file: spec } : spec;
          const scale = s.scale ?? theme.sprite_scale ?? 1;
          const img = new Image();
          img.onload = () => {
            const w = img.naturalWidth * scale;
            const h = img.naturalHeight * scale;
            const [ax, ay] = s.anchor ? [s.anchor[0] * scale, s.anchor[1] * scale] : [w / 2, h];
            sprites.set(key, { img, w, h, ax, ay });
            if (options.onLoad) options.onLoad(key);
            resolve();
          };
          img.onerror = () => {
            console.warn(`絵を読み込めませんでした: ${key} (${s.file})`);
            resolve();
          };
          img.src = (theme.base || "") + s.file;
        }),
    ),
  );
  return sprites;
}

/**
 * その島の地図で使う絵の名前の Set（loadSprites の priority 用）。
 * 地図データと定義から、描画（renderer.js）が実際に引く名前を集める。施設の絵は
 * 作り方・段階・規模・向きで後ろに印が付くので、名前で始まるものを全部先にする。
 * ここに挙がらなかった絵はあとから読まれるだけで、描画の結果は変わらない。
 */
export function spritePriorityKeys(theme, map, defs) {
  const keys = new Set();
  const all = Object.keys(theme.sprites || {});
  const add = (key) => { if (key in (theme.sprites || {})) keys.add(key); };
  const addFacility = (name) => {
    for (const key of all) {
      if (key === `facility/${name}` || key.startsWith(`facility/${name}+`) || key.startsWith(`facility/${name}@`)
        || key === `capital/${name}` || key.startsWith(`capital/${name}-`)) keys.add(key);
    }
  };
  for (const terrain of new Set(map.terrain || [])) add(`terrain/${terrain}`);
  if ((map.fallout || []).some((v) => v > 0)) add("overlay/contaminated");
  for (const zone of new Set(map.zone || [])) if (zone) add(`overlay/zone_${zone}`);
  if ((map.trench || []).some(Boolean)) { add("overlay/trench_back"); add("overlay/trench_front"); }
  if ((map.occupied || []).some(Boolean)) add("overlay/occupied");
  for (const u of map.units || []) add(`unit/${u.army}`);
  if (map.mine) add("unit/rebels"); // 反乱軍の位置はあとから軍の要約で届く
  for (const f of map.facilities || []) {
    if (!f.built) {
      add("construction");
      const look = theme?.construction_looks?.[f.kind];
      if (look) add(`construction@${look}`);
    } else if (f.kind === defs.housing) {
      add(`housing/${f.level}`);
    } else {
      addFacility(defs.facilities[f.kind]?.sprite || f.kind);
    }
  }
  return keys;
}

/** 絵を、基準点が (x, y) に来るように描く。 */
export function drawSprite(ctx, sprite, x, y) {
  ctx.drawImage(sprite.img, x - sprite.ax, y - sprite.ay, sprite.w, sprite.h);
}

/**
 * その国の旗の画像（上げた PNG か仮の旗）を読む。首都のマスに立てる旗に使う。
 * 読めたら { img, w, h }（画像そのものの大きさ）を onLoad に渡して Promise もそれで解決し、
 * 読めなければ null で解決する（旗なしで描く）。
 */
export function loadFlag(url, onLoad) {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      const flag = { img, w: img.naturalWidth, h: img.naturalHeight };
      if (onLoad) onLoad(flag);
      resolve(flag);
    };
    img.onerror = () => {
      console.warn(`旗を読み込めませんでした: ${url}`);
      resolve(null);
    };
    img.src = url;
  });
}
