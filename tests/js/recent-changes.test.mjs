// node --test tests/js/recent-changes.test.mjs
// API が選んだ主な変化を、画面側で選び直さずに並べることを確かめる。
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../../server/static/js/island/recent-changes.js", import.meta.url), "utf8")
  .replace(/^import .*;\r?\n/gm, "")
  .replace("export async function mountRecentChanges", "async function mountRecentChanges");

class Element {
  constructor(tagName) {
    this.tagName = tagName;
    this.children = [];
    this.hidden = true;
    this.value = "";
  }
  set textContent(value) { this.value = value; this.children = []; }
  get textContent() { return this.value + this.children.map((child) => child.textContent).join(""); }
  set innerHTML(_) { throw new Error("表示値は HTML として扱わない"); }
  replaceChildren(...children) { this.value = ""; this.children = [...children]; }
  append(...children) { this.children.push(...children); }
}

function environment({ data, fails = false, mounted = true } = {}) {
  const root = new Element("section");
  const list = new Element("ul");
  list.append(new Element("li"));
  root.dataset = { url: "/api/islands/17/summary" };
  root.querySelector = (selector) => {
    assert.equal(selector, "ul");
    return list;
  };
  const requests = [];
  const context = vm.createContext({
    document: {
      getElementById(id) {
        assert.equal(id, "recent-changes");
        return mounted ? root : null;
      },
      createElement: (tagName) => new Element(tagName),
    },
    async getJson(url) {
      requests.push(url);
      if (fails) throw new Error("通信に失敗しました");
      return data;
    },
  });
  vm.runInContext(source, context);
  return { root, list, requests, mount: () => context.mountRecentChanges() };
}

test("サーバーの並び順・行き先・前後の表示文をそのまま並べる", async () => {
  const changes = [
    { key: "treasury", label: "国庫", before: "¤9,000", now: "¤8,000", page: "fiscal" },
    { key: "population", label: "人口", before: "1,000人", now: "1,040人", page: "economy" },
    { key: "new_indicator", label: "新しい指標 <b>", before: "未満 & 10", now: "以上 > 20", page: "facilities" },
  ];
  const data = { island: { id: 17 }, notable_changes: changes };
  Object.defineProperty(data, "indicators", { get() { throw new Error("全指標から選び直さない"); } });
  const b = environment({ data });
  await b.mount();
  assert.deepEqual(b.requests, ["/api/islands/17/summary"]);
  assert.equal(b.root.hidden, false);
  assert.equal(b.list.children.length, 3, "前の表示を消して選ばれた行だけを出す");
  for (const [index, change] of changes.entries()) {
    const item = b.list.children[index];
    assert.equal(item.tagName, "li");
    assert.equal(item.children.length, 1);
    const link = item.children[0];
    assert.equal(link.tagName, "a");
    assert.equal(link.href, `/islands/17/${change.page}/`);
    assert.equal(link.textContent, `${change.label}: ${change.before} → ${change.now}（詳しく調べる）`);
    assert.equal(link.children.length, 0, "表示文にタグの形があっても文字として扱う");
  }
});

test("主な変化が空なら目安を超えた変化がないと知らせる", async () => {
  const b = environment({ data: {
    island: { id: 17 }, notable_changes: [],
    indicators: [{ key: "population", before: "0人", now: "100,000人" }],
  } });
  await b.mount();
  assert.equal(b.root.hidden, false);
  assert.equal(b.list.children.length, 1);
  assert.equal(b.list.textContent, "目安を超えた変化はありません");
  assert.equal(b.list.children[0].children.length, 0);
});

test("取得に失敗したら欄を開き、比較を読めなかったことを知らせる", async () => {
  const b = environment({ fails: true });
  await b.mount();
  assert.equal(b.root.hidden, false);
  assert.equal(b.list.textContent, "前のターンとの比較を読み込めませんでした。各画面で確認してください。");
  assert.equal(b.list.children.length, 0, "古い比較や途中のリンクを残さない");
});

test("比較欄がない画面では取得しない", async () => {
  const b = environment({ mounted: false });
  await b.mount();
  assert.deepEqual(b.requests, []);
});
