"""UE Chat Bridge bootstrap: registers the run_python toolset with the editor's MCP server."""
import unreal

try:
    import uechat_tools

    unreal.ToolsetRegistry.register_toolset_class(uechat_tools.UEChatTools)
    unreal.log(f"[UEChatBridge] toolset registered (v{uechat_tools._VERSION})")
except Exception as exc:  # the editor must still start if the toolset registry is unavailable
    unreal.log_warning(f"[UEChatBridge] toolset not registered: {exc}")
