// 島の種類ごとに全画面をブラウザで開き、JavaScript の例外・コンソールの error・失敗した読み込み・
// 画面に出た "undefined" "NaN" "[object Object]" を集める（段6 B4）。
//
//   DATABASE_URL=sqlite:////tmp/screens.sqlite3 python tools/screens/seed.py > /tmp/screens.json
//   DATABASE_URL=sqlite:////tmp/screens.sqlite3 python manage.py runserver 127.0.0.1:8123 --noreload &
//   node tools/screens/open_all.mjs http://127.0.0.1:8123 /tmp/screens.json [出力の JSON]
//
// playwright は Node から require('playwright') で使えること（クラウドの環境には入っている）。
import { createRequire } from 'node:module';
import fs from 'node:fs';
const require = createRequire(import.meta.url);
const { chromium } = require('playwright');

const [base, seedFile, outFile] = process.argv.slice(2);
const seed = JSON.parse(fs.readFileSync(seedFile, 'utf8'));

const ISLAND_PAGES = ['', 'log/', 'economy/', 'market/', 'chain/', 'facilities/', 'fiscal/',
  'finance/', 'trade/', 'fx/', 'politics/', 'plan/', 'diplomacy/', 'military/', 'country/', 'diary/'];
const SITE_PAGES = ['/', '/world/', '/rankings/', '/news/', '/help/', '/help/hakoniwa/', '/board/',
  '/hall/', '/letters/', '/found/', '/accounts/keys/'];
const BAD_TEXT = /\b(undefined|NaN|\[object Object\]|Infinity)\b/;

const browser = await chromium.launch();
const problems = [];
const seen = new Set();

async function login(context, kind) {
  const page = await context.newPage();
  await page.goto(`${base}/accounts/login/`);
  await page.fill('input[name=username]', `owner-${kind}`);
  await page.fill('input[name=password]', seed.password);
  await Promise.all([page.waitForNavigation(), page.click('button[type=submit], input[type=submit]')]);
  await page.close();
}

async function open(context, label, url) {
  const page = await context.newPage();
  const here = [];
  const note = (kind, text) => {
    const key = `${label}|${kind}|${text}`;
    if (!seen.has(key)) { seen.add(key); here.push({ label, url, kind, text }); }
  };
  page.on('pageerror', (e) => note('pageerror', String(e.message || e).split('\n')[0]));
  page.on('console', (m) => { if (m.type() === 'error') note('console', m.text().slice(0, 300)); });
  page.on('response', (r) => {
    if (r.status() >= 400) note(`http${r.status()}`, new URL(r.url()).pathname);
  });
  page.on('requestfailed', (r) => note('requestfailed', new URL(r.url()).pathname));
  try {
    const res = await page.goto(base + url, { waitUntil: 'networkidle', timeout: 60000 });
    if (res && res.status() >= 400) note(`page${res.status()}`, url);
    await page.waitForTimeout(400);
    const text = await page.evaluate(() => document.body ? document.body.innerText : '');
    const m = text.match(BAD_TEXT);
    if (m) {
      const at = text.indexOf(m[0]);
      note('bad-text', text.slice(Math.max(0, at - 40), at + 40).replace(/\s+/g, ' '));
    }
  } catch (e) {
    note('goto', String(e.message).split('\n')[0]);
  }
  await page.close();
  problems.push(...here);
}

const viewers = [['owner', (k) => k], ['anonymous', null], ['stranger', (k) => (k === 'rival' ? 'player' : 'rival')]];
let opened = 0;
for (const [viewer, asKind] of viewers) {
  for (const [kind, id] of Object.entries(seed.islands)) {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
    if (asKind) await login(context, asKind(kind));
    for (const p of ISLAND_PAGES) {
      await open(context, `${kind}/${viewer}`, `/islands/${id}/${p}`);
      opened++;
    }
    await context.close();
  }
}
for (const kind of ['player', 'fresh']) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
  await login(context, kind);
  for (const p of SITE_PAGES) { await open(context, `site/${kind}`, p); opened++; }
  await context.close();
}
await browser.close();
console.log(`${opened} 画面を開いた。問題 ${problems.length} 件`);
for (const p of problems) console.log(`[${p.label}] ${p.kind}: ${p.text}  (${p.url})`);
if (outFile) fs.writeFileSync(outFile, JSON.stringify(problems, null, 1));
