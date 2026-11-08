"use strict";

const letterForm = document.querySelector("[data-letter-send]");
if (letterForm) {
  const sendButton = letterForm.querySelector('button[type="submit"]');
  let sending = false;
  letterForm.addEventListener("submit", event => {
    if (sending) {
      event.preventDefault();
      return;
    }
    sending = true;
    sendButton.disabled = true;
  });
  // 戻る操作で入力中のページが復元された場合にも送信できるようにする。
  window.addEventListener("pageshow", () => {
    sending = false;
    sendButton.disabled = false;
  });
}
