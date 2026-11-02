self.addEventListener("push", (event) => {
  const data = event.data ? event.data.json() : {};
  event.waitUntil(self.registration.showNotification(data.title || "経済版箱庭諸国", {
    body: data.body || "公電が届きました",
    tag: "hakoniwa-letters",
    data: { url: "{{ inbox_url|escapejs }}" },
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const inbox = new URL("{{ inbox_url|escapejs }}", self.location.origin);
  event.waitUntil((async () => {
    // 受信箱のタブがあれば前に出す。ほかのタブは書きかけの公電や命令があるかもしれないので動かさない。
    const tabs = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    const tab = tabs.find((client) => new URL(client.url).pathname === inbox.pathname);
    if (tab) return tab.focus();
    return self.clients.openWindow(inbox.href);
  })());
});
