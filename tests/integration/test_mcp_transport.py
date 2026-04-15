"""Live MCP-transport integration test.

Spawns `mcp_server/server.py` in a subprocess via stdio, connects with
the official MCP Python client, lists the registered tools, and invokes
the cheapest content-only tool (`get_trace_summary`) end-to-end through
the real protocol.

Marked `slow` because subprocess + stdio handshake is heavier than the
mocked unit tests, and `e2e` would be wrong (no DB needed). Default
`pytest -m "not slow"` runs skip it; CI can opt in.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_PATH = REPO_ROOT / "mcp_server" / "server.py"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _have_mcp_client() -> bool:
    try:
        import mcp.client.session  # noqa: F401
        import mcp.client.stdio  # noqa: F401

        return True
    except Exception:
        return False


@pytest.mark.asyncio
@pytest.mark.skipif(not _have_mcp_client(), reason="mcp client SDK not installed")
async def test_mcp_server_handshake_lists_six_tools():
    """The server announces the same tools the README documents."""
    from mcp import StdioServerParameters
    from mcp.client.session import ClientSession
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER_PATH)],
        env=None,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_response = await session.list_tools()

    names = {t.name for t in tools_response.tools}
    expected = {
        "classify_agent_failure",
        "check_hallucination",
        "run_eval_benchmark",
        "get_trace_summary",
        "calibrate_judge",
        "run_multiagent_eval",
        "recommend_protocol",
    }
    assert expected.issubset(names), f"missing tools: {expected - names}"


@pytest.mark.asyncio
@pytest.mark.skipif(not _have_mcp_client(), reason="mcp client SDK not installed")
async def test_mcp_call_get_trace_summary_through_protocol():
    """Round-trip a real tool invocation via stdio JSON-RPC.

    `get_trace_summary` is sync, has no API-key requirements, and produces
    a deterministic shape — perfect happy-path transport check.
    """
    from mcp import StdioServerParameters
    from mcp.client.session import ClientSession
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER_PATH)],
        env=None,
    )
    payload = {
        "trace": [
            {"span_name": "n1", "duration_ms": 100, "status_code": "OK", "start_time": 1.0},
            {"span_name": "n2", "duration_ms": 200, "status_code": "OK", "start_time": 1.1},
        ]
    }
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("get_trace_summary", payload)

    assert not result.isError, f"tool returned an error: {result.content}"
    # The MCP spec wraps tool returns in a content list. The trace_summary
    # function returns a dict; FastMCP serialises it to JSON-text content.
    blob = "".join(getattr(c, "text", "") for c in result.content if hasattr(c, "text"))
    assert blob, "empty result content"
    import json

    data = json.loads(blob)
    assert data["span_count"] == 2
    assert data["total_duration_ms"] == 300
