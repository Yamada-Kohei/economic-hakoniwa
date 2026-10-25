// 反乱軍・部隊・占領のいる世界で、操作まで試す（段6 B4）。open_all.mjs の続き。
//   node tools/screens/interact.mjs http://127.0.0.1:8123 /tmp/screens.json
// 島の地図でマスを選ぶ（反乱軍のマス・部隊のマス・占領したマス）、軍の画面から命令を足す、
// 世界地図を開く。JavaScript の例外とコンソールの error があれば出す。
import { createRequire } from 'node:module';
import fs from 'node:fs';
const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const [base, seedFile] = process.argv.slice(2);
const seed = JSON.parse(fs.readFileSync(seedFile, 'utf8'));
const browser = await chromium.launch();
const problems = [];

async function session(kind) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await context.newPage();
  const note = (k, t) => problems.push(`[${kind}] ${k}: ${t}`);
  page.on('pageerror', (e) => note('pageerror', String(e.message).split('\n')[0]));
  page.on('console', (m) => { if (m.type() === 'error') note('console', m.text().slice(0, 300)); });
  await page.goto(`${base}/accounts/login/`);
  await page.fill('input[name=username]', `owner-${kind}`);
  await page.fill('input[name=password]', seed.password);
  await Promise.all([page.waitForNavigation(), page.click('button[type=submit]')]);
  return { page, note };
}

async function pickTile(page, id, x, y) {
  await page.goto(`${base}/islands/${id}/`, { waitUntil: 'networkidle' });
  // 小さな地図の押した位置からマスを選ぶ
  const n = await page.evaluate(async () => (await fetch(document.getElementById('island-map').dataset.api).then((r) => r.json())).size);
  const box = await page.locator('#minimap').boundingBox();
  await page.mouse.click(box.x + ((x + 0.5) / n) * box.width, box.y + ((y + 0.5) / n) * box.height);
  await page.waitForTimeout(800);
  return page.evaluate(() => document.body.innerText);
}

{
  const { page, note } = await session('player');
  const id = seed.islands.player;
  // 反乱軍のマス・占領されたマス・部隊のマス・首都のあたり
  for (const [x, y, want] of [[11, 21, '反乱'], [11, 20, '反乱'], [12, 6, null], [17, 6, null], [0, 0, null]]) {
    const text = await pickTile(page, id, x, y);
    if (want && !text.includes(want)) note('tile', `(${x},${y}) に「${want}」が出ない`);
    if (/\b(undefined|NaN|\[object Object\])\b/.test(text)) note('bad-text', `tile (${x},${y})`);
  }
  // 軍の画面から命令を足す（志願の兵・国防費・部隊を動かす）
  await page.goto(`${base}/islands/${id}/military/`, { waitUntil: 'networkidle' });
  const form = page.locator('form[data-order=recruit]');
  await form.locator('input[name=count]').fill('10');
  await form.locator('button[type=submit]').click();
  await page.waitForTimeout(1200);
  const notice = await form.locator('.order-notice').innerText();
  if (!notice.trim()) note('order', '志願の兵を足しても知らせが出ない');
  console.log('recruit notice:', notice.trim());
  await page.goto(`${base}/world/`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(800);
  await page.close();
}
{
  const { page, note } = await session('rival');
  await page.goto(`${base}/islands/${seed.islands.rival}/military/`, { waitUntil: 'networkidle' });
  await page.goto(`${base}/world/`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(600);
  const text = await pickTile(page, seed.islands.rival, 11, 24);
  console.log('rival occupied tile text includes 占領:', text.includes('占領'));
  await page.close();
}
{
  // 公電の知らせ: 島 2 から島 1 に手紙を送り、島 1 の画面の隅に出るか
  const a = await session('player');
  await a.page.goto(`${base}/islands/${seed.islands.player}/`, { waitUntil: 'networkidle' });
  await a.page.waitForTimeout(1500); // 初回の確認で基準を覚える
  const b = await session('rival');
  await b.page.goto(`${base}/letters/new/`, { waitUntil: 'networkidle' });
  const toSelect = b.page.locator('select[name=to], input[name=to]').first();
  await toSelect.selectOption(String(seed.islands.player)).catch(async () => toSelect.fill(String(seed.islands.player)));
  await b.page.fill('[name=subject]', '画面の試験');
  await b.page.fill('[name=body]', '公電の知らせが出るかの試験です。');
  await Promise.all([b.page.waitForNavigation(), b.page.locator('main form button.primary').click()]);
  await a.page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await a.page.waitForTimeout(2000);
  const shown = await a.page.locator('#letter-notice .letter-notice-card').isVisible();
  console.log('公電の知らせが隅に出た:', shown, shown ? await a.page.locator('[data-letter-notice-link]').innerText() : '');
  if (!shown) a.note('letter-notice', '手紙が届いても隅の知らせが出ない');
  await a.page.close(); await b.page.close();
}
await browser.close();
console.log(problems.length ? problems.join('\n') : '操作で見つかった問題はなし');
