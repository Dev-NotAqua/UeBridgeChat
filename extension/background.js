// Background script: the only part of the extension that talks to the bridge
// on 127.0.0.1 (a service worker on Chrome, an event page on Firefox).
// Fully event-driven and stateless, so it survives worker/page restarts.
// Content scripts ask through chrome.runtime.sendMessage({ type, payload }).

const DEFAULT_PORT = 17654;
const ROUTES = { status: "/status", prompt: "/prompt", run: "/run" };

async function bridge(path, payload) {
  const { port } = await chrome.storage.local.get({ port: DEFAULT_PORT });
  try {
    const response = await fetch(`http://127.0.0.1:${port}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {}),
    });
    if (response.status === 403) {
      return { error: "paired", detail: "The bridge is paired with another copy of this extension. Run start.bat --unpair." };
    }
    return await response.json();
  } catch (err) {
    return { error: "offline", detail: String(err) };
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const path = ROUTES[message && message.type];
  if (!path) return false;
  bridge(path, message.payload).then(sendResponse);
  return true; // keep the channel open for the async reply
});

// Seed the bridge port on first install so the popup never shows it blank.
chrome.runtime.onInstalled.addListener((details) => {
  if (details.reason === "install") {
    chrome.storage.local.get("port", (stored) => {
      if (stored.port === undefined) chrome.storage.local.set({ port: DEFAULT_PORT });
    });
  }
});
