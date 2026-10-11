# Changelog

## 1.2.1 — Firefox support (extension only; bridge stays 1.2.0)
- New: `extension/manifest-firefox.json` (MV3 `background.scripts` event page —
  Firefox has no `service_worker`) + `build-firefox.py`, which assembles
  `dist/firefox/` and fails loudly if the Chrome/Firefox manifests drift apart.
- The background script was already stateless/event-driven, so it runs
  unchanged as a Firefox event page. No bridge or plugin changes.
- `build-firefox.py --xpi` zips a versioned `.xpi` for "Install Add-on From
  File" (regular Firefox still requires Mozilla signing for it — README has
  the signing matrix).
- README: Firefox temporary-install steps + the `--unpair` caveat after
  browser restarts (temporary ids change, and the bridge pairs to the id).

## 1.2.0 — cleanup + tests
- Bridge: status probes and `/prompt` catalogue run on short budgets (8s/25s)
  so a hung editor can no longer stall commands behind the call lock.
- Bridge: every `/run` always replies — unexpected tool errors return a
  structured error instead of killing the connection.
- Bridge: 4MB request-body cap, corrupt-`config.json` fallback, friendlier
  `--install` errors, `--version` flag, type-hint/docstring cleanup.
- Extension: pure parser extracted to `parse.js` with 21 node self-tests
  (`node parse.test.js`); `content.js` rewired to use it.
- Extension: popup shows bridge version + Test button; port seeded on install;
  legacy `chat.openai.com` support; instant repaint on tab switch.
- Plugin: version logging on register (`v1.2.0`).
- New: `bridge/tests.py` — 20 Python self-tests (`python -m unittest`).
- New: `.gitignore` (logs, bytecode, local bridge config).

## 1.1.0 — ZeroScript hardening pass
- Bridge: self-healing port (reclaims its own leftover, proactive for
  Windows SO_REUSEADDR double-bind), `ACTION NEEDED` banner, `KEEP OPEN`
  banner, file logging, `--install` patches `.uproject` plugins, Unreal-port
  squatter notice, 1200-line output cap.
- Extension: ChatGPT CodeMirror-safe block reading, background-tab pause,
  `arena.ai` + `deepseek.com` coverage, popup settings page, generated icons.
- `start.bat`: robust Python discovery with winget fallback.
- `README.md` setup guide.

## 1.0.0 — initial
- `bridge.py`: stdlib-only HTTP bridge (extension ↔ editor MCP over
  streamable HTTP), pairing, `python/call/toolsets/describe/screenshot`.
- `extension/`: multi-chat content script with approval panel.
- `unreal-plugin/UEChatBridge`: editor-side `run_python` toolset.
