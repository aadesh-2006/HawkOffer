/**
 * Browser Web Push notification client service (RFC 8291 / RFC 8292).
 * Manages service worker registration, push permission lifecycle,
 * and subscription synchronization with the HawkOffer backend.
 */

import { pushApi } from "./api";

export interface PushStatus {
  supported: boolean;
  permission: NotificationPermission;
  isSubscribed: boolean;
  isTauri: boolean;
  endpoint?: string;
  error?: string;
}

/** Check if running inside the Tauri native desktop wrapper. */
export function isTauriEnvironment(): boolean {
  if (typeof window === "undefined") return false;
  return "__TAURI_INTERNALS__" in window || "__TAURI__" in window;
}

/** Convert base64url string to Uint8Array for pushManager.subscribe() applicationServerKey. */
export function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = window.atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; ++i) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}

/** Check whether browser Web Push is supported in the current runtime environment. */
export function isWebPushSupported(): boolean {
  if (typeof window === "undefined" || typeof navigator === "undefined") return false;
  // Browser Web Push is for web browsers; Tauri desktop uses native OS toast notifications
  if (isTauriEnvironment()) return false;
  return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

/** Register the service worker if not already registered. */
export async function registerServiceWorker(): Promise<ServiceWorkerRegistration | null> {
  if (!isWebPushSupported()) return null;
  try {
    const reg = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
    await navigator.serviceWorker.ready;
    return reg;
  } catch (err) {
    console.error("Failed to register HawkOffer service worker:", err);
    return null;
  }
}

/** Inspect the current Web Push capability, permission, and server subscription status. */
export async function getPushStatus(): Promise<PushStatus> {
  const supported = isWebPushSupported();
  const isTauri = isTauriEnvironment();

  if (!supported) {
    return {
      supported: false,
      permission: typeof Notification !== "undefined" ? Notification.permission : "default",
      isSubscribed: false,
      isTauri,
    };
  }

  const permission = Notification.permission;
  try {
    const reg = await navigator.serviceWorker.getRegistration("/sw.js");
    if (!reg) {
      return { supported: true, permission, isSubscribed: false, isTauri: false };
    }
    const sub = await reg.pushManager.getSubscription();
    return {
      supported: true,
      permission,
      isSubscribed: sub !== null,
      isTauri: false,
      endpoint: sub?.endpoint,
    };
  } catch (err) {
    return {
      supported: true,
      permission,
      isSubscribed: false,
      isTauri: false,
      error: String(err),
    };
  }
}

/**
 * Request notification permission and subscribe the browser to Web Push notifications.
 * MUST be invoked in response to an explicit user interaction (e.g. clicking a button).
 */
export async function subscribeToPush(): Promise<{ success: boolean; error?: string }> {
  if (!isWebPushSupported()) {
    return {
      success: false,
      error: isTauriEnvironment()
        ? "Desktop application uses native notifications."
        : "Web Push is not supported by this browser.",
    };
  }

  // 1. Explicitly request browser notification permission
  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    return {
      success: false,
      error: permission === "denied"
        ? "Notification permission was denied in browser settings."
        : "Notification permission was dismissed.",
    };
  }

  // 2. Ensure service worker is ready
  const reg = await registerServiceWorker();
  if (!reg) {
    return { success: false, error: "Service worker registration failed." };
  }

  try {
    // 3. Fetch server public VAPID key
    const { public_key } = await pushApi.getVapidPublicKey();
    if (!public_key) {
      return { success: false, error: "Server VAPID public key not available." };
    }

    // 4. Subscribe via browser PushManager
    const applicationServerKey = urlBase64ToUint8Array(public_key);
    const subscription = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: applicationServerKey.buffer as ArrayBuffer,
    });

    const subJson = subscription.toJSON();
    if (!subJson.endpoint || !subJson.keys?.p256dh || !subJson.keys?.auth) {
      return { success: false, error: "Incomplete browser push subscription keys." };
    }

    // 5. Register with backend
    await pushApi.subscribe({
      endpoint: subJson.endpoint,
      keys: {
        p256dh: subJson.keys.p256dh,
        auth: subJson.keys.auth,
      },
      user_agent: navigator.userAgent,
    });

    return { success: true };
  } catch (err: unknown) {
    console.error("Push subscription error:", err);
    const msg = err instanceof Error ? err.message : String(err);
    return { success: false, error: `Failed to subscribe: ${msg}` };
  }
}

/** Unsubscribe browser and delete the push subscription from the backend. */
export async function unsubscribeFromPush(): Promise<{ success: boolean; error?: string }> {
  if (!isWebPushSupported()) {
    return { success: true };
  }

  try {
    const reg = await navigator.serviceWorker.getRegistration("/sw.js");
    if (reg) {
      const sub = await reg.pushManager.getSubscription();
      if (sub) {
        const endpoint = sub.endpoint;
        await sub.unsubscribe();
        try {
          await pushApi.unsubscribe(endpoint);
        } catch (apiErr) {
          console.warn("Backend unsubscribe sync error (ignored):", apiErr);
        }
      }
    }
    return { success: true };
  } catch (err: unknown) {
    console.error("Push unsubscribe error:", err);
    const msg = err instanceof Error ? err.message : String(err);
    return { success: false, error: `Failed to unsubscribe: ${msg}` };
  }
}
