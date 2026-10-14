// 財政・金融の画面の命令の欄: 島の画面と同じ命令の列（OrderQueue）を、選んだ入力欄だけで出す。
// 命令は今の命令の列の後ろに足す（島の画面と同じ API と確かめを通る）。

import { getJson } from "../island/api.js";
import { OrderQueue } from "../island/orders.js";

/** #orders（data-url・data-defs・data-forms・data-heading）に命令の欄を作る。持ち主でなければ null。 */
export async function mountOrders() {
  const el = document.getElementById("orders");
  if (!el) return null;
  let defs;
  try {
    defs = await getJson(el.dataset.defs);
  } catch {
    el.innerHTML = `<p class="error">命令の欄を読み込めませんでした。</p>`;
    return null;
  }
  return new OrderQueue(el, defs, null, {
    forms: el.dataset.forms.split(","),
    heading: el.dataset.heading,
  });
}
