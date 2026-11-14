// 政治と計画の画面（フェーズ3-4）: 推移のグラフと、持ち主には政治の命令（報道と集会・労働の動員・
// 議会の解散・国家元首の名前・移民の受け入れと出国の扱い・憲法改正・経済体制の変更）と計画の命令（公定価格・公定価格の改定と
// 決まり・重点産業・人員計画と公共事業の優先度・輸出の計画・値段の上限と下限）。表の下の小さな入力欄（form[data-order]）から、命令の列（右の欄。島の画面と同じ列）の
// 後ろに足す。

import { mountCharts } from "./common.js";
import { mountOrders } from "./orders.js";

// 入力欄の名前ごとの値の形（ほかは文字）。PERCENT_FIELDS は % で入れ、命令には1万分の1で渡す
const INT_FIELDS = new Set(["band", "upkeep_priority", "build_priority"]);
// 値段は見せる単位で小数も書ける（W-30。サーバーが計算の単位に直して丸める）
const NUMBER_FIELDS = new Set(["price"]);
const PERCENT_FIELDS = new Set(["cap", "quota"]);
const BOOL_FIELDS = new Set(["on"]);

mountCharts();
const orders = await mountOrders();

/**
 * 入力欄の値から命令の中身を作る。空の欄（「変えない」）は入れない。data-list の欄は並びにし、
 * 何も選ばなかったときは空の並びにする（重点産業をなくす）。
 */
function payloadOf(form) {
  const payload = {};
  const list = form.dataset.list;
  if (list) payload[list] = [];
  for (const [key, value] of new FormData(form).entries()) {
    if (key === list) payload[key].push(String(value));
    else if (value === "") continue;
    else if (INT_FIELDS.has(key)) payload[key] = Math.round(Number(value));
    else if (NUMBER_FIELDS.has(key)) payload[key] = Number(value);
    else if (PERCENT_FIELDS.has(key)) payload[key] = Math.round(Number(value) * 100);
    else if (BOOL_FIELDS.has(key)) payload[key] = value === "true";
    else payload[key] = String(value).trim();
  }
  return payload;
}

/** 人員計画（PL-59）の表から命令の中身を作る: 枠を書いた行と「枠を外す」の行（外すは null）。 */
function staffPlanOf(form) {
  const target = (el) =>
    el.dataset.kind ? { kind: el.dataset.kind } : { x: Number(el.dataset.x), y: Number(el.dataset.y) };
  const plans = [];
  for (const box of form.querySelectorAll('input[name="clear"]:checked')) {
    plans.push({ ...target(box), cap: null });
  }
  for (const input of form.querySelectorAll('input[name="cap"]')) {
    if (input.value === "") continue;
    const t = target(input);
    if (plans.some((p) => p.kind === t.kind && p.x === t.x && p.y === t.y)) continue;
    plans.push({ ...t, cap: Math.round(Number(input.value)) });
  }
  return { plans };
}

/**
 * 輸出の計画（PL-62）の表から命令の中身を作る: 量を書いた財（見せる単位）と「輸出しない」の財（0）。
 * 表をまるごと置き換える命令なので、何も書かなければ空の表（名指しをやめる）。
 */
function exportPlanOf(form) {
  const plans = [];
  const none = new Set(
    [...form.querySelectorAll('input[name="none"]:checked')].map((box) => box.dataset.good),
  );
  for (const good of none) plans.push({ good, quantity: 0 });
  for (const input of form.querySelectorAll('input[name="quantity"]')) {
    const good = input.dataset.good;
    if (input.value === "" || none.has(good)) continue;
    plans.push({ good, quantity: Number(input.value) });
  }
  return { plans };
}

document.addEventListener("submit", async (e) => {
  const form = e.target.closest("form[data-order]");
  if (!form || !orders) return;
  e.preventDefault();
  if (!form.reportValidity()) return;
  const notice = form.closest("section")?.querySelector(".order-notice");
  const payload =
    "staffPlan" in form.dataset
      ? staffPlanOf(form)
      : "exportPlan" in form.dataset
        ? exportPlanOf(form)
        : payloadOf(form);
  if ("staffPlan" in form.dataset && !payload.plans.length) {
    if (notice) notice.textContent = "枠を書くか「枠を外す」を選んでください。";
    return;
  }
  await orders.add(form.dataset.order, payload);
  if (notice) notice.textContent = orders.message;
});
