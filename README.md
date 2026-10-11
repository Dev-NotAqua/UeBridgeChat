# UE Chat Bridge — free AI agent for Unreal Engine

Turn an online AI chat (ChatGPT, DeepSeek, Gemini, Claude, Kimi, Qwen, …) into an
**Unreal Engine agent**. Control the Unreal editor from your browser: run editor
Python, call editor tools, inspect actors, capture the viewport — all from a
normal AI chat. No API key, no terminal, no coding needed.

Inspired by [ZeroScript-Free](https://github.com/sebattfg/ZeroScript-Free) (the
same idea for Roblox Studio): online AI chat → browser extension → local bridge
on your PC → the engine's built-in MCP server.

```
AI chat (in your browser)  →  UE Chat Bridge extension  →  Bridge (your PC :17654)  →  Unreal editor MCP (:8000/mcp)
```

## Supported AI chats

ChatGPT, DeepSeek, Gemini, Claude, Qwen, Kimi, GLM (chat.z.ai), Meta AI,
Copilot, Grok, Mistral, LMArena / Arena, Perplexity, Poe, HuggingChat.
If a site redesigns its chat box, the extension falls back to generic detection,
and you can override the selectors in the popup.

## Requirements

- Windows (macOS/Linux: run `python3 bridge/bridge.py` manually)
- **Unreal Engine 5.8+** (older versions have no editor MCP server)
- Chrome or Edge
- Python 3.9+ (`start.bat` finds it or installs it via winget — no other
  libraries needed, the bridge is standard-library only)

## Setup

### 1. Enable the editor MCP server (one time per project)

**Easy path** — run once per Unreal project:

- Double-click `install_plugin.bat`, paste the path to your project's
  `.uproject` file (or pass it: `install_plugin.bat "C:\Path\MyGame.uproject"`).

This does three things: copies the `UEChatBridge` plugin into your project's
`Plugins/` folder (adds editor-side `run_python`), enables
`ModelContextProtocol` + `PythonScriptPlugin` + `AllToolsets` in the
`.uproject`, and turns on MCP auto-start in the project settings.
Then **restart the editor**.

**Manual path** — same result by hand:

1. In the editor: *Edit → Plugins* → enable **Unreal MCP**
   (`ModelContextProtocol`), **All Toolsets**, **Python**.
2. *Edit → Editor Preferences → Model Context Protocol* → check
   **Auto Start Server** (or run the console command
   `ModelContextProtocol.StartServer` each session).
3. Copy the `unreal-plugin/UEChatBridge` folder into your project's
   `Plugins/` folder and restart the editor (only needed for `#!ue python`;
   `#!ue call/describe/toolsets` work without it).

Verify: with the editor open, `http://127.0.0.1:8000/mcp` should answer.

### 2. Load the extension

**Chrome / Edge:**

- Go to `edge://extensions` (Edge) or `chrome://extensions` (Chrome)
- Enable **Developer mode** (top-right toggle)
- Click **Load unpacked** → select the `extension/` folder from this project

**Firefox — three ways, pick one:**

A. **Temporary (fastest, no signing):** run `python build-firefox.py`, then
   `about:debugging` → **This Firefox** → **Load Temporary Add-on** → open
   `dist/firefox/` and select `manifest.json`.

B. **From file (the menu in your screenshot):** run
   `python build-firefox.py --xpi`, then *Manage Your Extensions* (gear icon)
   → **Install Add-on From File…** → pick
   `dist/ue-chat-bridge-1.2.1.xpi`. This keeps the extension across restarts —
   BUT regular Firefox installs only **Mozilla-signed** `.xpi` files, so an
   unsigned local build is rejected here. Two options: submit the `.xpi` at
   [addons.mozilla.org](https://addons.mozilla.org) for signing (free,
   self-distribution keeps working with our `gecko.id`), or use route B in
   **Firefox Developer Edition / Nightly / ESR** with
   `xpinstall.signatures.required=false` in `about:config`.

- Notes: temporary add-ons (route A) get a fresh internal id on every browser
  restart, and the bridge pairs to that id — after restarting Firefox, run
  `start.bat --unpair` once so it pairs to the new id. Routes with a stable
  id (signed install) never need re-pairing.

### 3. Run the bridge

- Double-click **`start.bat`**. A small window opens — **keep it open**
  (minimize it). Closing it disconnects the chat.
- First run pairs the bridge with your extension automatically. If you later
  reinstall the extension, run `start.bat --unpair` once to pair again.

### 4. Start a session

Open any supported AI chat. The **UE Chat Bridge** panel appears (top-right).
Click **Start session** — the AI receives its instructions and replies with one
short "ready" sentence. Then tell it what to build.

> The panel dot mirrors the bridge: **green** = bridge + editor ready,
> **yellow** = bridge OK but the editor isn't reachable (open your project /
> check the MCP server), **grey** = bridge offline (run `start.bat`).

## What the AI can do

- Run editor Python with the full `unreal` module (`#!ue python`)
- Call any editor toolset tool (`#!ue call <toolset> <tool>` + JSON args)
- List toolsets and inspect their parameters (`#!ue toolsets`, `#!ue describe`)
- Capture the viewport (`#!ue screenshot`)
- Small steps, verify-as-it-goes, error self-correction — the system prompt
  enforces: inspect first, then change, then verify.

Python runs on the game thread: no `input()`, no infinite loops, no sleeping.
Destructive asset operations ask for your approval first (configurable:
*Ask before Python / Ask before everything / Auto*).

## Troubleshooting

| Symptom | Fix |
|---|---|
| Grey dot / "Bridge offline" | Run `start.bat`, keep its window open |
| Yellow dot / "Unreal not found" | Open your project in UE 5.8+, check the MCP server is on (§1), restart the editor after running `install_plugin.bat` |
| "Python is not available" | Run `install_plugin.bat` for that project and restart the editor; or stick to `#!ue call` tools |
| AI answers in plain text, no commands | Remind it to act through `#!ue … #!ue end` blocks, or start a new session |
| Page was reloaded / extension updated mid-run | Reload the chat page (F5) and start a new session |
| Port 17654 already in use | Just run `start.bat` again — it replaces its own leftover; if another app holds the port, stop that app |
| Logs | `logs/start.log` + `logs/bridge_debug.log` next to `start.bat` |

## Self-tests (no editor needed)

```bat
cd bridge
python -m unittest            :: 20 bridge tests (result shaping, execute, install)
cd ..\extension
node parse.test.js            :: 21 parser tests (command blocks, risk tripwire)
```

The live HTTP + MCP round-trips need a real editor + browser: with the editor
open and the bridge running, the popup's **Test** button should answer in
milliseconds, and starting a session in any supported chat should get the
AI's one-sentence "ready" reply.

## Files

```
UEChatBridge/
  start.bat               robust launcher (finds/installs Python, starts bridge)
  install_plugin.bat      installs the editor plugin into a .uproject
  bridge/bridge.py        local HTTP bridge: extension ↔ Unreal MCP (stdlib only)
  bridge/tests.py         20 self-tests: python -m unittest (no editor needed)
  extension/              Chrome/Edge extension (multi-chat content script)
    manifest.json  background.js  parse.js  sites.js  content.js
    popup.html  popup.js  parse.test.js (node parse.test.js)
    manifest-firefox.json (Firefox MV3: background.scripts + gecko id)
  build-firefox.py        assembles dist/firefox/ from extension/ (sync-checked)
  dist/firefox/           generated Firefox package (git-ignored, do not edit)
  unreal-plugin/UEChatBridge/   editor plugin (run_python toolset)
  logs/                   start.log + bridge_debug.log (git-ignored)
  CHANGELOG.md            version history (current: 1.2.0)
```

## Credits

Architecture reference: [ZeroScript-Free](https://github.com/sebattfg/ZeroScript-Free)
by sebattfg (GPL-3.0) — browser extension + local bridge driving an engine's
built-in MCP server. This project is an independent implementation for Unreal
Engine, not a fork.
