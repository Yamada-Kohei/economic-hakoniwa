// 描画: 奥のマスから手前のマスへ（x + y の小さい順に）描き、高いものが奥を自然に隠すようにする。
// 重ね表示の棒（UI-79）は島を描いたあとにもう一度奥から手前へ描き、建物に隠れず、半透明で奥の棒も透けて見える。

import { barPixels, barsBackToFront, drawsBars, solidColor } from "./overlays/bars.js";
import { CHANGE_COLORS } from "./changes.js";
import { LENS_COLORS } from "./history.js";
import { lift, tileToWorld } from "./projection.js";
import { drawSprite } from "./sprites.js";

/**
 * 島を描く。
 * options: selected（選んだマス {x, y}）、marked（印を付けるマスの Set "x,y"）、
 *          changeMarks（前のターンから変わったマスの Map "x,y" → 種類。changes.js）、
 *          lensMarks（節目の写しと比べた差の Map "x,y" → 種類。history.js）、
 *          overlay（重ねて表示する情報。overlays/ のモジュール）、
 *          overlayMode（"bars" で量のある重ね表示を棒にする。ほかは塗り分け。UI-79）、
 *          sprites（sprites.js の loadSprites が返す絵。ないものは色の箱で描く）、
 *          flag（その国の旗の画像 { img, w, h }。sprites.js の loadFlag が返す。
 *                あれば首都のマスに小さく立てる）、
 *          ships（停泊中の船の置き場所。ships.js の placeShips が返す "x,y" → 船の並び）
 */
export function drawIsland(ctx, map, defs, view, options = {}) {
  const theme = defs.theme;
  const { width, height } = ctx.canvas;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, width, height);
  ctx.setTransform(view.scale * view.dpr, 0, 0, view.scale * view.dpr, view.offsetX * view.dpr, view.offsetY * view.dpr);

  const sprites = options.sprites || new Map();
  const bars = drawsBars(options.overlay, options.overlayMode);
  const fill = options.overlay && !bars ? options.overlay : null;
  const n = map.size;
  const facilityAt = new Map();
  for (const f of map.facilities || []) facilityAt.set(f.y * n + f.x, f);
  const unitsAt = new Map();
  // 反乱軍の位置は持ち主だけ。通常の部隊とまとめて、同じマスでは印をずらす。
  const rebels = map.mine ? (map.civil_war?.groups || []).map((g) => ({ ...g, army: "rebels", rebel: true })) : [];
  for (const u of [...(map.units || []), ...rebels]) {
    const i = u.y * n + u.x;
    unitsAt.set(i, [...(unitsAt.get(i) || []), u]);
  }

  for (let s = 0; s <= 2 * (n - 1); s++) {
    for (let x = Math.max(0, s - n + 1); x <= Math.min(s, n - 1); x++) {
      const y = s - x;
      const i = y * n + x;
      const terrain = map.terrain[i];
      const colors = theme.terrain[terrain] || theme.fallback;
      const h = lift(defs.terrain[terrain]?.height ?? 0, theme);
      const { px, py } = tileToWorld(x, y, theme);
      const top = py - h;
      const f = facilityAt.get(i);
      // 施設のあるマスは地形の絵（木や山）を描かず、上面の色だけにする。施設の絵と重ならないように
      drawBlock(ctx, px, py, h, colors, theme, f ? null : sprites.get(`terrain/${terrain}`));
      drawFallout(ctx, map.fallout?.[i], sprites.get("overlay/contaminated"), px, top, theme);
      if (fill) {
        const color = fill.color(map, i, defs);
        if (color) {
          ctx.fillStyle = color;
          diamondPath(ctx, px, top, theme.tile_width / 2, theme.tile_height / 2);
          ctx.fill();
        }
      }
      if (map.zone?.[i] === "no_development") drawHatch(ctx, px, top, theme);
      // 区域の指定（工業団地・オフィス街・商業地）: まだ何も建っていないマスに柵と看板（docs/map-art.md）
      if (!f && ZONE_LOTS.has(map.zone?.[i])) drawOverlay(ctx, sprites.get(`overlay/zone_${map.zone[i]}`), px, top, theme);
      if (map.power_line?.[i] && !facilityAt.has(i)) drawPowerLine(ctx, map, x, y, px, top, theme, facilityAt);
      const shipsHere = options.ships?.get(`${x},${y}`);
      if (shipsHere) drawShips(ctx, shipsHere, px, top, theme, sprites);
      // 陣地（塹壕）: 奥の2辺を施設・住宅より先に、手前の2辺をあとに描く（docs/map-art.md）
      const trench = map.trench?.[i];
      if (trench) drawOverlay(ctx, sprites.get("overlay/trench_back"), px, top, theme);
      if (f) drawFacility(ctx, f, map, defs, px, top, theme, sprites, options.flag);
      // 首都のマスに建物がないときは、地面に直接旗を立てる
      if (!f && options.flag && map.capital && map.capital[0] === x && map.capital[1] === y) {
        drawCapitalFlag(ctx, options.flag, px, top + theme.tile_height / 2, theme);
      }
      if (f?.hidden && map.mine) drawUnderground(ctx, px, top, theme, options.undergroundLabel || "");
      if (trench) drawOverlay(ctx, sprites.get("overlay/trench_front"), px, top, theme);
      if (map.occupied?.[i]) {
        if (map.occupied[i] === -1) {
          ctx.fillStyle = "rgba(190, 35, 55, 0.35)";
          diamondPath(ctx, px, top, theme.tile_width / 2, theme.tile_height / 2);
          ctx.fill();
          outlineTop(ctx, px, top, theme, "#b51f38");
        } else if (sprites.has("overlay/occupied")) drawOverlay(ctx, sprites.get("overlay/occupied"), px, top, theme);
        else outlineTop(ctx, px, top, theme, "#c34b38");
      }
      if (unitsAt.has(i)) drawUnits(ctx, unitsAt.get(i), px, top, theme, sprites);
      const changeKind = options.changeMarks?.get(`${x},${y}`);
      if (changeKind) drawChangeMark(ctx, changeKind, px, top, theme);
      const lensKind = options.lensMarks?.get(`${x},${y}`);
      if (lensKind) drawLensMark(ctx, lensKind, px, top, theme);
      if (options.marked?.has(`${x},${y}`)) outlineTop(ctx, px, top, theme, "#ff5722");
      if (options.selected && options.selected.x === x && options.selected.y === y) {
        outlineTop(ctx, px, top, theme, "#ffeb3b");
      }
    }
  }
  if (bars) drawBars(ctx, map, defs, options.overlay, options.selected);
}

// 棒の透け具合（上面と側面）。側面は黒を重ねて暗くし、角柱に見せる
const BAR_TOP_ALPHA = 0.7;
const BAR_SIDE_ALPHA = 0.55;
const BAR_SHADE_LEFT = "rgba(0, 0, 0, 0.18)";
const BAR_SHADE_RIGHT = "rgba(0, 0, 0, 0.36)";
const BAR_EDGE = "rgba(0, 0, 0, 0.45)";
const BAR_FOOT = 0.55; // 棒の足元の大きさ（マスに対する割合）。周りの地面と隣の棒が見えるように

/**
 * 重ね表示の棒: マスの上面の真ん中に、高さを重ね表示ごとの物差し（height）で決めた半透明の角柱を立てる。
 * 奥から手前へ描くので、手前の高い棒が奥の棒を覆っても、半透明なので奥の棒が透けて見える。
 * 選んだマスの枠は棒の上にも描き直す。
 */
function drawBars(ctx, map, defs, overlay, selected) {
  const theme = defs.theme;
  const hw = (theme.tile_width / 2) * BAR_FOOT;
  const hh = (theme.tile_height / 2) * BAR_FOOT;
  ctx.save();
  ctx.lineWidth = 0.75;
  ctx.strokeStyle = BAR_EDGE;
  for (const { x, y, i, value } of barsBackToFront(map, overlay, defs)) {
    const color = solidColor(overlay.color(map, i, defs) || "hsl(0, 0%, 50%)");
    const { px, py } = tileToWorld(x, y, theme);
    const ground = py - lift(defs.terrain[map.terrain[i]]?.height ?? 0, theme);
    // 足元のひし形の上の頂点（マスの真ん中に置く）と、棒の上面の上の頂点
    const base = ground + theme.tile_height / 2 - hh;
    const top = base - barPixels(value, theme);
    ctx.globalAlpha = BAR_SIDE_ALPHA;
    sides(ctx, px, top, base, hw, hh, color, color);
    sides(ctx, px, top, base, hw, hh, BAR_SHADE_LEFT, BAR_SHADE_RIGHT);
    ctx.globalAlpha = BAR_TOP_ALPHA;
    ctx.fillStyle = color;
    diamondPath(ctx, px, top, hw, hh);
    ctx.fill();
    ctx.stroke();
    ctx.beginPath();
    for (const dx of [-hw, 0, hw]) {
      const dy = dx === 0 ? 2 * hh : hh;
      ctx.moveTo(px + dx, top + dy);
      ctx.lineTo(px + dx, base + dy);
    }
    ctx.stroke();
  }
  ctx.restore();
  if (selected) {
    const i = selected.y * map.size + selected.x;
    const { px, py } = tileToWorld(selected.x, selected.y, theme);
    outlineTop(ctx, px, py - lift(defs.terrain[map.terrain[i]]?.height ?? 0, theme), theme, "#ffeb3b");
  }
}

/** 1 点を超えてもなめらかに濃くする。表示の透明度であり、ゲームの数値ではない。 */
export function falloutOpacity(value) {
  return value > 0 ? 0.15 + 0.8 * value / (value + 3) : 0;
}
function drawFallout(ctx, value, sprite, px, top, theme) {
  const opacity = falloutOpacity(value);
  if (!opacity) return;
  ctx.save();
  ctx.globalAlpha = opacity;
  if (sprite) drawOverlay(ctx, sprite, px, top, theme);
  else {
    ctx.fillStyle = "#9a8d30";
    diamondPath(ctx, px, top, theme.tile_width / 2, theme.tile_height / 2);
    ctx.fill();
  }
  ctx.restore();
}
function drawUnderground(ctx, px, top, theme, label) {
  ctx.save();
  ctx.font = "bold 9px sans-serif";
  ctx.textAlign = "center";
  const y = top + theme.tile_height * 0.8;
  const width = Math.max(22, ctx.measureText(label).width + 6);
  ctx.fillStyle = "#283b4b";
  ctx.fillRect(px - width / 2, y - 9, width, 12);
  ctx.fillStyle = "#fff";
  ctx.fillText(label, px, y);
  ctx.restore();
}

/** マスいっぱいの重ねる絵（overlay/）。基準点を上面のひし形の下の頂点に合わせる。 */
// 柵と看板の絵を描く区域（overlay/zone_<区域>）
const ZONE_LOTS = new Set(["industrial", "office", "commercial"]);

function drawOverlay(ctx, sprite, px, top, theme) {
  if (sprite) drawSprite(ctx, sprite, px, top + theme.tile_height);
}

const OWN_RING = "#ffca28"; // 自国の部隊の印の下の輪（site.css の自国の区画の縁の色）

/**
 * 部隊の印（unit/<種類>）をマスの真ん中に立て、下に国の色の輪を描く。絵は国によらず同じ。
 * 絵がない種類は輪だけ。1 マスに複数いるときは横にずらす。
 */
function drawUnits(ctx, units, px, top, theme, sprites) {
  const hw = theme.tile_width / 5;
  const hh = theme.tile_height / 5;
  const cy = top + theme.tile_height / 2;
  units.forEach((u, k) => {
    const x = px + (k - (units.length - 1) / 2) * theme.tile_width * 0.22;
    ctx.strokeStyle = u.rebel ? "#b51f38" : OWN_RING;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.ellipse(x, cy, hw, hh, 0, 0, Math.PI * 2);
    ctx.stroke();
    const sprite = sprites.get(`unit/${u.army}`);
    if (sprite) drawSprite(ctx, sprite, x, cy + hh / 2);
    if (u.rebel) {
      ctx.save();
      ctx.font = "bold 9px sans-serif";
      ctx.textAlign = "center";
      const label = String(u.soldiers);
      const width = ctx.measureText(label).width + 6;
      ctx.fillStyle = "#92222a";
      ctx.fillRect(x - width / 2, cy + hh, width, 12);
      ctx.fillStyle = "#fff";
      ctx.fillText(label, x, cy + hh + 9);
      ctx.restore();
    }
  });
}

/**
 * 停泊中の船（案6 の商船・W-100 の艦隊）: 海マスの真ん中に船の絵（ship/）を浮かべる。
 * 絵がない種類は色の楕円（商船は錆び色、艦隊は灰色）で描く。
 */
function drawShips(ctx, ships, px, top, theme, sprites) {
  const cy = top + theme.tile_height / 2;
  for (const s of ships) {
    const sprite = sprites.get(s.name);
    if (sprite) {
      drawSprite(ctx, sprite, px + s.dx, cy + s.dy);
    } else {
      ctx.save();
      ctx.fillStyle = s.fleet ? "#7e8a94" : "#9c4a2f";
      ctx.beginPath();
      ctx.ellipse(px + s.dx, cy + s.dy, theme.tile_width / 5, theme.tile_height / 5, 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    }
  }
}

function drawBlock(ctx, px, py, h, colors, theme, sprite) {
  const hw = theme.tile_width / 2;
  const hh = theme.tile_height / 2;
  const top = py - h;
  if (h > 0) sides(ctx, px, top, py, hw, hh, colors.left, colors.right);
  if (sprite) {
    // 地形の絵は上面（とそこから上に出る木や岩）。側面は色で描く
    drawSprite(ctx, sprite, px, top + 2 * hh);
    return;
  }
  ctx.fillStyle = colors.top;
  diamondPath(ctx, px, top, hw, hh);
  ctx.fill();
}

/**
 * 施設の絵の名前。住宅は住宅地の段階ごと。作り方や段階で絵を分ける施設は lookSprite の絵。
 * 首都の官庁は、体制で決まる建物の組み合わせの絵
 * （capital/議事堂-王宮 など。map.capital_buildings を軸の順に "-" でつなぐ）、なければ capital/ の絵。
 */
function facilitySprite(f, defs, map, isCapital, sprites, theme) {
  if (!f.built) {
    // 工事中は施設の種類の絵（theme.construction_looks。農場は畑、鉱山は岩の斜面、港は岸壁の工事）
    const look = theme?.construction_looks?.[f.kind];
    return (look && sprites.get(`construction@${look}`)) || sprites.get("construction");
  }
  if (f.kind === defs.housing) {
    const level = defs.settlements[f.level] ? f.level : Object.keys(defs.settlements)[0];
    return sprites.get(`housing/${level}`);
  }
  const name = defs.facilities[f.kind]?.sprite || f.kind;
  if (!isCapital) {
    return coastalSprite(f, defs, map, name, sprites) || lookSprite(f, defs, theme, name, sprites)
      || sprites.get(`facility/${name}`);
  }
  const buildings = defs.facilities[f.kind]?.category === "admin" ? map.capital_buildings || [] : [];
  const regime = buildings.length ? sprites.get(`capital/${buildings.join("-")}`) : null;
  return regime || sprites.get(`capital/${name}`) || sprites.get(`facility/${name}`);
}

/**
 * 作り方・段階・規模で分けた絵（docs/map-art.md）。theme.sprite_looks の財が作り方の産出か投入にあれば
 * +<財>（綿の畑・銅の鉱山・油の火力など）、段階（農場の FM-50）があれば @g<段階>、建て増しの規模（FM-47）が
 * theme.size_tiers の区切りを超えれば @s<段>を、この順に名前の後ろに付ける（facility/thermal_power_plant+fuel@s3 など）。
 * 付けられる物を全部付けた名前から順に、後ろの物を外しながら探し、どれもなければ null（名前のままの絵）。
 */
function lookSprite(f, defs, theme, name, sprites) {
  const recipe = defs.facilities[f.kind]?.recipes?.[f.recipe];
  const goods = theme?.sprite_looks?.[f.kind] || [];
  const look = recipe ? goods.find((g) => g in recipe.outputs || g in recipe.inputs) : undefined;
  const tier = 1 + (theme?.size_tiers || []).filter((n) => (f.size || 1) >= n).length;
  const looks = look ? [`+${look}`, ""] : [""];
  const grades = f.grade ? [`@g${f.grade}`, ""] : [""];
  const sizes = tier > 1 ? [`@s${tier}`, ""] : [""];
  for (const l of looks) {
    for (const g of grades) {
      for (const s of sizes) {
        if (!l && !g && !s) continue;
        const sprite = sprites.get(`facility/${name}${l}${g}${s}`);
        if (sprite) return sprite;
      }
    }
  }
  return null;
}

// 海沿いの施設の絵の向き: 海のあるマスの向きと、その絵の名前に付ける印（docs/map-art.md）。
// 海が手前（右下・左下）の絵は水面と岸壁が見えるので先に選ぶ
const SEA_SIDES = [["", 1, 0], ["@sw", 0, 1], ["@ne", 0, -1], ["@nw", -1, 0]];

/** 海沿いの施設（coastal）は、隣の海のある向きの絵（facility/port@sw など）。なければ null。 */
function coastalSprite(f, defs, map, name, sprites) {
  if (!defs.facilities[f.kind]?.coastal) return null;
  const n = map.size;
  for (const [suffix, dx, dy] of SEA_SIDES) {
    const x = f.x + dx;
    const y = f.y + dy;
    if (x < 0 || y < 0 || x >= n || y >= n) continue;
    if (defs.terrain[map.terrain[y * n + x]]?.land) continue;
    const sprite = sprites.get(`facility/${name}${suffix}`);
    if (sprite) return sprite;
  }
  return null;
}

/** 施設: 絵があれば絵、なければ色付きの箱。住宅の高さは住宅地の段階、工事中は枠だけ。
 * 首都の建物が建っていて旗の画像（flag）があれば、建物の上に旗を立てる。 */
function drawFacility(ctx, f, map, defs, px, ground, theme, sprites, flag) {
  const isCapital = map.capital && map.capital[0] === f.x && map.capital[1] === f.y;
  const sprite = facilitySprite(f, defs, map, isCapital, sprites, theme);
  if (sprite) {
    drawSprite(ctx, sprite, px, ground + theme.tile_height);
    if (f.built) drawStopped(ctx, f, defs, px, ground, theme.tile_height / 2);
    if (isCapital && f.built && flag) {
      // 絵の上の余白ぶん、竿の下端を屋根に少し沈める
      drawCapitalFlag(ctx, flag, px, ground + theme.tile_height - sprite.h + FLAG_SINK, theme);
    }
    return;
  }
  const style = theme.facilities?.[f.kind] || theme.facility_fallback;
  let height = style.height;
  if (f.kind === defs.housing) {
    const level = defs.settlements[f.level] || Object.values(defs.settlements)[0];
    height = level?.height ?? 1;
  }
  const unit = theme.facility_unit;
  // 箱はマスより一回り小さくする
  const k = f.kind === defs.housing ? 0.7 : 0.8;
  const hw = (theme.tile_width / 2) * k;
  const hh = (theme.tile_height / 2) * k;
  const base = ground + (theme.tile_height / 2) * (1 - k);
  const top = base - height * unit;
  if (!f.built) {
    ctx.strokeStyle = theme.construction;
    ctx.lineWidth = 1.5;
    ctx.setLineDash([3, 3]);
    diamondPath(ctx, px, top, hw, hh);
    ctx.stroke();
    ctx.beginPath();
    for (const [dx, dy] of [[-hw, hh], [hw, hh], [0, 2 * hh]]) {
      ctx.moveTo(px + dx, top + dy);
      ctx.lineTo(px + dx, base + dy);
    }
    ctx.stroke();
    ctx.setLineDash([]);
    return;
  }
  const color = style.color;
  sides(ctx, px, top, base, hw, hh, shade(color, 0.78), shade(color, 0.62));
  ctx.fillStyle = isCapital ? theme.capital_roof : color;
  diamondPath(ctx, px, top, hw, hh);
  ctx.fill();
  drawStopped(ctx, f, defs, px, top, hh);
  if (isCapital && flag) drawCapitalFlag(ctx, flag, px, top + hh, theme);
}

// 首都の旗の竿の下端を、建物のてっぺんに少し沈める量（ピクセル）
const FLAG_SINK = 3;

/**
 * 首都の旗を描くときの大きさ。建物の屋根に立つ小旗として、幅はマスの幅の 8 分の 1、縦は画像の縦横比どおり。
 * 背の高い画像はマスの高さの 4 分の 1 までに収め、幅もそれに合わせて縮める。
 * （11-26 本人「もっとずっと小さく」: 最初の版はマスの半分の幅で、建物より目立っていた）
 */
export function capitalFlagSize(flag, theme) {
  let w = theme.tile_width / 8;
  let h = (w * flag.h) / flag.w;
  if (h > theme.tile_height / 4) {
    h = theme.tile_height / 4;
    w = (h * flag.w) / flag.h;
  }
  return { w, h };
}

/**
 * 首都の旗: (px, base) を竿の下端として竿を立て、竿の上のほうから右へ旗を広げる（静止画）。
 * 旗の後ろに白い縁を置き、建物や空の色に埋もれて見えなくならないようにする。
 */
export function drawCapitalFlag(ctx, flag, px, base, theme) {
  if (!flag || !flag.w || !flag.h) return;
  const { w, h } = capitalFlagSize(flag, theme);
  const poleTop = base - h - 3;
  ctx.save();
  ctx.fillStyle = "#fff";
  ctx.fillRect(px - 0.5, poleTop + 0.5, w + 1, h + 1);
  ctx.drawImage(flag.img, px, poleTop + 1, w, h);
  ctx.strokeStyle = "#4a3520";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(px, base);
  ctx.lineTo(px, poleTop);
  ctx.stroke();
  ctx.restore();
}

/**
 * 前のターンから変わったマスの印（案3 の静止版）: 種類の色の静止枠と、上の頂点に色丸＋白い記号。
 * 記号は 建設=＋、撤去=×、段階=◆。色は changes.js の CHANGE_COLORS（凡例と同じ色）。
 * top はマスの上面の上の頂点。丸は枠の角に重ね、白い縁を付けて建物や地形の絵に埋もれないようにする。
 * 止まった施設の赤い点（マスの真ん中）とは置く場所を分け、重なっても両方読めるようにする。
 */
function drawChangeMark(ctx, kind, px, top, theme) {
  const color = CHANGE_COLORS[kind];
  if (!color) return;
  const hw = theme.tile_width / 2;
  const hh = theme.tile_height / 2;
  ctx.strokeStyle = color;
  ctx.lineWidth = 2.5;
  diamondPath(ctx, px, top, hw, hh);
  ctx.stroke();
  const r = Math.max(5, hh * 0.42);
  ctx.beginPath();
  ctx.arc(px, top, r, 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();
  ctx.strokeStyle = "#fff";
  ctx.lineWidth = 1.5;
  ctx.stroke();
  const g = r * 0.52;
  ctx.save();
  ctx.strokeStyle = "#fff";
  ctx.fillStyle = "#fff";
  ctx.lineWidth = 2;
  ctx.lineCap = "round";
  ctx.beginPath();
  if (kind === "built") {
    ctx.moveTo(px - g, top);
    ctx.lineTo(px + g, top);
    ctx.moveTo(px, top - g);
    ctx.lineTo(px, top + g);
    ctx.stroke();
  } else if (kind === "removed") {
    ctx.moveTo(px - g, top - g);
    ctx.lineTo(px + g, top + g);
    ctx.moveTo(px + g, top - g);
    ctx.lineTo(px - g, top + g);
    ctx.stroke();
  } else {
    ctx.moveTo(px, top - g);
    ctx.lineTo(px + g, top);
    ctx.lineTo(px, top + g);
    ctx.lineTo(px - g, top);
    ctx.closePath();
    ctx.fill();
  }
  ctx.restore();
}

/**
 * 変化レンズの印（案29）: 節目の写しと比べた差。種類の色の枠と、上の頂点に色丸＋白い三角。
 * 新しくできた物は上向き（▲）、無くなった・衰えた物は下向き（▼）、姿が変わっただけの
 * 物は輪郭だけ（丸も三角も付けない。hal #5716）。色は history.js の LENS_COLORS（凡例と同じ）。
 * 変わったマスの印（drawChangeMark）と置く場所・形をそろえ、重ねても読み分けられるようにする。
 */
function drawLensMark(ctx, kind, px, top, theme) {
  const color = LENS_COLORS[kind];
  if (!color) return;
  const hw = theme.tile_width / 2;
  const hh = theme.tile_height / 2;
  ctx.strokeStyle = color;
  ctx.lineWidth = 2.5;
  diamondPath(ctx, px, top, hw, hh);
  ctx.stroke();
  if (kind === "changed") return; // 姿が変わっただけの物は輪郭だけ
  const r = Math.max(5, hh * 0.42);
  ctx.beginPath();
  ctx.arc(px, top, r, 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();
  ctx.strokeStyle = "#fff";
  ctx.lineWidth = 1.5;
  ctx.stroke();
  const g = r * 0.55;
  ctx.save();
  ctx.fillStyle = "#fff";
  ctx.beginPath();
  if (kind === "new") {
    ctx.moveTo(px, top - g);
    ctx.lineTo(px + g, top + g * 0.8);
    ctx.lineTo(px - g, top + g * 0.8);
  } else {
    ctx.moveTo(px, top + g);
    ctx.lineTo(px + g, top - g * 0.8);
    ctx.lineTo(px - g, top - g * 0.8);
  }
  ctx.closePath();
  ctx.fill();
  ctx.restore();
}

/** 止まっている施設には赤い印（絵の上にも描く）。top は印を置くひし形の上の頂点。
 * 止まりかはサーバーの理由の表（hakoniwa_core/stops.py）で決めた f.stopped を読み、理由の一覧を
 * ここに写さない（W-34）。稼働率 0 で、稼働準備中（W-219）・扱う荷や客に合わせた操業でないもの。
 * 売れ行きに合わせて 0 まで下げて止まった施設（生産停止中（買い注文不足））にも描き、下げても
 * 作り続けている生産調整中には描かない（UI-84）。 */
export function drawStopped(ctx, f, defs, px, top, hh) {
  if (f.kind === defs.housing || !f.stopped) return;
  ctx.fillStyle = "#e53935";
  ctx.beginPath();
  ctx.arc(px, top + hh, Math.max(2, hh * 0.35), 0, Math.PI * 2);
  ctx.fill();
}

/** 送電線: 隣の送電網のマスへ向けて線を引く。 */
function drawPowerLine(ctx, map, x, y, px, top, theme, facilityAt) {
  const n = map.size;
  const hw = theme.tile_width / 2;
  const hh = theme.tile_height / 2;
  const cx = px;
  const cy = top + hh;
  ctx.strokeStyle = theme.power_line;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  let linked = false;
  for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
    const nx = x + dx;
    const ny = y + dy;
    if (nx < 0 || ny < 0 || nx >= n || ny >= n) continue;
    const j = ny * n + nx;
    if (!map.power_line[j] && !facilityAt.has(j)) continue;
    // x が増えると右下、y が増えると左下
    const ex = cx + (dx - dy) * hw * 0.5;
    const ey = cy + (dx + dy) * hh * 0.5;
    ctx.moveTo(cx, cy);
    ctx.lineTo(ex, ey);
    linked = true;
  }
  ctx.stroke();
  if (!linked) {
    ctx.fillStyle = theme.power_line;
    ctx.fillRect(cx - 1.5, cy - 1.5, 3, 3);
  }
}

function drawHatch(ctx, px, top, theme) {
  const hw = theme.tile_width / 2;
  const hh = theme.tile_height / 2;
  ctx.save();
  diamondPath(ctx, px, top, hw, hh);
  ctx.clip();
  ctx.strokeStyle = theme.no_development;
  ctx.lineWidth = 2;
  ctx.beginPath();
  for (let d = -hw * 2; d <= hw * 2; d += 8) {
    ctx.moveTo(px + d, top);
    ctx.lineTo(px + d + hh * 2, top + hh * 2);
  }
  ctx.stroke();
  ctx.restore();
}

function sides(ctx, px, top, bottom, hw, hh, left, right) {
  ctx.fillStyle = left;
  ctx.beginPath();
  ctx.moveTo(px - hw, top + hh);
  ctx.lineTo(px, top + 2 * hh);
  ctx.lineTo(px, bottom + 2 * hh);
  ctx.lineTo(px - hw, bottom + hh);
  ctx.closePath();
  ctx.fill();
  ctx.fillStyle = right;
  ctx.beginPath();
  ctx.moveTo(px + hw, top + hh);
  ctx.lineTo(px, top + 2 * hh);
  ctx.lineTo(px, bottom + 2 * hh);
  ctx.lineTo(px + hw, bottom + hh);
  ctx.closePath();
  ctx.fill();
}

function outlineTop(ctx, px, top, theme, color) {
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  diamondPath(ctx, px, top, theme.tile_width / 2, theme.tile_height / 2);
  ctx.stroke();
}

function diamondPath(ctx, px, top, hw, hh) {
  ctx.beginPath();
  ctx.moveTo(px, top);
  ctx.lineTo(px + hw, top + hh);
  ctx.lineTo(px, top + 2 * hh);
  ctx.lineTo(px - hw, top + hh);
  ctx.closePath();
}

/** "#rrggbb" の色を factor 倍に暗くする。 */
export function shade(hex, factor) {
  const v = parseInt(hex.slice(1), 16);
  const r = Math.round(((v >> 16) & 255) * factor);
  const g = Math.round(((v >> 8) & 255) * factor);
  const b = Math.round((v & 255) * factor);
  return `rgb(${r}, ${g}, ${b})`;
}
