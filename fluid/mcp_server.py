"""MCP wrapper around the synchronous Fluid Decisions service."""
from __future__ import annotations

from typing import Any

try:
    from mcp.server.mcpserver import MCPServer
except ImportError:  # pragma: no cover - optional dependency
    MCPServer = None  # type: ignore[assignment,misc]

from .cli import _service


def build_server(service: Any | None = None) -> Any:
    if MCPServer is None:
        raise RuntimeError("MCP support is not installed; install fluid-decisions[mcp]")
    decisions = service or _service()
    server = MCPServer("fluid-decisions")

    @server.tool()
    def classify(state: Any, images: list[str] | None = None, audio: str | None = None) -> dict[str, Any]:
        return decisions.classify(state, images=images, audio=audio)

    @server.tool()
    def route(state: Any, destinations: dict[str, Any] | None = None, images: list[str] | None = None, audio: str | None = None) -> dict[str, Any]:
        return decisions.route(state, destinations=destinations, images=images, audio=audio)

    @server.tool(name="gate_tool")
    def gate_tool(tool_name: str, tool_input: dict[str, Any], user_request: str = "",
                  session_id: str | None = None, cwd: str | None = None) -> dict[str, Any]:
        return decisions.gate(tool_name, tool_input, user_request=user_request,
                              session_id=session_id, cwd=cwd)

    @server.tool(name="post_result")
    def post_result(result: str, session_id: str, tool_name: str = "",
                    user_request: str = "", source_path: str | None = None) -> dict[str, Any]:
        # A worker that cannot wrap the hook submits the result here; the response
        # carries redacted_text when a unit was confirmed (IP-007 §4), so the
        # worker releases that in place of the full result.
        return decisions.post_result(result, session_id=session_id, tool_name=tool_name,
                                     user_request=user_request, source_path=source_path)

    @server.tool()
    def verify_claims(source: str, claims: list[str]) -> dict[str, Any]:
        return decisions.verify(source, claims)

    @server.tool(name="check_constraints")
    def check_constraints(candidate: Any, constraints: list[dict[str, Any]],
                          images: list[str] | None = None) -> dict[str, Any]:
        # Score a candidate the caller already has against a closed list of
        # {id, rule} constraints (IP-009). No label proposes a fix.
        return decisions.constraints(candidate, constraints, images=images)

    @server.tool(name="check_validity")
    def check_validity(packet: Any, spec: dict[str, Any],
                       images: list[str] | None = None) -> dict[str, Any]:
        # Score a finished packet against a closed spec of checks (IP-009).
        # Validity is not support; support is the verify tool.
        return decisions.validity(packet, spec, images=images)

    @server.tool()
    def health() -> dict[str, Any]:
        return decisions.health()

    return server


def main() -> int:
    decisions = _service()
    try:
        # Warmup is best effort.  A down lane must leave a visible health/error
        # response rather than preventing MCP from starting indefinitely.
        try:
            decisions.warmup()
        except Exception:
            pass
        build_server(decisions).run(transport="stdio")
    finally:
        decisions.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
