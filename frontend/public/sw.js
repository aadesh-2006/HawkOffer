/**
 * HawkOffer Service Worker for Web Push Notifications (RFC 8291 / RFC 8292).
 * Handles incoming push events and notification click navigation.
 */

/* eslint-disable no-restricted-globals */

self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("push", (event) => {
  let payload = {};
  if (event.data) {
    try {
      payload = event.data.json();
    } catch (e) {
      payload = {
        title: "HawkOffer Job Alert",
        body: event.data.text(),
      };
    }
  }

  const title = payload.title || "New Matching Job Found!";
  const options = {
    body: payload.body || "A newly monitored career posting matches your profile criteria.",
    icon: "/favicon.ico",
    badge: "/favicon.ico",
    tag: payload.tag || "hawkoffer-job-alert",
    renotify: true,
    data: {
      url: payload.url || "/",
      job_id: payload.data?.job_id,
      application_url: payload.data?.application_url,
      notification_id: payload.data?.notification_id,
    },
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();

  const targetPath = event.notification.data?.url || "/";
  const targetUrl = new URL(targetPath, self.location.origin).href;

  event.waitUntil(
    self.clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then((clientList) => {
        // If an existing tab is open on this origin, focus it and navigate
        for (const client of clientList) {
          if (client.url.startsWith(self.location.origin) && "focus" in client) {
            client.navigate(targetUrl);
            return client.focus();
          }
        }
        // Otherwise open a new window
        if (self.clients.openWindow) {
          return self.clients.openWindow(targetUrl);
        }
        return null;
      })
  );
});
