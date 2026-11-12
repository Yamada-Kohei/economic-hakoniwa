// 命令のページ（UI-74）: 待っている命令の列の全部・並べ替え・取り消しと、前のターンまでの結果。
// 命令を足す入力欄は各ページの右の欄にあり、ここには出さない。

import { getJson } from "../island/api.js";
import { OrderQueue } from "../island/orders.js";

const el = document.getElementById("orders-page");
if (el) {
  try {
    const defs = await getJson(el.dataset.defs);
    new OrderQueue(el, defs, null, { page: true, heading: el.dataset.heading });
  } catch {
    el.innerHTML = `<p class="error">命令の列を読み込めませんでした。</p>`;
  }
}
