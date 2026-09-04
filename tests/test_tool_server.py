"""The harness tools, exercised as the agent reaches them: real JSON-RPC over
real HTTP against the real server. Nothing here stubs the transport."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping, Sequence

import pytest

from auto.model import TaskResolutionMode
from auto.tools.harness import (
    COMPLETE_NODE,
    EMIT_GRAPH,
    ESCALATE_QUESTION,
    FAIL_NODE,
    HAND_TO_USER,
    PING_USER,
    PROTOTYPE_READY,
    REPORT_EFFORT_CLEAN,
    RESET_NODE,
    SEND_TO_SESSION,
    SERVER_NAME,
    ToolResult,
)
from auto.tools.server import ToolServer, intervention_path
from tests.conftest import call_tool, mcp_request, tool_text


class RecordingTools:
    """A stand-in for the loop side of the tools: it only remembers."""

    def __init__(self, *, refuse: str | None = None) -> None:
        self.sent: list[tuple[str, list[str]]] = []
        self.completed: list[tuple[str, list[str]]] = []
        self.failed: list[tuple[str, list[str]]] = []
        self.emitted: list[tuple[str, dict[str, TaskResolutionMode], list[str]]] = []
        self.readied: list[tuple[str, str, list[str]]] = []
        self.handed: list[tuple[str, list[str]]] = []
        self.escalated: list[tuple[str, list[str]]] = []
        self.pinged: list[tuple[str, list[str]]] = []
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

    async def fail_node(self, reason: str, highlights: Sequence[str]) -> ToolResult:
        if self.refuse is not None:
            return ToolResult(self.refuse, is_error=True)
        self.failed.append((reason, list(highlights)))
        return ToolResult("node failed")

    async def emit_graph(
        self,
        effort: str,
        task_modes: Mapping[str, TaskResolutionMode],
        highlights: Sequence[str],
    ) -> ToolResult:
        if self.refuse is not None:
            return ToolResult(self.refuse, is_error=True)
        self.emitted.append((effort, dict(task_modes), list(highlights)))
        return ToolResult("graph emitted")

    async def prototype_ready(
        self, question: str, artifact: str, highlights: Sequence[str]
    ) -> ToolResult:
        if self.refuse is not None:
            return ToolResult(self.refuse, is_error=True)
        self.readied.append((question, artifact, list(highlights)))
        return ToolResult("gate raised")

    async def hand_to_user(
        self, question: str, highlights: Sequence[str]
    ) -> ToolResult:
        if self.refuse is not None:
            return ToolResult(self.refuse, is_error=True)
        self.handed.append((question, list(highlights)))
        return ToolResult("gate raised")

    async def escalate_question(
        self, question: str, highlights: Sequence[str]
    ) -> ToolResult:
        if self.refuse is not None:
            return ToolResult(self.refuse, is_error=True)
        self.escalated.append((question, list(highlights)))
        return ToolResult("gate raised")

    async def ping_user(self, message: str, highlights: Sequence[str]) -> ToolResult:
        if self.refuse is not None:
            return ToolResult(self.refuse, is_error=True)
        self.pinged.append((message, list(highlights)))
        return ToolResult("pinged")


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


async def test_it_answers_initialize_and_lists_the_tools(
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
        assert names == [
            SEND_TO_SESSION,
            COMPLETE_NODE,
            EMIT_GRAPH,
            PROTOTYPE_READY,
            HAND_TO_USER,
            ESCALATE_QUESTION,
            PING_USER,
            FAIL_NODE,
        ]


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


async def test_fail_node_reaches_the_loop_side_of_the_tools(
    server: ToolServer,
) -> None:
    tools = RecordingTools()
    with server.intervention("r", "root", tools):
        result = await call_tool(
            server.url_for("r", "root"),
            FAIL_NODE,
            {"reason": "it never wrote its ticket", "highlights": ["three nudges"]},
        )
    assert result["isError"] is False
    assert tools.failed == [("it never wrote its ticket", ["three nudges"])]


async def test_emit_graph_reaches_the_loop_side_with_its_classifications(
    server: ToolServer,
) -> None:
    tools = RecordingTools()
    with server.intervention("r", "root", tools):
        result = await call_tool(
            server.url_for("r", "root"),
            EMIT_GRAPH,
            {
                "effort": "add-search",
                "tasks": [{"ticket": "03-wire-up", "mode": "agent"}],
            },
        )
    assert result["isError"] is False
    assert tools.emitted == [
        ("add-search", {"03-wire-up": TaskResolutionMode.AGENT}, [])
    ]


async def test_emit_graph_is_not_exclusive_with_completing_the_node(
    server: ToolServer,
) -> None:
    """The graph is emitted and the node completed in one intervention."""
    tools = RecordingTools()
    with server.intervention("r", "root", tools) as intervention:
        url = server.url_for("r", "root")
        emitted = await call_tool(url, EMIT_GRAPH, {"effort": "add-search"})
        completed = await call_tool(url, COMPLETE_NODE, {"summary": "Charted."})
    assert emitted["isError"] is False
    assert completed["isError"] is False
    assert [(c.tool, c.accepted) for c in intervention.tool_calls] == [
        (EMIT_GRAPH, True),
        (COMPLETE_NODE, True),
    ]


async def test_failing_a_node_is_exclusive_with_completing_it(
    server: ToolServer,
) -> None:
    """A node is moved along, called finished, or given up on. Never two."""
    tools = RecordingTools()
    with server.intervention("r", "root", tools) as intervention:
        url = server.url_for("r", "root")
        await call_tool(url, FAIL_NODE, {"reason": "it cannot be done"})
        refused = await call_tool(url, COMPLETE_NODE, {"summary": "Done after all."})
    assert refused["isError"] is True
    assert "at most one of" in tool_text(refused)
    assert tools.completed == []
    assert [call.accepted for call in intervention.tool_calls] == [True, False]


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
        result = await call_tool(server.url_for("r", "root"), "prototype_ready", {})
    assert result["isError"] is True
    assert intervention.tool_calls[0].tool == "prototype_ready"
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


async def test_the_gate_raising_tools_reach_the_loop_side(
    server: ToolServer,
) -> None:
    tools = RecordingTools()
    with server.intervention("r", "add-search/01-keys", tools):
        url = server.url_for("r", "add-search/01-keys")
        handed = await call_tool(
            url,
            HAND_TO_USER,
            {"question": "Create the API key and report where it lives."},
        )
        assert not handed["isError"]
    with server.intervention("r2", "root", tools):
        url = server.url_for("r2", "root")
        escalated = await call_tool(
            url,
            ESCALATE_QUESTION,
            {"question": "Postgres or DynamoDB? Vendor lock-in either way."},
        )
        assert not escalated["isError"]
    assert tools.handed == [("Create the API key and report where it lives.", [])]
    assert tools.escalated == [
        ("Postgres or DynamoDB? Vendor lock-in either way.", [])
    ]


async def test_handing_to_the_user_and_escalating_are_exclusive_moves(
    server: ToolServer,
) -> None:
    """Parking a node at any kind of gate uses the intervention's one move up."""
    tools = RecordingTools()
    with server.intervention("r", "add-search/01-keys", tools):
        url = server.url_for("r", "add-search/01-keys")
        landed = await call_tool(url, HAND_TO_USER, {"question": "Do the thing."})
        assert not landed["isError"]
        second = await call_tool(
            url, ESCALATE_QUESTION, {"question": "Also, which vendor?"}
        )
        assert second["isError"]
        assert HAND_TO_USER in tool_text(second)
    assert tools.escalated == []


async def test_a_ping_blocks_nothing_and_rides_alongside_any_other_move(
    server: ToolServer,
) -> None:
    """Run-level and non-exclusive: pinging is not a decision about the node."""
    tools = RecordingTools()
    with server.intervention("r", "root", tools):
        url = server.url_for("r", "root")
        pinged = await call_tool(
            url, PING_USER, {"message": "Heads up: I chose SQLite."}
        )
        assert not pinged["isError"]
        sent = await call_tool(url, SEND_TO_SESSION, {"message": "carry on"})
        assert not sent["isError"]
    assert tools.pinged == [("Heads up: I chose SQLite.", [])]
    assert tools.sent == [("carry on", [])]


class RecordingTakeoverTools:
    """A stand-in for the loop side of the takeover tools."""

    def __init__(self) -> None:
        self.cleans: list[str] = []
        self.resets: list[tuple[str, str]] = []

    async def report_effort_clean(self, summary: str) -> ToolResult:
        self.cleans.append(summary)
        return ToolResult("recorded")

    async def reset_node(self, node: str, evidence: str) -> ToolResult:
        self.resets.append((node, evidence))
        return ToolResult("reset")


async def test_a_takeover_window_serves_the_takeover_roster_on_the_effort_address(
    server: ToolServer,
) -> None:
    """The same endpoint, one level up: the URL names the effort under
    reconciliation, and the roster behind it is takeover's alone."""
    tools = RecordingTakeoverTools()
    with server.takeover("r", "add-search", tools) as consultation:
        url = server.takeover_url("r", "add-search")
        assert url.endswith("/runs/r/efforts/add-search/mcp")

        _, listed = await mcp_request(url, "tools/list")
        assert listed is not None
        names = [tool["name"] for tool in listed["result"]["tools"]]
        assert names == [REPORT_EFFORT_CLEAN, RESET_NODE]

        landed = await call_tool(
            url, REPORT_EFFORT_CLEAN, {"summary": "Everything agrees."}
        )
        assert landed["isError"] is False
    assert tools.cleans == ["Everything agrees."]
    assert [call.tool for call in consultation.tool_calls] == [REPORT_EFFORT_CLEAN]
    assert consultation.tool_calls[0].accepted is True


async def test_no_takeover_tool_takes_an_effort_argument(
    server: ToolServer,
) -> None:
    """Scope is the URL's job here too: enforcement by addressing."""
    with server.takeover("r", "add-search", RecordingTakeoverTools()):
        _, listed = await mcp_request(
            server.takeover_url("r", "add-search"), "tools/list"
        )
    assert listed is not None
    for tool in listed["result"]["tools"]:
        assert "effort" not in tool["inputSchema"]["properties"]


async def test_the_node_tools_do_not_answer_on_an_effort_address(
    server: ToolServer,
) -> None:
    with server.takeover("r", "add-search", RecordingTakeoverTools()):
        refused = await call_tool(
            server.takeover_url("r", "add-search"),
            COMPLETE_NODE,
            {"summary": "sneaky"},
        )
    assert refused["isError"] is True
    assert "no such harness tool" in tool_text(refused)


async def test_a_takeover_call_outside_its_window_finds_no_address(
    server: ToolServer,
) -> None:
    status, _ = await mcp_request(
        server.takeover_url("r", "add-search"),
        "tools/call",
        {"name": REPORT_EFFORT_CLEAN, "arguments": {"summary": "late"}},
    )
    assert status == 404


async def test_the_takeover_mcp_config_is_inline_strict_http_scoped_to_the_effort(
    server: ToolServer,
) -> None:
    config = json.loads(server.takeover_mcp_config("20260828-120000-add-search", "add-search"))
    entry = config["mcpServers"][SERVER_NAME]
    assert entry["type"] == "http"
    assert entry["url"].endswith("/runs/20260828-120000-add-search/efforts/add-search/mcp")


async def test_two_consultations_on_one_effort_cannot_be_open_at_once(
    server: ToolServer,
) -> None:
    with server.takeover("r", "add-search", RecordingTakeoverTools()):
        with pytest.raises(RuntimeError, match="never overlap"):
            with server.takeover("r", "add-search", RecordingTakeoverTools()):
                pass  # pragma: no cover — the second window never opens
