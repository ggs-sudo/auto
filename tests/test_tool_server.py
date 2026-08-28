"""The harness tools, exercised as the agent reaches them: real JSON-RPC over
real HTTP against the real server. Nothing here stubs the transport."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence

import pytest

from auto.tools.harness import COMPLETE_NODE, SEND_TO_SESSION, SERVER_NAME, ToolResult
from auto.tools.server import ToolServer, intervention_path
from tests.conftest import call_tool, mcp_request, tool_text


class RecordingTools:
    """A stand-in for the loop side of the tools: it only remembers."""

    def __init__(self, *, refuse: str | None = None) -> None:
        self.sent: list[tuple[str, list[str]]] = []
        self.completed: list[tuple[str, list[str]]] = []
        self.refuse = refuse

    async def send_to_session(
        self, message: str, highlights: Sequence[str]
    ) -> ToolResult:
        if self.refuse is not None:
            return ToolResult(self.refuse, is_error=True)
        self.sent.append((message, list(highlights)))
        return ToolResult("delivered")

    async def complete_node(
        self, summary: str, highlights: Sequence[str]
    ) -> ToolResult:
        if self.refuse is not None:
            return ToolResult(self.refuse, is_error=True)
        self.completed.append((summary, list(highlights)))
        return ToolResult("node complete")


@pytest.fixture
async def server() -> AsyncIterator[ToolServer]:
    tools = ToolServer(asyncio.get_running_loop())
    try:
        yield tools
    finally:
        tools.close()


async def test_the_mcp_config_is_inline_strict_http_scoped_to_run_and_node(
    server: ToolServer,
) -> None:
    config = json.loads(server.mcp_config("20260828-120000-add-search", "root"))
    entry = config["mcpServers"][SERVER_NAME]
    assert entry["type"] == "http"
    assert entry["url"].endswith(
        "/runs/20260828-120000-add-search/nodes/root/mcp"
    )
    assert entry["url"].startswith("http://127.0.0.1:")


async def test_a_node_id_with_awkward_characters_still_addresses_one_node(
    server: ToolServer,
) -> None:
    assert intervention_path("run/1", "a b") == "/runs/run%2F1/nodes/a%20b/mcp"


async def test_it_answers_initialize_and_lists_the_two_tools(
    server: ToolServer,
) -> None:
    tools = RecordingTools()
    with server.intervention("r", "root", tools):
        url = server.url_for("r", "root")
        _, initialised = await mcp_request(url, "initialize", {"protocolVersion": "2025-06-18"})
        assert initialised is not None
        assert initialised["result"]["protocolVersion"] == "2025-06-18"
        assert initialised["result"]["serverInfo"]["name"] == SERVER_NAME

        _, listed = await mcp_request(url, "tools/list")
        assert listed is not None
        names = [tool["name"] for tool in listed["result"]["tools"]]
        assert names == [SEND_TO_SESSION, COMPLETE_NODE]


async def test_no_tool_takes_a_node_argument(server: ToolServer) -> None:
    """Scope is the URL's job. There is no argument for an agent to get wrong."""
    with server.intervention("r", "root", RecordingTools()):
        _, listed = await mcp_request(server.url_for("r", "root"), "tools/list")
    assert listed is not None
    for tool in listed["result"]["tools"]:
        assert "node" not in tool["inputSchema"]["properties"]


async def test_send_to_session_reaches_the_loop_side_of_the_tools(
    server: ToolServer,
) -> None:
    tools = RecordingTools()
    with server.intervention("r", "root", tools) as intervention:
        result = await call_tool(
            server.url_for("r", "root"),
            SEND_TO_SESSION,
            {"message": "Carry on.", "highlights": ["asked about auth"]},
        )
    assert result["isError"] is False
    assert tools.sent == [("Carry on.", ["asked about auth"])]
    assert [call.tool for call in intervention.tool_calls] == [SEND_TO_SESSION]
    assert intervention.tool_calls[0].accepted is True


async def test_complete_node_reaches_the_loop_side_of_the_tools(
    server: ToolServer,
) -> None:
    tools = RecordingTools()
    with server.intervention("r", "root", tools):
        await call_tool(
            server.url_for("r", "root"), COMPLETE_NODE, {"summary": "Map written."}
        )
    assert tools.completed == [("Map written.", [])]


async def test_a_url_for_another_node_answers_nothing(server: ToolServer) -> None:
    with server.intervention("r", "root", RecordingTools()):
        status, _ = await mcp_request(server.url_for("r", "other"), "tools/list")
    assert status == 404


async def test_a_url_for_another_run_answers_nothing(server: ToolServer) -> None:
    with server.intervention("r", "root", RecordingTools()):
        status, _ = await mcp_request(server.url_for("other-run", "root"), "tools/list")
    assert status == 404


async def test_the_address_stops_existing_when_the_invocation_ends(
    server: ToolServer,
) -> None:
    """A process that outlived its intervention has nowhere to send a call."""
    tools = RecordingTools()
    with server.intervention("r", "root", tools):
        pass
    status, _ = await mcp_request(
        server.url_for("r", "root"),
        "tools/call",
        {"name": SEND_TO_SESSION, "arguments": {"message": "Too late."}},
    )
    assert status == 404
    assert tools.sent == []


async def test_completing_after_sending_is_refused_and_recorded(
    server: ToolServer,
) -> None:
    tools = RecordingTools()
    with server.intervention("r", "root", tools) as intervention:
        url = server.url_for("r", "root")
        await call_tool(url, SEND_TO_SESSION, {"message": "Carry on."})
        refused = await call_tool(url, COMPLETE_NODE, {"summary": "Done."})
    assert refused["isError"] is True
    assert "at most one of" in tool_text(refused)
    assert tools.completed == []
    assert [(c.tool, c.accepted) for c in intervention.tool_calls] == [
        (SEND_TO_SESSION, True),
        (COMPLETE_NODE, False),
    ]


async def test_sending_after_completing_is_refused_and_recorded(
    server: ToolServer,
) -> None:
    tools = RecordingTools()
    with server.intervention("r", "root", tools) as intervention:
        url = server.url_for("r", "root")
        await call_tool(url, COMPLETE_NODE, {"summary": "Done."})
        refused = await call_tool(url, SEND_TO_SESSION, {"message": "One more thing."})
    assert refused["isError"] is True
    assert tools.sent == []
    assert intervention.tool_calls[-1].accepted is False


async def test_an_unknown_tool_is_refused_rather_than_crashing(
    server: ToolServer,
) -> None:
    with server.intervention("r", "root", RecordingTools()) as intervention:
        result = await call_tool(server.url_for("r", "root"), "emit_graph", {})
    assert result["isError"] is True
    assert intervention.tool_calls[0].tool == "emit_graph"
    assert intervention.tool_calls[0].accepted is False


async def test_bad_arguments_are_refused_with_a_reason_the_agent_can_read(
    server: ToolServer,
) -> None:
    with server.intervention("r", "root", RecordingTools()) as intervention:
        result = await call_tool(server.url_for("r", "root"), SEND_TO_SESSION, {})
    assert result["isError"] is True
    assert "message" in tool_text(result)
    assert intervention.tool_calls[0].accepted is False


async def test_a_refused_effect_leaves_the_exclusive_slot_free(
    server: ToolServer,
) -> None:
    """A tool that could not do its job has not used up the invocation."""
    tools = RecordingTools(refuse="its session is gone")
    with server.intervention("r", "root", tools) as intervention:
        url = server.url_for("r", "root")
        failed = await call_tool(url, SEND_TO_SESSION, {"message": "Carry on."})
        assert failed["isError"] is True
        tools.refuse = None
        second = await call_tool(url, COMPLETE_NODE, {"summary": "Done."})
    assert second["isError"] is False
    assert tools.completed == [("Done.", [])]
    assert [call.accepted for call in intervention.tool_calls] == [False, True]


async def test_two_interventions_on_one_node_cannot_be_open_at_once(
    server: ToolServer,
) -> None:
    with server.intervention("r", "root", RecordingTools()):
        with pytest.raises(RuntimeError, match="never overlap"):
            with server.intervention("r", "root", RecordingTools()):
                pass


async def test_interventions_on_different_nodes_are_open_side_by_side(
    server: ToolServer,
) -> None:
    first, second = RecordingTools(), RecordingTools()
    with server.intervention("r", "a", first), server.intervention("r", "b", second):
        await call_tool(server.url_for("r", "a"), SEND_TO_SESSION, {"message": "A."})
        await call_tool(server.url_for("r", "b"), SEND_TO_SESSION, {"message": "B."})
    assert first.sent == [("A.", [])]
    assert second.sent == [("B.", [])]


async def test_a_notification_is_accepted_without_an_answer(
    server: ToolServer,
) -> None:
    with server.intervention("r", "root", RecordingTools()):
        status, body = await mcp_request(
            server.url_for("r", "root"), "notifications/initialized", request_id=None
        )
    assert status == 202
    assert body is None


async def test_an_unsupported_method_is_a_json_rpc_error(server: ToolServer) -> None:
    with server.intervention("r", "root", RecordingTools()):
        _, body = await mcp_request(server.url_for("r", "root"), "resources/list")
    assert body is not None
    assert body["error"]["code"] == -32601
