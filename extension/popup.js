// Popup: bridge status + the same settings the content script reads.
const DEFAULTS = { port: 17654, mode: "ask-python", maxBlocks: 3, attachImages: true };

function ask(type, payload) {
  return new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage({ type, payload }, (answer) => {
        resolve(chrome.runtime.lastError || !answer ? { error: "offline" } : answer);
      });
    } catch (err) {
      resolve({ error: "offline" });
    }
  });
}

function paint(status) {
  const dot = document.getElementById("dot");
  const line = document.getElementById("status");
  document.getElementById("ver").textContent = status && status.version ? `v${status.version}` : "";
  if (!status || status.error === "offline") {
    dot.className = "dot";
    line.textContent = "Bridge offline — run start.bat in the UEChatBridge folder.";
    return;
  }
  if (status.error) {
    dot.className = "dot warn";
    line.textContent = status.detail || status.error;
    return;
  }
  const ue = status.unreal || {};
  if (!ue.connected) {
    dot.className = "dot warn";
    line.textContent = "Bridge OK, Unreal not found — open your project (UE 5.8+, MCP server on).";
    return;
  }
  dot.className = "dot ready";
  const what = ue.project ? `${ue.project} · UE ${ue.engine || "?"}` : "Unreal connected";
  line.textContent = ue.python === false
    ? `${what}. Python is off here — run install_plugin.bat to enable it.`
    : `${what}. Ready — open a supported AI chat and Start session.`;
}

document.addEventListener("DOMContentLoaded", async () => {
  const stored = await chrome.storage.local.get(DEFAULTS);
  document.getElementById("port").value = stored.port;
  document.getElementById("mode").value = stored.mode;
  document.getElementById("maxBlocks").value = stored.maxBlocks;
  document.getElementById("attachImages").checked = !!stored.attachImages;
  paint(await ask("status"));
  document.getElementById("save").addEventListener("click", async () => {
    await chrome.storage.local.set({
      port: parseInt(document.getElementById("port").value, 10) || DEFAULTS.port,
      mode: document.getElementById("mode").value,
      maxBlocks: Math.min(10, Math.max(1, parseInt(document.getElementById("maxBlocks").value, 10) || 3)),
      attachImages: document.getElementById("attachImages").checked,
    });
    flash("Saved.");
    paint(await ask("status"));
  });
  document.getElementById("test").addEventListener("click", async () => {
    const t0 = Date.now();
    const status = await ask("status");
    paint(status);
    if (!status.error) flash(`Bridge answered in ${Date.now() - t0}ms.`);
  });

  function flash(text) {
    const note = document.getElementById("saved");
    note.textContent = text;
    setTimeout(() => { note.textContent = ""; }, 1500);
  }
});
