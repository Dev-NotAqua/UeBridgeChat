// UE Chat Bridge content script: watches the AI's replies for command blocks, runs them in the
// Unreal editor through the bridge and sends the results back as the next chat message.
//
// A command block is a code block whose first line starts with "#!ue" and whose last line is
// "#!ue end". The end line is what proves the block finished streaming before anything runs.

(() => {
  if (window.__uecbLoaded) return;
  window.__uecbLoaded = true;

  const DEFAULTS = { mode: "ask-python", maxSteps: 30, maxBlocks: 3, attachImages: true, selectors: {}, panelPos: null, collapsed: false };
  const TICK_MS = 700;
  const QUIET_MS = 1500;       // no page changes for this long = the reply has finished
  const FORCE_MS = 15000;      // run finished blocks anyway on pages that never go quiet
  const INCOMPLETE_MS = 8000;  // a block without its end line for this long is treated as cut off
  const REMIND_EVERY = 8;
  const BLOCKS = "pre, .cm-content";
  const REMINDER = "\n\n(Reminder: act only through command blocks that start with `#!ue ...` and end with `#!ue end`, then wait for UE RESULT.)";
  // Command-block parsing lives in parse.js (UECBParse, unit-tested) - this
  // file only handles the page: finding blocks, running them, the panel.

  let settings = Object.assign({}, DEFAULTS);
  let adapter = uecbAdapter();
  const S = { active: false, paused: false, pauseWhy: "", busy: false, steps: 0, pythonGranted: false, status: null, log: [], notice: "", approval: null };
  const seen = new WeakMap();   // block element -> { len, parsed, since }
  const ownBlocks = new Set();  // code blocks this script sent itself (the examples in the start message)
  let lastChange = Date.now();

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  function ask(type, payload) {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage({ type, payload }, (answer) => {
          if (chrome.runtime.lastError || !answer) resolve({ error: "extension", detail: "Extension was reloaded. Refresh this page." });
          else resolve(answer);
        });
      } catch (err) {
        resolve({ error: "extension", detail: "Extension was reloaded. Refresh this page." });
      }
    });
  }

  // ---------------------------------------------------------------- command blocks

  function candidates() {
    return [...document.querySelectorAll(BLOCKS)].filter((el) => !el.closest("#uecb-host") && !(el.parentElement && el.parentElement.closest(BLOCKS)));
  }

  function textOf(el) {
    // ChatGPT renders code blocks with CodeMirror: one element per line and no
    // newline characters, so a valid command block reads back as a single
    // giant line (and long lines render truncated). Read the editor document
    // line-by-line instead of trusting the flattened text.
    const cmLines = el.querySelectorAll ? el.querySelectorAll(".cm-line") : [];
    if (cmLines && cmLines.length) {
      return [...cmLines].map((l) => l.innerText || l.textContent || "").join("\n").replace(/\r/g, "");
    }
    const code = el.matches("pre") ? el.querySelector("code") || el : el;
    const cmHost = code.querySelector ? code.querySelector(".cm-content") : null;
    if (cmHost) {
      const hostLines = cmHost.querySelectorAll(".cm-line");
      if (hostLines.length) {
        return [...hostLines].map((l) => l.innerText || l.textContent || "").join("\n").replace(/\r/g, "");
      }
    }
    return (code.innerText || code.textContent || "").replace(/ /g, " ").replace(/\r/g, "");
  }

  // ---------------------------------------------------------------- the loop

  function tick() {
    if (!S.active || S.paused || S.busy || S.approval) return;
    // A backgrounded tab throttles timers and rendering, which makes DOM reads
    // unreliable (duplicate feedback, running a tool blind). Pause with no time
    // limit until this tab is the foreground tab again, then resume in place.
    if (document.hidden) return;
    const now = Date.now();
    const pending = [];
    for (const el of candidates()) {
      if (el.dataset.uecb) continue;
      const len = el.textContent.length;
      let state = seen.get(el);
      if (!state || state.len !== len) {
        const raw = textOf(el);
        state = { len, parsed: UECBParse.parseBlock(raw), since: now };
        seen.set(el, state);
        if (state.parsed && ownBlocks.has(UECBParse.squash(raw))) { el.dataset.uecb = "own"; continue; }
      }
      if (state.parsed) pending.push({ el, parsed: state.parsed, since: state.since });
    }
    if (!pending.length) return;
    const settled = (ms) => pending.every((p) => now - p.since > ms);
    const forced = settled(FORCE_MS);
    if (!forced && (adapter.isStreaming() === true || now - lastChange < QUIET_MS || !settled(QUIET_MS))) return;
    if (pending.some((p) => !p.parsed.complete)) {
      if (settled(INCOMPLETE_MS)) cutOff(pending);
      return;
    }
    runBatch(pending);
  }

  async function cutOff(pending) {
    const choice = await approve({
      heading: "A command looks cut off",
      detail: "The reply ended without the `#!ue end` line. Running half a command can do damage.",
      preview: pending.map((p) => textOf(p.el)).join("\n\n").slice(0, 4000),
      buttons: [["skip", "Skip it"], ["run", "Run anyway"]],
    });
    if (choice === "run") return runBatch(pending);
    for (const p of pending) p.el.dataset.uecb = "skipped";
    log({ title: "cut-off command skipped", state: "skipped", output: "" });
    render();
  }

  async function permit(command) {
    if (settings.mode === "auto") return true;
    const why = UECBParse.riskOf(command);
    if (settings.mode === "ask-python") {
      if (command.kind !== "python") return true;
      if (S.pythonGranted && !why) return true;
    }
    const python = command.kind === "python";
    const choice = await approve({
      heading: python ? "Run this Python in Unreal?" : `Run ${command.kind}${command.tool ? " " + command.tool : ""}?`,
      detail: why ? `Careful: this script ${why}.` : python ? "Python can change anything in the project and on this PC." : "",
      danger: !!why,
      preview: python ? command.code : JSON.stringify(command, null, 1),
      buttons: python && !why && settings.mode === "ask-python"
        ? [["skip", "Skip"], ["run", "Run once"], ["grant", "Run + allow Python this session"]]
        : [["skip", "Skip"], ["run", "Run"]],
    });
    if (choice === "grant") S.pythonGranted = true;
    return choice !== "skip";
  }

  async function runBatch(pending) {
    S.busy = true;
    S.notice = "";
    render();
    const batch = pending.slice(0, settings.maxBlocks);
    for (const extra of pending.slice(settings.maxBlocks)) extra.el.dataset.uecb = "skipped";
    const parts = [];
    const images = [];
    let failed = false;
    for (let i = 0; i < batch.length; i++) {
      const { el, parsed } = batch[i];
      const label = `--- command ${i + 1} of ${batch.length}: ${parsed.title}`;
      el.dataset.uecb = "done";
      if (failed) {
        parts.push(`${label} · NOT RUN ---\nSkipped because an earlier command in this message failed.`);
        continue;
      }
      if (parsed.error) {
        parts.push(`${label} · ERROR ---\n${parsed.error}`);
        log({ title: parsed.title, state: "error", output: parsed.error });
        failed = true;
        continue;
      }
      if (!(await permit(parsed.command))) {
        parts.push(`${label} · NOT RUN ---\nThe user declined to run this command. Ask what they want instead.`);
        log({ title: parsed.title, state: "skipped", output: "Declined." });
        failed = true;
        continue;
      }
      const entry = log({ title: parsed.title, state: "run", output: "" });
      render();
      const result = await ask("run", parsed.command);
      if (result.error) {
        // nothing ran: leave this block and the rest for when the bridge is back
        for (const later of batch.slice(i)) delete later.el.dataset.uecb;
        S.log.splice(S.log.indexOf(entry), 1);
        pause(result.error === "offline" ? "Bridge offline. Run start.bat, then Resume." : result.detail || result.error);
        S.busy = false;
        return render();
      }
      Object.assign(entry, { state: result.ok ? "ok" : "error", output: result.output, seconds: result.seconds });
      parts.push(`${label} · ${result.ok ? "OK" : "ERROR"} ---\n${result.output}`);
      if (settings.attachImages && result.images) images.push(...result.images);
      if (!result.ok) failed = true;
      render();
    }
    S.steps++;
    let message = `UE RESULT\n\n${parts.join("\n\n")}`;
    if (pending.length > batch.length) message += `\n\n(${pending.length - batch.length} more command blocks were ignored: at most ${settings.maxBlocks} per message.)`;
    if (S.steps % REMIND_EVERY === 0) message += REMINDER;
    await reply(message, images);
    if (S.steps >= settings.maxSteps) pause(`Paused after ${S.steps} steps. Resume to let it continue.`);
    S.busy = false;
    render();
  }

  // Put text (and images) into the site's message box and send it.
  async function reply(text, images) {
    const input = adapter.findInput();
    if (!input) {
      S.notice = "Message box not found on this page. Set the selectors in the extension popup.";
      return false;
    }
    for (const image of images || []) {
      try {
        await adapter.attachImage(input, image);
        await sleep(1500);
      } catch (err) { /* this site takes no images: the text still goes out */ }
    }
    if (!adapter.setInput(input, text)) {
      S.notice = "Could not type into the message box. Set the selectors in the extension popup.";
      return false;
    }
    await sleep(350);
    lastChange = Date.now();
    const sent = await adapter.send(input);
    if (!sent) S.notice = "The result is in the message box: press Enter to send it.";
    lastChange = Date.now();
    return sent;
  }

  async function startSession() {
    const answer = await ask("prompt", { max_blocks: settings.maxBlocks });
    if (answer.error || !answer.prompt) {
      S.notice = answer.detail || answer.error || "Bridge gave no start message.";
      return render();
    }
    for (const el of candidates()) el.dataset.uecb = "old";
    ownBlocks.clear();
    for (const match of answer.prompt.matchAll(/```[^\n]*\n([\s\S]*?)```/g)) ownBlocks.add(UECBParse.squash(match[1]));
    Object.assign(S, { active: true, paused: false, pauseWhy: "", steps: 0, pythonGranted: false, log: [], notice: "", busy: true });
    render();
    await reply(answer.prompt, []);
    S.busy = false;
    render();
  }

  function stopSession() {
    if (S.approval) S.approval.resolve("skip");
    Object.assign(S, { active: false, paused: false, pauseWhy: "", approval: null, notice: "" });
    render();
  }

  function pause(why) {
    S.paused = true;
    S.pauseWhy = why || "";
  }

  function log(entry) {
    S.log.unshift(entry);
    S.log.length = Math.min(S.log.length, 40);
    return entry;
  }

  // ---------------------------------------------------------------- panel

  const host = document.createElement("div");
  host.id = "uecb-host";
  const root = host.attachShadow({ mode: "open" });
  root.innerHTML = `<style>
    :host { all: initial; }
    .panel { position: fixed; top: 76px; right: 16px; width: 308px; z-index: 2147483600; font: 12.5px/1.45 "Segoe UI", system-ui, sans-serif;
      color: #e8e6e1; background: #16181d; border: 1px solid #2c3038; border-radius: 12px; box-shadow: 0 12px 34px rgba(0,0,0,.45); overflow: hidden; }
    header { display: flex; align-items: center; gap: 8px; padding: 9px 10px 9px 12px; cursor: move; user-select: none; background: #1c1f26; }
    .dot { width: 9px; height: 9px; border-radius: 50%; background: #6b7280; flex: none; }
    .dot.ready { background: #3ecf8e; box-shadow: 0 0 0 3px rgba(62,207,142,.18); }
    .dot.warn { background: #f5b83d; }
    .name { font-weight: 650; letter-spacing: .01em; }
    .sub { color: #9aa0ab; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 1; }
    button { font: inherit; color: inherit; background: #262a33; border: 1px solid #353a45; border-radius: 7px; padding: 5px 10px; cursor: pointer; }
    button:hover { background: #2e333e; }
    button.primary { background: #f5b83d; border-color: #f5b83d; color: #1a1300; font-weight: 650; }
    button.primary:hover { background: #ffc857; }
    button.danger { background: #c2412d; border-color: #c2412d; color: #fff; }
    button.icon { padding: 2px 8px; border: none; background: transparent; color: #9aa0ab; }
    button:disabled { opacity: .45; cursor: default; }
    .body { padding: 10px 12px 12px; display: grid; gap: 9px; }
    .collapsed .body { display: none; }
    .line { color: #b9bec8; }
    .row { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
    select { font: inherit; color: inherit; background: #262a33; border: 1px solid #353a45; border-radius: 7px; padding: 4px 6px; flex: 1; min-width: 0; }
    .notice { background: #2a2413; border: 1px solid #5a4a17; color: #f3d48a; border-radius: 8px; padding: 7px 9px; }
    .card { background: #1f232b; border: 1px solid #3a404c; border-radius: 9px; padding: 9px; display: grid; gap: 7px; }
    .card.danger { border-color: #c2412d; }
    .card h4 { margin: 0; font-size: 12.5px; }
    pre { margin: 0; max-height: 170px; overflow: auto; background: #0f1115; border-radius: 7px; padding: 7px 8px; font: 11.5px/1.4 Consolas, "Cascadia Mono", monospace; color: #cdd3de; white-space: pre-wrap; word-break: break-word; }
    .log { display: grid; gap: 4px; max-height: 230px; overflow: auto; }
    details { background: #1b1e25; border-radius: 7px; }
    summary { cursor: pointer; padding: 5px 8px; list-style: none; display: flex; gap: 7px; align-items: baseline; }
    summary::-webkit-details-marker { display: none; }
    summary .t { flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    summary .s { color: #7d8490; font-size: 11px; }
    .ok { color: #3ecf8e; } .error { color: #ff7a66; } .run { color: #f5b83d; } .skipped { color: #7d8490; }
    details pre { border-radius: 0 0 7px 7px; max-height: 140px; }
    .muted { color: #7d8490; font-size: 11.5px; }
  </style><div class="panel"><header></header><div class="body"></div></div>`;
  const panel = root.querySelector(".panel");
  const MARK = { ok: "✓", error: "✕", run: "…", skipped: "–" };

  function approve(request) {
    return new Promise((resolve) => {
      S.approval = Object.assign({}, request, { resolve });
      settings.collapsed = false;
      render();
    });
  }

  function statusView() {
    const st = S.status;
    if (!st || st.error === "offline") return { dot: "", sub: "bridge offline", line: "Run start.bat in the UE Chat Bridge folder." };
    if (st.error) return { dot: "warn", sub: "not paired", line: st.detail || st.error };
    const ue = st.unreal || {};
    if (!ue.connected) return { dot: "warn", sub: "Unreal not found", line: "Open your Unreal project. The editor's MCP server must be on (install_plugin.bat does that)." };
    const sub = ue.project ? `${ue.project} · UE ${ue.engine}` : "Unreal connected";
    if (ue.python === false) return { dot: "ready", sub, line: "Connected. Python is off in this project: run install_plugin.bat to enable it." };
    return { dot: "ready", sub, line: `Connected to ${adapter.name}. ${S.active ? "" : "Start a session, then tell the AI what to build."}` };
  }

  function render() {
    const view = statusView();
    panel.classList.toggle("collapsed", !!settings.collapsed);
    root.querySelector("header").innerHTML =
      `<span class="dot ${view.dot}"></span><span class="name">UE Chat Bridge</span><span class="sub">${esc(view.sub)}</span>` +
      `<button class="icon" data-act="collapse" title="Collapse">${settings.collapsed ? "▸" : "▾"}</button>`;
    const ready = view.dot === "ready";
    let html = `<div class="line">${esc(view.line)}</div>`;
    if (!S.active) {
      html += `<div class="row"><button class="primary" data-act="start" ${ready ? "" : "disabled"}>Start session</button></div>`;
    } else {
      const state = document.hidden ? "paused (tab in background)" : S.busy ? "working…" : S.paused ? "paused" : "watching the chat";
      html += `<div class="row"><button data-act="${S.paused ? "resume" : "pause"}">${S.paused ? "Resume" : "Pause"}</button>` +
        `<button data-act="stop">Stop</button><span class="muted">step ${S.steps} · ${state}</span></div>`;
      if (S.paused && S.pauseWhy) html += `<div class="notice">${esc(S.pauseWhy)}</div>`;
    }
    html += `<div class="row"><select data-act="mode" title="When to ask before running a command">` +
      [["ask-python", "Ask before Python"], ["ask-all", "Ask before every command"], ["auto", "Auto: never ask"]]
        .map(([value, text]) => `<option value="${value}" ${settings.mode === value ? "selected" : ""}>${text}</option>`).join("") + `</select></div>`;
    if (S.notice) html += `<div class="notice">${esc(S.notice)}</div>`;
    if (S.approval) {
      const a = S.approval;
      html += `<div class="card ${a.danger ? "danger" : ""}"><h4>${esc(a.heading)}</h4>` + (a.detail ? `<div class="line">${esc(a.detail)}</div>` : "") +
        `<pre>${esc(a.preview)}</pre><div class="row">` +
        a.buttons.map(([value, text], i) => `<button data-choice="${value}" class="${i === a.buttons.length - 1 ? (a.danger ? "danger" : "primary") : ""}">${esc(text)}</button>`).join("") + `</div></div>`;
    }
    if (S.log.length) {
      html += `<div class="log">` + S.log.map((e) =>
        `<details><summary><span class="${e.state}">${MARK[e.state]}</span><span class="t">${esc(e.title)}</span>` +
        `<span class="s">${e.seconds !== undefined ? e.seconds + "s" : ""}</span></summary><pre>${esc(e.output || "")}</pre></details>`).join("") + `</div>`;
    }
    root.querySelector(".body").innerHTML = html;
  }

  root.addEventListener("click", (event) => {
    const choice = event.target.closest("[data-choice]");
    if (choice && S.approval) {
      const { resolve } = S.approval;
      S.approval = null;
      render();
      return resolve(choice.dataset.choice);
    }
    const act = event.target.closest("[data-act]");
    if (!act || act.tagName === "SELECT") return;
    if (act.dataset.act === "start") startSession();
    if (act.dataset.act === "stop") stopSession();
    if (act.dataset.act === "pause") { pause(""); render(); }
    if (act.dataset.act === "resume") { S.paused = false; S.pauseWhy = ""; S.steps = 0; render(); }
    if (act.dataset.act === "collapse") { settings.collapsed = !settings.collapsed; chrome.storage.local.set({ collapsed: settings.collapsed }); render(); }
  });
  root.addEventListener("change", (event) => {
    if (event.target.dataset.act === "mode") {
      settings.mode = event.target.value;
      chrome.storage.local.set({ mode: settings.mode });
    }
  });

  // drag the panel by its header
  root.querySelector("header").addEventListener("pointerdown", (down) => {
    if (down.target.closest("button")) return;
    const box = panel.getBoundingClientRect();
    const dx = down.clientX - box.left, dy = down.clientY - box.top;
    const move = (e) => place({ left: e.clientX - dx, top: e.clientY - dy });
    const up = () => {
      removeEventListener("pointermove", move);
      removeEventListener("pointerup", up);
      chrome.storage.local.set({ panelPos: settings.panelPos });
    };
    addEventListener("pointermove", move);
    addEventListener("pointerup", up);
  });

  function place(pos) {
    if (!pos) return;
    settings.panelPos = { left: Math.max(4, Math.min(innerWidth - 60, pos.left)), top: Math.max(4, Math.min(innerHeight - 40, pos.top)) };
    panel.style.left = settings.panelPos.left + "px";
    panel.style.top = settings.panelPos.top + "px";
    panel.style.right = "auto";
  }

  async function pollStatus() {
    S.status = await ask("status");
    render();
  }

  // ---------------------------------------------------------------- start

  new MutationObserver((records) => {
    for (const record of records) {
      const target = record.target.nodeType === 1 ? record.target : record.target.parentElement;
      if (target && target !== host && !host.contains(target)) { lastChange = Date.now(); return; }
    }
  }).observe(document.documentElement, { childList: true, subtree: true, characterData: true });

  chrome.storage.onChanged.addListener((changes) => {
    for (const key of Object.keys(changes)) if (key in DEFAULTS) settings[key] = changes[key].newValue;
    adapter = uecbAdapter(settings.selectors[location.hostname]);
    render();
  });

  // Repaint immediately on tab switch so the "paused (tab in background)"
  // state never lags one poll interval behind.
  document.addEventListener("visibilitychange", () => render());

  chrome.storage.local.get(DEFAULTS, (stored) => {
    settings = Object.assign({}, DEFAULTS, stored);
    adapter = uecbAdapter(settings.selectors[location.hostname]);
    document.documentElement.appendChild(host);
    place(settings.panelPos);
    render();
    pollStatus();
    setInterval(pollStatus, 4000);
    setInterval(tick, TICK_MS);
  });
})();
