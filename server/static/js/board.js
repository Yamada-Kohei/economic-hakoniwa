// 掲示板（スレッドの画面）: >>番号の浮き出し、番号を押して返信、いいね・通報・NG。
// 書き込みの中身は画面を出すときにサーバーが作り、ここは見た目の動きと API の呼び出しだけを受け持つ。

function csrfToken() {
  const input = document.querySelector("input[name=csrfmiddlewaretoken]");
  if (input) return input.value;
  const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

async function send(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
    body: JSON.stringify(body || {}),
  });
  let data = {};
  try {
    data = await res.json();
  } catch {
    data = {};
  }
  return { ok: res.ok, data };
}

const touchOnly = window.matchMedia("(hover: none)").matches;
let pop = null;

function closePop() {
  if (pop) pop.remove();
  pop = null;
}

function placePop(anchor) {
  const rect = anchor.getBoundingClientRect();
  pop.style.left = `${Math.max(8, Math.min(rect.left + window.scrollX, window.scrollX + document.documentElement.clientWidth - pop.offsetWidth - 8))}px`;
  const below = rect.bottom + window.scrollY + 4;
  const above = rect.top + window.scrollY - pop.offsetHeight - 4;
  pop.style.top = `${rect.top > pop.offsetHeight + 8 ? above : below}px`;
}

function plainPost(post) {
  const root = document.createElement("article");
  root.className = "bpost";
  const head = document.createElement("header");
  head.className = "bpost-head";
  const parts = [`${post.number}`, post.name + (post.trip ? ` ◆${post.trip}` : ""), post.created_at.replace("T", " ").slice(0, 16)];
  if (post.id) parts.push(`ID:${post.id}`);
  head.textContent = parts.join("　");
  const body = document.createElement("div");
  body.className = "bbody";
  body.style.whiteSpace = "pre-wrap";
  body.textContent = post.hidden ? "（非表示）" : post.body === null ? "（NG の書き込み）" : post.body;
  root.append(head, body);
  return root;
}

async function showPop(anchor) {
  closePop();
  const number = anchor.dataset.n;
  const target = document.getElementById(`p${number}`);
  let content;
  if (target && target.closest(".bthread") && (!anchor.dataset.thread || anchor.dataset.thread === target.closest(".bthread").dataset.thread)) {
    content = target.cloneNode(true);
    content.removeAttribute("id");
    content.querySelectorAll("[id]").forEach((el) => el.removeAttribute("id"));
    content.querySelector(".bpost-foot")?.remove();
  } else if (anchor.dataset.thread) {
    const res = await fetch(`/api/board/threads/${anchor.dataset.thread}/posts/${number}`, {
      headers: { Accept: "application/json" },
    });
    if (!res.ok) return;
    content = plainPost(await res.json());
  } else {
    return;
  }
  closePop();
  pop = document.createElement("div");
  pop.className = "bpop";
  pop.append(content);
  if (touchOnly) {
    const go = document.createElement("a");
    go.className = "bpop-go";
    go.href = anchor.getAttribute("href");
    go.textContent = "その書き込みへ移動";
    go.dataset.go = "1";
    pop.append(go);
  }
  document.body.append(pop);
  placePop(anchor);
}

function jump(anchor) {
  const target = document.getElementById(`p${anchor.dataset.n}`);
  if (!target) return false;
  target.scrollIntoView({ block: "start" });
  target.classList.add("flash");
  setTimeout(() => target.classList.remove("flash"), 2500);
  history.replaceState(null, "", `#p${anchor.dataset.n}`);
  return true;
}

document.addEventListener("mouseover", (event) => {
  if (touchOnly) return;
  const anchor = event.target.closest("a.anchor");
  if (anchor) showPop(anchor);
  else if (!event.target.closest(".bpop")) closePop();
});

document.addEventListener("click", async (event) => {
  const anchor = event.target.closest("a.anchor");
  if (anchor && !event.target.closest(".bpop")) {
    if (touchOnly) {
      event.preventDefault();
      await showPop(anchor);
    } else if (jump(anchor)) {
      event.preventDefault();
      closePop();
    }
    return;
  }
  if (event.target.closest("a[data-go]")) {
    closePop();
    return;
  }
  if (pop && !event.target.closest(".bpop")) closePop();

  const button = event.target.closest("[data-act]");
  if (!button) return;
  const box = button.closest(".bactions");
  const head = button.closest(".bpost");
  const thread = box?.dataset.thread || document.querySelector(".bthread")?.dataset.thread;
  const number = box?.dataset.n || head?.dataset.n;
  const act = button.dataset.act;
  if (act === "reply") {
    event.preventDefault();
    const field = document.querySelector(".bwrite textarea[name=body]");
    if (!field) return;
    field.value += `${field.value && !field.value.endsWith("\n") ? "\n" : ""}>>${number}\n`;
    field.focus();
    field.scrollIntoView({ block: "center" });
  } else if (act === "like") {
    const { ok, data } = await send(`/api/board/threads/${thread}/posts/${number}/like`);
    if (!ok) return alert(data.error || "いいねできませんでした");
    button.classList.toggle("on", data.liked);
    button.setAttribute("aria-pressed", String(data.liked));
    button.querySelector(".bcount").textContent = data.likes;
  } else if (act === "report") {
    const reason = prompt("通報の理由（任意。荒らし・不適切な書き込みを管理者に知らせます。ゲームの不具合は「不具合を報告」へ）", "");
    if (reason === null) return;
    const { ok, data } = await send(`/api/board/threads/${thread}/posts/${number}/report`, { reason });
    alert(ok ? "通報しました。管理者が確かめます。" : data.error || "通報できませんでした");
  } else if (act === "ng-person" || act === "ng-ident") {
    const person = act === "ng-person";
    if (!confirm(person ? "この人の書き込みを NG にします（誰かは分かりません）。よろしいですか？" : "この ID を NG にします（その日の分だけ効きます）。よろしいですか？")) return;
    const { ok, data } = await send("/api/board/ng", { kind: person ? "person" : "ident", thread: Number(thread), number: Number(number) });
    if (ok) location.reload();
    else alert(data.error || "NG にできませんでした");
  }
});
