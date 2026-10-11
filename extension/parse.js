// UECBParse: pure command-block parsing. Zero DOM, zero chrome.* APIs: the
// content script extracts a block's text from the page; everything here
// operates on that text. Loaded before content.js (see manifest.json) and
// covered by parse.test.js (`node parse.test.js`).
var UECBParse = (() => {
  "use strict";

  const HEAD = /^\s*#!\s*ue\s+(\w+)[ \t]*(.*)$/i;
  const END = /^\s*#!\s*ue\s+end\s*$/i;
  const KINDS = ["python", "call", "toolsets", "describe", "screenshot"];
  // A tripwire for the obvious cases, not a sandbox: Python in the editor can
  // do anything the user can.
  const RISKY = [
    { re: /\b(?:import|from)\s+(?:os|subprocess|shutil|socket|ctypes|winreg|urllib|http|requests|pathlib|tempfile|glob|importlib|webbrowser)\b/, why: "imports a module that reaches outside the editor" },
    { re: /\bos\.|\bsubprocess\b|\bshutil\./, why: "uses the operating system" },
    { re: /\b(?:open|eval|exec|compile|__import__)\s*\(/, why: "opens files or runs generated code" },
    { re: /execute_console_command|PythonScriptLibrary/, why: "runs console commands" },
    { re: /delete_asset|delete_directory|delete_loaded_asset|consolidate_assets/, why: "deletes project assets" },
  ];

  // Collapse a block to one trimmed line per line: the identity used to tell
  // "a block this script sent itself" apart from "a block the AI wrote".
  function squash(s) {
    return s.split("\n").map((l) => l.trim()).filter(Boolean).join("\n");
  }

  // null when the text is not a command block; otherwise
  // { complete, command | error, title }. `complete` is false while the reply
  // is still streaming (no `#!ue end` line yet) - callers must wait, never run.
  function parseBlock(raw) {
    const lines = raw.split("\n");
    let start = -1;
    for (let i = 0; i < Math.min(lines.length, 5); i++) {
      if (HEAD.test(lines[i])) { start = i; break; }
    }
    if (start < 0) return null;
    const [, kindRaw, argRaw] = lines[start].match(HEAD);
    const kind = kindRaw.toLowerCase();
    if (kind === "end") return null;
    let end = -1;
    for (let i = lines.length - 1; i > start; i--) {
      if (END.test(lines[i])) { end = i; break; }
    }
    const body = lines.slice(start + 1, end < 0 ? undefined : end).join("\n");
    const arg = argRaw.trim();
    const parsed = { complete: end > 0, title: kind, command: null, error: null };
    if (!KINDS.includes(kind)) {
      parsed.error = `Unknown command "#!ue ${kind}". Valid commands: ${KINDS.join(", ")}.`;
    } else if (kind === "python") {
      parsed.command = { kind, code: body };
      if (!body.trim()) parsed.error = "The python block is empty.";
    } else if (kind === "call") {
      let [toolset, tool] = arg.split(/\s+/);
      if (toolset && !tool && toolset.includes(".")) {
        tool = toolset.slice(toolset.lastIndexOf(".") + 1);
        toolset = toolset.slice(0, toolset.lastIndexOf("."));
      }
      parsed.title = `call ${tool || "?"}`;
      if (!toolset || !tool) parsed.error = "Write the header as: #!ue call <toolset name> <tool name>";
      else {
        try {
          parsed.command = { kind, toolset, tool, args: body.trim() ? JSON.parse(body) : {} };
        } catch (err) {
          parsed.error = `The arguments are not valid JSON (${err.message}).`;
        }
      }
    } else if (kind === "describe") {
      parsed.title = `describe ${arg}`;
      if (!arg) parsed.error = "Write the header as: #!ue describe <toolset name>";
      else parsed.command = { kind, toolset: arg };
    } else {
      parsed.command = { kind };
    }
    return parsed;
  }

  // "" when harmless, otherwise the reason to ask the user before running.
  function riskOf(command) {
    if (!command || command.kind !== "python") return "";
    const hit = RISKY.find((r) => r.re.test(command.code));
    return hit ? hit.why : "";
  }

  return { HEAD, END, KINDS, RISKY, squash, parseBlock, riskOf };
})();
