"""UE Chat Bridge self-tests: `python -m unittest` inside bridge/ (or run start.bat --test).

Covers the pure result-shaping helpers plus execute()/install on stubs - no
Unreal editor needed. The live HTTP + MCP round-trips are covered by the manual
smoke test in README (they need a real editor + browser).
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bridge


class ClipTests(unittest.TestCase):
    def test_short_text_untouched(self):
        self.assertEqual(bridge.clip("hello", 8000), "hello")

    def test_char_cap_keeps_head_and_tail(self):
        out = bridge.clip("abcdefghij", 6)
        self.assertIn("cut", out)
        self.assertTrue(out.startswith("abcd"))
        self.assertTrue(out.endswith("ij"))

    def test_line_cap(self):
        text = "\n".join(f"line {i}" for i in range(2000))
        out = bridge.clip(text, 10 ** 9)
        self.assertIn("lines cut", out)
        self.assertIn("line 0", out)
        self.assertIn("line 1999", out)


class UnwrapTests(unittest.TestCase):
    def test_plain_text_passthrough(self):
        self.assertEqual(bridge.unwrap("just text", []), "just text")

    def test_return_value_unboxed(self):
        self.assertEqual(bridge.unwrap('{"returnValue": 42}', []), "42")

    def test_null_becomes_done(self):
        self.assertEqual(bridge.unwrap('{"returnValue": null}', []), "(done)")

    def test_images_lifted(self):
        images = []
        out = bridge.unwrap(
            '{"returnValue": {"shot": {"mimeType": "image/png", "data": "AAA"}}}', images)
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["mime"], "image/png")
        self.assertIn("<image 1 attached>", out)

    def test_nested_images(self):
        images = []
        bridge.lift_images([{"m": {"mimeType": "image/jpeg", "data": "B"}}], images)
        self.assertEqual(len(images), 1)


class RenderTests(unittest.TestCase):
    def test_first_sentence(self):
        self.assertEqual(bridge.first_sentence("One. Two."), "One.")
        self.assertEqual(bridge.first_sentence("No stop"), "No stop")

    def test_short_type(self):
        self.assertEqual(bridge.short_type({"type": "string"}), "string")
        self.assertEqual(bridge.short_type({"type": "array", "items": {"type": "integer"}}), "integer[]")
        self.assertEqual(bridge.short_type({"enum": ["a", "b"]}), "a|b")
        self.assertEqual(bridge.short_type(None), "any")
        self.assertEqual(bridge.short_type({"type": "object", "properties": None}), "object")

    def test_render_toolset(self):
        text = json.dumps({"name": "ts", "description": "Does things. More.",
                           "tools": [{"name": "ts.Foo", "description": "Foo it.",
                                      "inputSchema": {"properties": {"x": {"type": "string"}},
                                                      "required": ["x"]}}]})
        out = bridge.render_toolset(text)
        self.assertIn("- Foo(x: string)", out)

    def test_render_toolset_tolerates_none(self):
        text = json.dumps({"name": "ts", "description": "d",
                           "tools": [{"name": "T", "inputSchema": None}]})
        self.assertIn("- T()", bridge.render_toolset(text))
        self.assertEqual(bridge.render_toolset("not json"), "not json")

    def test_render_toolsets(self):
        out = bridge.render_toolsets("- a: Alpha does x\n continued\n- b: Beta")
        self.assertEqual(out.splitlines(), ["- a: Alpha does x continued", "- b: Beta"])


class FakeUnreal:
    """Minimal stand-in for Unreal: records calls, never touches a socket."""

    def __init__(self):
        import threading
        self.lock = threading.Lock()
        self.calls = []

    def run_python(self, code, timeout=None):
        self.calls.append(("python", code))
        return True, "printed", []

    def call(self, toolset, tool, arguments, timeout=None):
        self.calls.append((toolset, tool, arguments))
        return True, "called", []

    def tool(self, name, arguments, timeout=None):
        self.calls.append((name, arguments))
        if name == "list_toolsets":
            return True, "- a: Alpha", []
        return True, "{}", []


class ExecuteTests(unittest.TestCase):
    def test_unknown_kind_is_structured_error(self):
        out = bridge.execute(FakeUnreal(), {"kind": "nope"}, 8000)
        self.assertFalse(out["ok"])
        self.assertIn("Unknown command kind", out["output"])
        self.assertIn("seconds", out)

    def test_python_and_call_dispatch(self):
        fake = FakeUnreal()
        self.assertTrue(bridge.execute(fake, {"kind": "python", "code": "x"}, 8000)["ok"])
        self.assertTrue(bridge.execute(
            fake, {"kind": "call", "toolset": "t", "tool": "f", "args": {}}, 8000)["ok"])
        self.assertEqual(fake.calls[0], ("python", "x"))

    def test_transport_error_never_raises(self):
        class Broken(FakeUnreal):
            def run_python(self, code, timeout=None):
                raise bridge.UnrealError("down")
        out = bridge.execute(Broken(), {"kind": "python", "code": "x"}, 8000)
        self.assertFalse(out["ok"])
        self.assertIn("down", out["output"])

    def test_unexpected_error_never_raises(self):
        class Broken(FakeUnreal):
            def run_python(self, code, timeout=None):
                raise RuntimeError("boom")
        out = bridge.execute(Broken(), {"kind": "python", "code": "x"}, 8000)
        self.assertFalse(out["ok"])
        self.assertIn("boom", out["output"])

    def test_screenshot_falls_back_to_editor_capture(self):
        class Shot(FakeUnreal):
            def call(self, toolset, tool, arguments, timeout=None):
                if tool == "CaptureEditorImage":
                    return True, "shot", [{"mime": "image/png", "data": "AAA"}]
                return True, "{}", []
        ok, text, images = bridge.screenshot(Shot())
        self.assertTrue(ok)
        self.assertEqual(len(images), 1)
        self.assertIn("Editor screenshot", text)


class InstallTests(unittest.TestCase):
    def test_install_patches_everything(self):
        with tempfile.TemporaryDirectory() as d:
            uproject_path = os.path.join(d, "Game.uproject")
            with open(uproject_path, "w") as f:
                json.dump({"FileVersion": 3}, f)
            bridge.PLUGIN_SOURCE = os.path.join(
                os.path.dirname(os.path.abspath(bridge.__file__)),
                "..", "unreal-plugin", "UEChatBridge")
            bridge.install_plugin(uproject_path)
            with open(uproject_path) as f:
                uproject = json.load(f)
            names = {p["Name"]: p["Enabled"] for p in uproject["Plugins"]}
            for required in bridge.REQUIRED_PLUGINS:
                self.assertTrue(names.get(required), required)
            ini_path = os.path.join(d, "Config", "DefaultEditorPerProjectUserSettings.ini")
            with open(ini_path) as f:
                self.assertIn("bAutoStartServer=True", f.read())
            self.assertTrue(os.path.exists(
                os.path.join(d, "Plugins", "UEChatBridge", "UEChatBridge.uplugin")))

    def test_install_rejects_garbage(self):
        with self.assertRaises(SystemExit):
            bridge.install_plugin(os.path.join(tempfile.gettempdir(), "nope-missing-uecb"))
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit):
                bridge.install_plugin(d)  # no .uproject inside

    def test_patch_uproject_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "G.uproject")
            with open(target, "w") as f:
                json.dump({"Plugins": [{"Name": "ModelContextProtocol", "Enabled": False}]}, f)
            bridge.patch_uproject(target)
            bridge.patch_uproject(target)
            with open(target) as f:
                plugins = json.load(f)["Plugins"]
            self.assertEqual(len(plugins), len(bridge.REQUIRED_PLUGINS))
            self.assertTrue(all(p["Enabled"] for p in plugins))


if __name__ == "__main__":
    unittest.main()
