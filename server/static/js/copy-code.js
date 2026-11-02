// 手引きのコードと頼む文の枠（pre.code・pre.prompt）に「コピー」ボタンを付ける。
// 押すと枠の中の文をそのままクリップボードに写す。写せないブラウザでは枠の中を選んだ状態にする。
(function () {
  const COPY = "コピー";
  const DONE = "コピーしました";
  const FAILED = "選んだので Ctrl+C（Mac は ⌘+C）で写してください";

  function select(pre) {
    const range = document.createRange();
    range.selectNodeContents(pre);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  }

  async function copy(pre, button) {
    const text = pre.textContent;
    try {
      await navigator.clipboard.writeText(text);
      button.textContent = DONE;
    } catch (_error) {
      select(pre);
      button.textContent = FAILED;
    }
    setTimeout(() => {
      button.textContent = COPY;
    }, 2000);
  }

  for (const pre of document.querySelectorAll("pre.code, pre.prompt")) {
    const box = document.createElement("div");
    box.className = "copy-box";
    pre.parentNode.insertBefore(box, pre);
    box.appendChild(pre);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "copy-button";
    button.textContent = COPY;
    button.addEventListener("click", () => copy(pre, button));
    box.appendChild(button);
  }
})();
