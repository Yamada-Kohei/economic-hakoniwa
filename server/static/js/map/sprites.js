// 絵（スプライト）: テーマの sprites に並べた画像を読み込む。
// 絵がない・読めないものは、これまでどおり色付きのひし形と箱で描く（renderer.js）。
// 絵の決まり（大きさ・基準点・名前）は docs/map-art.md にまとめています。

/**
 * テーマの sprites を読み込み、名前 → { img, w, h, ax, ay } の Map を返す。
 * w, h は描くときの大きさ、(ax, ay) は基準点（マスの上面のひし形の下の頂点に合わせる点）。
 * どれも拡大前のピクセルで、倍率（項目の scale か、テーマの sprite_scale）を掛けた値。
 */
export async function loadSprites(theme) {
  const sprites = new Map();
  const entries = Object.entries(theme.sprites || {});
  await Promise.all(
    entries.map(
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

/** 絵を、基準点が (x, y) に来るように描く。 */
export function drawSprite(ctx, sprite, x, y) {
  ctx.drawImage(sprite.img, x - sprite.ax, y - sprite.ay, sprite.w, sprite.h);
}
