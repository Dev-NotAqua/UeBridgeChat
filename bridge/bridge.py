"""UE Chat Bridge: local link between the browser extension and the Unreal editor.

    AI chat page  ->  browser extension  ->  this bridge (127.0.0.1)  ->  Unreal editor MCP server

The extension posts commands here; the bridge forwards them to the editor's
built-in Model Context Protocol server and returns the result as text (plus
images for screenshots). Standard library only, Python 3.9+.

usage:
  bridge.py                      run the bridge
  bridge.py --install <project>  copy the UE Chat Bridge plugin into an Unreal project
  bridge.py --unpair             forget the paired browser extension
  bridge.py --version            print the version and exit
"""

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

VERSION = "1.2.0"

# ---------------------------------------------------------------- paths

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CONFIG_PATH = os.path.join(HERE, "config.json")
PLUGIN_SOURCE = os.path.join(os.path.dirname(HERE), "unreal-plugin", "UEChatBridge")
LOGS_DIR = os.path.join(ROOT, "logs")

DEFAULTS = {
    "port": 17654,
    "unreal_mcp_url": "http://127.0.0.1:8000/mcp",
    # Toolsets that expose run_python(code), tried in order.
    "python_toolsets": ["uechat_tools.UEChatTools", "bw_tools.BWTools"],
    "max_output_chars": 8000,
    "call_timeout_seconds": 300,
    "paired_extension": "",
}

# Short budget for background probes (status polls, /prompt catalogue): these
# must never hold the call lock long enough to stall a real command.
PROBE_TIMEOUT_SECONDS = 8
PROMPT_LIST_TIMEOUT_SECONDS = 25
MAX_BODY_BYTES = 4 * 1024 * 1024

EXTENSION_ORIGIN = re.compile(r"^(chrome-extension|moz-extension)://[A-Za-z0-9\-]+$")
MCP_SETTINGS_SECTION = "[/Script/ModelContextProtocolEngine.ModelContextProtocolSettings]"
MCP_PROTOCOL_VERSION = "2025-06-18"
INFO_SCRIPT = (
    "import json, unreal\n"
    "w = unreal.EditorLevelLibrary.get_editor_world()\n"
    "print(json.dumps({'project': unreal.Paths.get_base_filename(unreal.Paths.get_project_file_path()),"
    " 'engine': unreal.SystemLibrary.get_engine_version().split('-')[0],"
    " 'level': w.get_name() if w else ''}))\n"
)

_log_file = None  # opened by _init_logging() below


# ---------------------------------------------------------------- config

def load_config():
    """Defaults overlaid with bridge/config.json. A corrupt file warns and
    falls back to defaults instead of killing the bridge at startup."""
    config = dict(DEFAULTS)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                config.update(loaded)
            else:
                say("config.json is not an object - using defaults")
        except (OSError, ValueError) as exc:
            say(f"config.json unreadable ({exc}) - using defaults")
    return config


def save_config(config):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


# ---------------------------------------------------------------- logging

def _init_logging():
    global _log_file
    try:
        os.makedirs(LOGS_DIR, exist_ok=True)
        _log_file = open(os.path.join(LOGS_DIR, "bridge_debug.log"), "a",
                         encoding="utf-8", errors="replace")
    except OSError:
        _log_file = None


def say(text):
    """One timestamped line to the console AND the debug log."""
    line = f"{time.strftime('%H:%M:%S')} {text}"
    print(line, flush=True)
    if _log_file:
        try:
            _log_file.write(f"{time.strftime('%Y-%m-%d')} {line}\n")
            _log_file.flush()
        except OSError:
            pass


def action_banner(lines):
    """A step the USER must physically go do, framed so it stands out from
    routine status noise in a scrolling terminal."""
    header = "ACTION NEEDED"
    width = max([len(header) + 8] + [len(ln) for ln in lines]) + 2
    top = f">>> {header} " + ">" * max(0, width - len(header) - 5)
    print()
    print(f"  {top.ljust(width)}")
    for ln in lines:
        print(f"  {ln.ljust(width)}")
    print(f"  {'>' * width}")
    print()
    say("ACTION NEEDED: " + " | ".join(lines))


_init_logging()


# ---------------------------------------------------------------- local ports (Windows)

def _port_owner(port):
    """(pid, name) of the process LISTENING on `port`, or None. Best effort."""
    if sys.platform != "win32":
        return None
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=8).stdout
    except Exception:
        return None
    pid = None
    for line in out.splitlines():
        if "LISTENING" in line and f":{port} " in line:
            parts = line.split()
            if parts and parts[-1].isdigit():
                pid = parts[-1]
                break
    if not pid:
        return None
    name = "?"
    try:
        ps = subprocess.run(["powershell", "-NoProfile", "-Command",
                             f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue).Name"],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=8).stdout.strip().splitlines()
        if ps and ps[0].strip():
            name = ps[0].strip()
    except Exception:
        pass
    return (pid, name)


def _process_cmdline(pid):
    if sys.platform != "win32":
        return ""
    try:
        return subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\" "
             "-ErrorAction SilentlyContinue).CommandLine"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=8).stdout.strip()
    except Exception:
        return ""


def reclaim_bridge_port(port):
    """Free our own listen port from a leftover bridge before binding.

    Relaunching start.bat while an earlier bridge still holds the port (window
    closed with X, crash, double double-click) otherwise kills the new bridge
    with a cryptic bind error - or, on Windows, both bind at once (SO_REUSEADDR)
    and the leftover silently steals half the connections. Only ever kills a
    process provably running bridge.py, never an unrelated app. Returns True
    if a leftover was killed.
    """
    owner = _port_owner(port)
    if not owner:
        return False
    pid, name = owner
    try:
        pid_i = int(pid)
    except (TypeError, ValueError):
        return False
    if pid_i == os.getpid():
        return False
    if "bridge.py" not in _process_cmdline(pid_i).lower():
        say(f"Port {port} is held by '{name}' (pid {pid_i}), not a UE Chat Bridge - leaving it alone.")
        return False
    say(f"Port {port} is held by a leftover bridge (pid {pid_i}) - closing it so this one can start.")
    try:
        subprocess.run(["taskkill", "/F", "/PID", str(pid_i)], capture_output=True, timeout=8)
    except Exception as exc:
        say(f"Could not close the leftover bridge (pid {pid_i}): {exc}")
        return False
    time.sleep(1.0)
    return True


def check_unreal_port(url):
    """Warn when something that is NOT Unreal holds the editor MCP port.

    A third-party tool binding 8000 first hijacks the channel: the bridge sees
    a wrong protocol or nothing at all. Log-only notice, never a prompt - the
    bridge must also run non-interactively.
    """
    try:
        port = urlparse(url).port or 80
    except Exception:
        return
    owner = _port_owner(port)
    if not owner:
        return
    pid, name = owner
    low = (name or "").lower()
    if "unreal" in low or "ue" in low or "editor" in low:
        return
    say(f"NOTE: port {port} (Unreal's MCP port) is held by '{name}' (pid {pid}). "
        "If the bridge reports 0 tools, close that app and restart the editor.")


# ---------------------------------------------------------------- editor client

class UnrealError(Exception):
    """The editor could not be reached or answered with a protocol error."""


class Unreal:
    """Client for the editor's MCP server (streamable HTTP). One call at a time."""

    def __init__(self, config):
        self.config = config
        self.lock = threading.Lock()
        self.session = None
        self.python_toolset = None
        self.info = {}
        self.info_time = 0.0

    def _post(self, body, timeout):
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": MCP_PROTOCOL_VERSION}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        request = urllib.request.Request(self.config["unreal_mcp_url"],
                                         data=json.dumps(body).encode("utf-8"),
                                         headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace").strip()
            session = response.headers.get("Mcp-Session-Id")
        if not raw:
            return session, None
        if raw.startswith("{"):
            return session, json.loads(raw)
        last = None
        for line in raw.splitlines():  # server-sent events: the last data line carries the response
            if line.startswith("data:"):
                last = json.loads(line[5:].strip())
        return session, last

    def _open_session(self):
        self.session = None
        session, _ = self._post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": MCP_PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": "ue-chat-bridge", "version": VERSION}}}, PROBE_TIMEOUT_SECONDS)
        self.session = session
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, PROBE_TIMEOUT_SECONDS)

    def _request(self, name, arguments, timeout=None):
        """One tools/call round-trip, reconnecting once on transport failure."""
        if timeout is None:
            timeout = self.config["call_timeout_seconds"]
        body = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": name, "arguments": arguments}}
        try:
            if not self.session:
                self._open_session()
            _, reply = self._post(body, timeout)
        except (urllib.error.URLError, OSError, ValueError):
            # The editor was restarted or the session expired: reconnect once.
            try:
                self._open_session()
                _, reply = self._post(body, timeout)
            except (urllib.error.URLError, OSError, ValueError) as exc:
                self.session = None
                raise UnrealError(f"Unreal editor not reachable at {self.config['unreal_mcp_url']} ({exc})") from exc
        if reply is None:
            raise UnrealError("Unreal returned an empty reply")
        if "error" in reply:
            raise UnrealError(json.dumps(reply["error"])[:2000])
        return reply.get("result", {})

    def tool(self, name, arguments, timeout=None):
        """Call a server tool. Returns (ok, text, images)."""
        result = self._request(name, arguments, timeout)
        texts, images = [], []
        for item in result.get("content", []) or []:
            if item.get("type") == "text":
                texts.append(unwrap(item["text"], images))
            elif item.get("type") == "image":
                images.append({"mime": item.get("mimeType", "image/png"), "data": item["data"]})
        if not texts and "structuredContent" in result:
            texts.append(unwrap(json.dumps(result["structuredContent"]), images))
        return not result.get("isError"), "\n".join(texts).strip(), images

    def call(self, toolset, tool, arguments, timeout=None):
        return self.tool("call_tool",
                         {"toolset_name": toolset, "tool_name": tool, "arguments": arguments},
                         timeout)

    def find_python(self, timeout=None):
        """First configured toolset whose run_python actually echoes. None if absent."""
        if self.python_toolset:
            return self.python_toolset
        for name in self.config["python_toolsets"]:
            ok, text, _ = self.call(name, "run_python", {"code": "print('uecb-ok')"}, timeout)
            if ok and "uecb-ok" in text:
                self.python_toolset = name
                return name
        return None

    def run_python(self, code, timeout=None):
        toolset = self.find_python(timeout)
        if not toolset:
            return False, ("Python is not available: the UE Chat Bridge plugin is not installed in this project. "
                            "Use `#!ue call` with the editor toolsets instead, or ask the user to run install_plugin.bat."), []
        return self.call(toolset, "run_python", {"code": code}, timeout)

    def port_open(self):
        url = urlparse(self.config["unreal_mcp_url"])
        try:
            with socket.create_connection((url.hostname, url.port or 80), timeout=0.5):
                return True
        except OSError:
            return False

    def status(self):
        """Connection summary for the extension. Never waits behind a running
        command, and probes on a short budget so a hung editor can't stall /run."""
        if not self.port_open():
            self.session = None
            self.python_toolset = None
            self.info = {}
            return {"connected": False}
        if time.time() - self.info_time > 20 and self.lock.acquire(blocking=False):
            try:
                ok, text, _ = self.run_python(INFO_SCRIPT, PROBE_TIMEOUT_SECONDS)
                self.info = json.loads(text.strip().splitlines()[-1]) if ok else {}
                self.info["python"] = bool(self.python_toolset)
            except (UnrealError, ValueError, IndexError):
                self.info = {"python": False}
            finally:
                self.info_time = time.time()
                self.lock.release()
        return dict(self.info, connected=True, busy=self.lock.locked())


# ---------------------------------------------------------------- result shaping (pure: covered by tests.py)

def unwrap(text, images):
    """Tool results arrive as JSON text: pull out the return value and lift any
    embedded images aside (replaced by placeholders in the text)."""
    try:
        data = json.loads(text)
    except ValueError:
        return text
    if isinstance(data, dict) and set(data) == {"returnValue"}:
        data = data["returnValue"]
    data = lift_images(data, images)
    if data is None:
        return "(done)"
    return data if isinstance(data, str) else json.dumps(data, indent=1, ensure_ascii=False)


def lift_images(node, images):
    if isinstance(node, dict):
        if isinstance(node.get("data"), str) and str(node.get("mimeType", "")).startswith("image/"):
            images.append({"mime": node["mimeType"], "data": node["data"]})
            return f"<image {len(images)} attached>"
        return {key: lift_images(value, images) for key, value in node.items()}
    if isinstance(node, list):
        return [lift_images(value, images) for value in node]
    return node


def clip(text, limit):
    """Cap a tool result so giant outputs can't freeze the chat page's composer
    (which inserts text line-by-line on the main thread). Head + tail are kept
    so the model still sees the start and the end."""
    lines = text.splitlines()
    if len(lines) > 1200:
        head, tail = 900, 300
        text = ("\n".join(lines[:head])
                + f"\n... [{len(lines) - head - tail} lines cut] ...\n"
                + "\n".join(lines[-tail:]))
    if len(text) <= limit:
        return text
    head, tail = limit * 2 // 3, limit // 3
    return f"{text[:head]}\n... [{len(text) - head - tail} characters cut] ...\n{text[-tail:]}"


def short_type(schema):
    if not isinstance(schema, dict):
        return "any"
    if "enum" in schema:
        return "|".join(str(v) for v in schema["enum"][:8])
    kind = schema.get("type", "any")
    if kind == "array":
        return short_type(schema.get("items")) + "[]"
    if kind == "object":
        props = schema.get("properties", {}) or {}
        if set(props) == {"refPath"}:
            return "{refPath}"
        if props:
            return "{" + ", ".join(f"{k}: {short_type(v)}" for k, v in list(props.items())[:12]) + "}"
        return schema.get("title", "object")
    return kind


def first_sentence(text):
    text = " ".join(str(text).split())
    cut = text.find(". ")
    return text if cut < 0 or cut > 160 else text[:cut + 1]


def render_toolset(text):
    """A describe_toolset JSON reply -> one compact signature line per tool."""
    try:
        data = json.loads(text)
    except ValueError:
        return text
    lines = [f"{data.get('name', '')}: {first_sentence(data.get('description', ''))}"]
    for tool in data.get("tools", []) or []:
        schema = tool.get("inputSchema", {}) or {}
        required = set(schema.get("required", []) or [])
        props = schema.get("properties", {}) or {}
        params = [f"{name}{'' if name in required else '?'}: {short_type(spec)}"
                  for name, spec in props.items()]
        name = tool.get("name", "").split(".")[-1]
        lines.append(f"- {name}({', '.join(params)}) :: {first_sentence(tool.get('description', ''))}")
    return "\n".join(lines)


def render_toolsets(text):
    """list_toolsets prints '- name: description' with wrapped lines: keep one
    line per toolset so the system prompt stays compact."""
    lines = []
    for block in re.split(r"\n(?=- )", text):
        if block.startswith("- "):
            name, _, description = block[2:].partition(":")
            lines.append(f"- {name.strip()}: {first_sentence(description)}")
    return "\n".join(lines) or text


# ---------------------------------------------------------------- commands

def execute(unreal, command, limit):
    """Run one command from the extension. ALWAYS returns a reply dict - success
    or structured error - so the agentic loop never hangs waiting."""
    kind = command.get("kind")
    started = time.time()
    images = []
    try:
        with unreal.lock:
            if kind == "python":
                ok, text, images = unreal.run_python(command.get("code", ""))
            elif kind == "call":
                ok, text, images = unreal.call(command["toolset"], command["tool"],
                                               command.get("args") or {})
            elif kind == "toolsets":
                ok, text, _ = unreal.tool("list_toolsets", {})
                text = render_toolsets(text)
            elif kind == "describe":
                ok, text, _ = unreal.tool("describe_toolset", {"toolset_name": command["toolset"]})
                text = render_toolset(text) if ok else text
            elif kind == "screenshot":
                ok, text, images = screenshot(unreal)
            else:
                ok, text = False, f"Unknown command kind: {kind}"
    except UnrealError as exc:
        ok, text = False, str(exc)
    except KeyError as exc:
        ok, text = False, f"Command is missing {exc}"
    except Exception as exc:  # never let one bad tool kill the HTTP reply
        ok, text = False, f"Bridge error while running {kind}: {exc}"
    seconds = round(time.time() - started, 2)
    say(f"{'ok ' if ok else 'ERR'} {kind} {command.get('tool', '')} ({seconds}s)")
    return {"ok": ok, "output": clip(text or "(no output)", limit), "seconds": seconds,
            "images": [f"data:{i['mime']};base64,{i['data']}" for i in images]}


def screenshot(unreal):
    """Viewport capture, falling back to a full-editor capture.

    CaptureViewport's optional struct params (e.g. annotations) have no
    default on some engine builds, so the call is rejected even though the
    params are marked optional. CaptureEditorImage takes no arguments and
    always works, so it is the fallback, not the failure.
    """
    toolset = "EditorToolset.EditorAppToolset"
    ok, text, _ = unreal.call(toolset, "GetCameraTransform", {})
    args = {"bShowUI": False}
    if ok:
        try:
            transform = json.loads(text)
        except ValueError:
            transform = None
        if isinstance(transform, dict):
            args["captureTransform"] = transform
    ok, text, images = unreal.call(toolset, "CaptureViewport", args)
    if ok and images:
        return True, "Viewport screenshot attached.", images[:1]
    ok2, text2, images2 = unreal.call(toolset, "CaptureEditorImage", {})
    if ok2 and images2:
        return True, ("Editor screenshot attached "
                      "(viewport capture is unavailable in this engine build)."), images2[:1]
    return False, text or text2 or "No image returned.", []


PROMPT = """You are now connected to a live Unreal Engine editor running on my PC, through a tool called UE Chat Bridge.
{context}

HOW YOU ACT
You act by writing command blocks. A command block is a fenced code block whose FIRST line starts with `#!ue` and whose LAST line is `#!ue end`. My tool runs every command block in your message inside the editor, in order, and then sends you a message that starts with `UE RESULT`. That message is the only source of truth about what happened.

COMMANDS

1) Python inside the editor (full `unreal` module, runs on the game thread). Use print() for everything you want to see.
```python
#!ue python
import unreal
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
print(len(actors), "actors")
#!ue end
```

2) Call one editor tool with JSON arguments.
```
#!ue call <toolset name> <tool name>
{{"argument": "value"}}
#!ue end
```

3) List the editor toolsets, or show the tools of one toolset with their parameters.
```
#!ue toolsets
#!ue end
```
```
#!ue describe <toolset name>
#!ue end
```

4) See the level viewport (an image is attached to my reply when this chat accepts images).
```
#!ue screenshot
#!ue end
```

RULES
- After writing command blocks, stop and wait for my `UE RESULT` message. Never guess or invent a result.
- Work in small steps: inspect first, then change, then verify. At most {max_blocks} command blocks per message.
- If a result is an error, read it, fix the command and try again. Do not repeat a failing command unchanged.
- Python: no input(), no infinite loops, no sleeping, no quit. Keep scripts short; variables persist between scripts.
- Do not touch files or programs outside the Unreal project unless I ask for it.
- Use `#!ue describe` before the first `#!ue call` to a toolset, so the argument names are right.
- When the task is finished, answer normally with no command block.

Reply now with one short sentence saying you are ready, then wait for my task."""


def build_prompt(unreal, max_blocks):
    status = unreal.status()
    if not status.get("connected"):
        return None
    parts = []
    if status.get("project"):
        parts.append(f"Project: {status['project']}. Engine: Unreal Engine {status.get('engine', '?')}. Open level: {status.get('level', '?')}.")
    if not status.get("python"):
        parts.append("Python commands are NOT available in this project: use `#!ue call`, `#!ue toolsets` and `#!ue describe` only.")
    try:
        with unreal.lock:
            ok, text, _ = unreal.tool("list_toolsets", {}, PROMPT_LIST_TIMEOUT_SECONDS)
        if ok:
            names = [line[2:].split(":")[0].strip() for line in text.splitlines() if line.startswith("- ")]
            # The raw catalogue mixes real toolset ids ("X.Y") with bare
            # continuation bullets, so list only clean dotted ids.
            names = [n for n in names if "." in n and " " not in n and "," not in n]
            parts.append("Editor toolsets available to `#!ue call`: " + ", ".join(names) + ".")
    except UnrealError:
        pass
    return PROMPT.format(context="\n".join(parts), max_blocks=max_blocks)


# ---------------------------------------------------------------- HTTP layer

class Handler(BaseHTTPRequestHandler):
    server_version = "UEChatBridge/" + VERSION
    unreal = None
    config = None

    def log_message(self, *args):
        pass

    def reply(self, code, payload, origin=None):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(body)

    def caller(self):
        """Origin of a trusted caller, or None. Only the paired browser
        extension may use the bridge: a web page cannot forge the Origin
        header, and the Host check stops DNS rebinding."""
        host = (self.headers.get("Host") or "").split(":")[0]
        origin = self.headers.get("Origin") or ""
        if host not in ("127.0.0.1", "localhost") or not EXTENSION_ORIGIN.match(origin):
            return None
        paired = self.config.get("paired_extension")
        if not paired:
            self.config["paired_extension"] = origin
            save_config(self.config)
            say(f"Paired with browser extension {origin}")
            return origin
        if origin != paired:
            say(f"Refused another extension ({origin}). Run start.bat --unpair to pair again.")
            return None
        return origin

    def do_OPTIONS(self):
        origin = self.caller()
        if not origin:
            return self.reply(403, {"error": "forbidden"})
        self.reply(200, {}, origin)

    def do_GET(self):
        self.reply(200, {"name": "UE Chat Bridge", "version": VERSION})

    def _read_json(self, origin):
        """Request body as a dict, or None after replying 400/413."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length < 0 or length > MAX_BODY_BYTES:
            self.reply(413 if length > MAX_BODY_BYTES else 400, {"error": "bad body"}, origin)
            return None
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self.reply(400, {"error": "bad json"}, origin)
            return None

    def do_POST(self):
        origin = self.caller()
        if not origin:
            return self.reply(403, {"error": "forbidden"})
        data = self._read_json(origin)
        if data is None or not isinstance(data, dict):
            if data is not None:
                self.reply(400, {"error": "bad json"}, origin)
            return
        path = self.path.rstrip("/")
        if path == "/status":
            return self.reply(200, {"version": VERSION, "unreal": self.unreal.status()}, origin)
        if path == "/prompt":
            prompt = build_prompt(self.unreal, int(data.get("max_blocks") or 3))
            if prompt is None:
                return self.reply(200, {"error": "Unreal editor is not running or its MCP server is off."}, origin)
            return self.reply(200, {"prompt": prompt}, origin)
        if path == "/run":
            return self.reply(200, execute(self.unreal, data, self.config["max_output_chars"]), origin)
        self.reply(404, {"error": "not found"}, origin)


# ---------------------------------------------------------------- project install

REQUIRED_PLUGINS = ("ModelContextProtocol", "PythonScriptPlugin", "AllToolsets")


def patch_uproject(uproject_file):
    """Enable the editor MCP plugins in the .uproject so the bridge can attach.
    Never disables anything; safe to re-run."""
    with open(uproject_file, encoding="utf-8-sig") as f:
        data = json.load(f)
    for name in REQUIRED_PLUGINS:
        found = next((p for p in data.get("Plugins", []) if p.get("Name") == name), None)
        if found:
            found["Enabled"] = True
            print(f"Enabled plugin: {name}")
        else:
            data.setdefault("Plugins", []).append({"Name": name, "Enabled": True})
            print(f"Added plugin: {name}")
    with open(uproject_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def patch_mcp_ini(project_dir):
    """Turn on MCP auto-start in the project settings (created if missing)."""
    ini = os.path.join(project_dir, "Config", "DefaultEditorPerProjectUserSettings.ini")
    existing = open(ini, encoding="utf-8-sig").read() if os.path.exists(ini) else ""
    if MCP_SETTINGS_SECTION not in existing:
        os.makedirs(os.path.dirname(ini), exist_ok=True)
        with open(ini, "a", encoding="utf-8") as f:
            f.write(f"\n{MCP_SETTINGS_SECTION}\nServerPortNumber=8000\nServerUrlPath=/mcp\nbAutoStartServer=True\n")
        print("MCP server auto-start added to", ini)
    elif "bAutoStartServer=True" not in existing:
        print("Check that bAutoStartServer=True is set in", ini)


def install_plugin(target):
    """Copy the plugin into an Unreal project and switch on the editor's MCP server."""
    target = os.path.abspath(target.strip().strip('"'))
    if not os.path.exists(target):
        sys.exit(f"Not found: {target}\nPass your .uproject file or its folder.")
    project_dir = os.path.dirname(target) if target.lower().endswith(".uproject") else target
    try:
        names = os.listdir(project_dir)
    except OSError as exc:
        sys.exit(f"Cannot read {project_dir} ({exc})")
    if not any(name.lower().endswith(".uproject") for name in names):
        sys.exit(f"No .uproject file in {project_dir}")
    if not os.path.isdir(PLUGIN_SOURCE):
        sys.exit(f"Plugin source missing: {PLUGIN_SOURCE}\nRe-extract the UEChatBridge ZIP.")
    destination = os.path.join(project_dir, "Plugins", "UEChatBridge")
    shutil.copytree(PLUGIN_SOURCE, destination, dirs_exist_ok=True)
    print("Plugin copied to", destination)
    uproject = next((os.path.join(project_dir, n) for n in names
                     if n.lower().endswith(".uproject")), None)
    if uproject:
        patch_uproject(uproject)
        print("Patched:", uproject)
    patch_mcp_ini(project_dir)
    print("Done. Restart the Unreal editor (needs Unreal Engine 5.8 or newer).")


# ---------------------------------------------------------------- entry point

def main():
    parser = argparse.ArgumentParser(description="UE Chat Bridge: AI chats drive the Unreal editor.")
    parser.add_argument("--install", metavar="PROJECT",
                        help="install the plugin into an Unreal project (.uproject or its folder)")
    parser.add_argument("--unpair", action="store_true",
                        help="forget the paired browser extension, then keep serving")
    parser.add_argument("--port", type=int, help="port to listen on")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    args = parser.parse_args()
    if args.install:
        return install_plugin(args.install)
    config = load_config()
    if args.unpair:
        config["paired_extension"] = ""
        save_config(config)
        print("Extension pairing cleared: the next extension that connects will be paired.")
    port = args.port or int(os.environ.get("UECB_PORT") or config["port"])
    Handler.config = config
    Handler.unreal = Unreal(config)
    # Proactive (not only on bind failure): on Windows, SO_REUSEADDR lets two
    # bridges bind the SAME port, so a leftover would otherwise go unnoticed
    # and steal half the connections. Reclaim first, then bind.
    reclaim_bridge_port(port)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        sys.exit(f"Cannot listen on 127.0.0.1:{port} ({exc}). Is another app using it?")
    say(f"UE Chat Bridge {VERSION}  -  listening on 127.0.0.1:{port}")
    check_unreal_port(config["unreal_mcp_url"])
    if Handler.unreal.port_open():
        say("Unreal editor: reachable - open your project and start a session in the browser.")
    else:
        action_banner([
            "Open your Unreal project (UE 5.8+) with the editor MCP server ON.",
            "Fast path: run install_plugin.bat once, then restart the editor.",
            "This window stays yellow until the editor is reachable.",
        ])
        say(f"Unreal editor: not found yet at {config['unreal_mcp_url']} (open your project)")
    print()
    print("  ############################################################")
    print("  ##                                                        ##")
    print("  ##   KEEP THIS WINDOW OPEN - the chat stops working        ##")
    print("  ##   if you close it. Minimize it and leave it running.    ##")
    print("  ##                                                        ##")
    print("  ############################################################")
    print()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBridge stopped.")


if __name__ == "__main__":
    main()
