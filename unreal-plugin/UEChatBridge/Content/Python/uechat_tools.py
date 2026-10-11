"""UE Chat Bridge editor toolset: runs Python sent by the bridge, with the full `unreal` module.

Registered from init_unreal.py. Scripts share one globals dict, so a variable or helper defined by
one command is still there for the next.
"""

import contextlib
import io
import traceback

import toolset_registry
import unreal

_VERSION = "1.2.0"
_MAX_OUTPUT = 60000
_GLOBALS = {"unreal": unreal, "__name__": "__uechat__"}


@unreal.uclass()
class UEChatTools(unreal.ToolsetDefinition):
    """UE Chat Bridge: run Python in the editor on behalf of the connected AI chat."""

    @toolset_registry.tool_call
    @staticmethod
    def run_python(code: str) -> str:
        """Run Python inside the editor with the full unreal module.

        Args:
            code: Python source to execute.

        Returns:
            Everything the code printed, followed by the traceback if it raised.
            Output is capped at 60000 chars (newest kept); BaseExceptions
            (including a stray quit()/SystemExit) are caught so a bad script
            can never close the editor.
        """
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                exec(compile(code, "<chat>", "exec"), _GLOBALS)
        except BaseException:  # SystemExit from a stray quit() must not close the editor
            buf.write("\n" + traceback.format_exc())
        out = buf.getvalue()
        return out[-_MAX_OUTPUT:] if len(out) > _MAX_OUTPUT else out
