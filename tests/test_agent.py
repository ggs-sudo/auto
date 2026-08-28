"""What an ephemeral agent is told, and that no two of them ever look at one
node at the same moment."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from auto.agent.invoke import AGENT_TOOLS, OrchestratorAgent
from auto.agent.prompt import intervention_message, stable_system_prompt
from auto.agent.trace import MAX_TURN_CHARS, render, truncate_middle, turns_for
from auto.model import (
    InterventionTrigger,
    Manifest,
    NodeStatus,
    NodeType,
    ResolvedConfig,
    RootNode,
    Route,
)
from auto.owed import OWED
from auto.run import RunDirectory
from auto.session.protocol import LaunchSpec
from auto.tools.harness import ToolResult
from auto.tools.server import ToolServer
from tests.conftest import (
    TRACKER_DOC_BODY,
    assistant_event,
    init_event,
    one_turn,
    result_event,
)

CREATED_AT = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


def a_manifest(prompt: str = "Add search to the settings page.") -> Manifest:
    return Manifest(
        run_id="20260828-120000-add-search",
        route=Route.WAYFINDER,
        prompt=prompt,
        target_repo="/tmp/target",
        config=ResolvedConfig(),
        created_at=CREATED_AT,
        root_node=RootNode(type=NodeType.WAYFINDER, prompt=prompt),
    )


def a_prompt(prompt: str = "Add search to the settings page.") -> str:
    """The stable half, assembled as a run assembles it once and reuses it."""
    return stable_system_prompt(a_manifest(prompt), tracker_doc=TRACKER_DOC_BODY)


# --- what the agent is shown -------------------------------------------------


def test_a_monitored_node_is_read_in_full() -> None:
    events = [*one_turn("First."), *one_turn("Second.")]
    assert len(turns_for(NodeType.WAYFINDER, events)) == 2
    trace = render(NodeType.WAYFINDER, events)
    assert "First." in trace
    assert "Second." in trace


def test_an_autonomous_node_is_read_from_the_previous_stale_point() -> None:
    events = [*one_turn("First."), *one_turn("Second.")]
    assert len(turns_for(NodeType.IMPLEMENT, events)) == 1
    trace = render(NodeType.IMPLEMENT, events)
    assert "First." not in trace
    assert "Second." in trace


def test_an_autonomous_nodes_first_stale_point_is_its_whole_session() -> None:
    trace = render(NodeType.IMPLEMENT, one_turn("Only turn."))
    assert "Only turn." in trace


def test_a_session_that_has_said_nothing_yet_reads_as_such() -> None:
    assert "nothing" in render(NodeType.WAYFINDER, [])


def test_tool_use_and_its_result_are_summarised_not_reproduced() -> None:
    events: list[dict[str, Any]] = [
        init_event(),
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "Write", "input": {"path": "map.md"}}
                ],
            },
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "content": "x" * 50_000}],
            },
        },
        result_event("Wrote the map."),
    ]
    trace = render(NodeType.WAYFINDER, events)
    assert "[used Write:" in trace
    assert "map.md" in trace
    assert "elided" in trace


def test_the_stream_bookkeeping_a_reader_does_not_need_is_dropped() -> None:
    assert "permissionMode" not in render(NodeType.WAYFINDER, one_turn())


def test_the_turn_boundary_is_visible_in_the_trace() -> None:
    assert "turn ended" in render(NodeType.WAYFINDER, one_turn())


def test_a_pathologically_long_turn_is_truncated_from_the_middle() -> None:
    events = [init_event(), assistant_event("A" * 60_000), result_event("Done.")]
    trace = render(NodeType.WAYFINDER, events)
    assert len(trace) <= MAX_TURN_CHARS + 200
    assert trace.startswith("session: AAA")
    assert "turn ended" in trace, "the end of the turn survives truncation"
    assert "elided from the middle" in trace


def test_truncation_says_how_much_it_dropped() -> None:
    truncated = truncate_middle("abcdefghij" * 100, 120)
    assert "…" in truncated
    assert truncated.startswith("abcdefghij")
    assert truncated.endswith("abcdefghij")
    assert len(truncated) <= 120


def test_short_enough_text_is_left_exactly_alone() -> None:
    assert truncate_middle("just this", 100) == "just this"


# --- what the agent is told --------------------------------------------------


def test_the_seed_prompt_is_framed_as_a_message_the_orchestrator_wrote() -> None:
    prompt = a_prompt("Add faceted search.")
    assert "Add faceted search." in prompt
    assert "You opened this run by sending" in prompt


def test_the_stable_prompt_carries_the_answer_policy_and_the_chaining_rules() -> None:
    prompt = a_prompt()
    assert "recommended answer" in prompt
    assert "same conversation" in prompt
    assert "mutually exclusive" in prompt
    assert "no tool at all is a legitimate answer" in prompt


def test_the_stable_prompt_holds_nothing_about_a_particular_node() -> None:
    """Anything per-node in here would cost the run its cached prefix."""
    assert a_prompt() == a_prompt()
    prompt = a_prompt()
    assert "<trace>" not in prompt
    assert "- node:" not in prompt


def test_the_stable_prompt_carries_the_owed_artifact_table() -> None:
    """The harness's knowledge of what a node leaves behind, in the agent's words."""
    prompt = a_prompt()
    for node_type in NodeType:
        assert node_type.skill_invocation in prompt
    for artifact in OWED[NodeType.WAYFINDER]:
        assert artifact.owes in prompt


def test_the_stable_prompt_carries_the_repos_own_tracker_doc_verbatim() -> None:
    """So a nudge is phrased in the repo's vocabulary, not the harness's."""
    assert TRACKER_DOC_BODY in a_prompt()


def test_the_stable_prompt_says_a_completion_is_refused_while_anything_is_owed() -> None:
    prompt = a_prompt()
    assert "refused" in prompt
    assert "fail_node" in prompt


def test_the_invocation_message_names_the_node_and_carries_its_trace() -> None:
    message = intervention_message(
        a_manifest(),
        node_id="root",
        node_type=NodeType.WAYFINDER,
        node_status=NodeStatus.IN_PROGRESS,
        trigger=InterventionTrigger.STALE,
        trace="session: I charted the map.",
    )
    assert "`root`" in message
    assert "/wayfinder" in message
    assert "its turn ended" in message
    assert "I charted the map." in message


def test_the_invocation_message_says_how_deep_the_trace_goes() -> None:
    def message(node_type: NodeType) -> str:
        return intervention_message(
            a_manifest(),
            node_id="n",
            node_type=node_type,
            node_status=NodeStatus.IN_PROGRESS,
            trigger=InterventionTrigger.STALE,
            trace="…",
        )

    assert "whole conversation" in message(NodeType.WAYFINDER)
    assert "since the previous time" in message(NodeType.IMPLEMENT)


def test_the_agent_may_read_the_target_repo_and_may_not_change_it() -> None:
    """ADR-0002: a rambling agent can fail to act, but cannot corrupt state."""
    assert "mcp__harness__send_to_session" in AGENT_TOOLS
    assert "mcp__harness__complete_node" in AGENT_TOOLS
    assert "mcp__harness__fail_node" in AGENT_TOOLS
    assert "Read" in AGENT_TOOLS
    for forbidden in ("Write", "Edit", "Bash", "NotebookEdit"):
        assert forbidden not in AGENT_TOOLS


# --- one node, one agent at a time -------------------------------------------


class SlowSession:
    """An invocation that will not finish until it is let go."""

    def __init__(self, spec: LaunchSpec, owner: CountingLauncher) -> None:
        self._spec = spec
        self._owner = owner

    @property
    def session_id(self) -> str:
        return self._spec.session_id

    async def events(self) -> AsyncGenerator[dict[str, Any], None]:
        self._owner.enter()
        try:
            await self._owner.release.wait()
            yield result_event("Judged.", session_id=self._spec.session_id)
        finally:
            self._owner.leave()

    async def send(self, message: str) -> None: ...

    async def close(self) -> None: ...

    async def terminate(self) -> None: ...

    def kill(self) -> None: ...


class CountingLauncher:
    """Records how many invocations were ever in flight at once."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.live = 0
        self.most = 0
        self.started = 0

    def enter(self) -> None:
        self.live += 1
        self.started += 1
        self.most = max(self.most, self.live)

    def leave(self) -> None:
        self.live -= 1

    async def launch(self, spec: LaunchSpec) -> SlowSession:
        return SlowSession(spec, self)


class NoTools:
    async def send_to_session(self, message: str, highlights: Any) -> ToolResult:
        return ToolResult("delivered")

    async def complete_node(self, summary: str, highlights: Any) -> ToolResult:
        return ToolResult("complete")

    async def fail_node(self, reason: str, highlights: Any) -> ToolResult:
        return ToolResult("failed")


def an_agent(run: RunDirectory, launcher: CountingLauncher, tools: ToolServer) -> OrchestratorAgent:
    ids = iter(f"agent-{n}" for n in range(1, 100))
    return OrchestratorAgent(
        run=run,
        launcher=launcher,
        tools=tools,
        model="claude-opus-5",
        system_prompt="stable",
        cwd=Path("/tmp/target"),
        clock=lambda: CREATED_AT,
        session_id_factory=lambda: next(ids),
    )


async def test_interventions_on_one_node_never_overlap(tmp_path: Path) -> None:
    run = RunDirectory.create(tmp_path, a_manifest())
    launcher = CountingLauncher()
    tools = ToolServer(asyncio.get_running_loop())
    agent = an_agent(run, launcher, tools)
    try:
        both = asyncio.gather(
            *(
                agent.intervene(
                    node="root",
                    trigger=InterventionTrigger.STALE,
                    message="judge it",
                    tools=NoTools(),
                )
                for _ in range(2)
            )
        )
        await asyncio.sleep(0)
        launcher.release.set()
        await both
    finally:
        tools.close()
    assert launcher.started == 2
    assert launcher.most == 1


async def test_interventions_on_different_nodes_run_side_by_side(
    tmp_path: Path,
) -> None:
    run = RunDirectory.create(tmp_path, a_manifest())
    launcher = CountingLauncher()
    tools = ToolServer(asyncio.get_running_loop())
    agent = an_agent(run, launcher, tools)
    try:
        both = asyncio.gather(
            *(
                agent.intervene(
                    node=node,
                    trigger=InterventionTrigger.STALE,
                    message="judge it",
                    tools=NoTools(),
                )
                for node in ("a", "b")
            )
        )
        while launcher.live < 2:
            await asyncio.sleep(0)
        launcher.release.set()
        await both
    finally:
        tools.close()
    assert launcher.most == 2


async def test_an_intervention_is_visible_while_it_is_still_being_made(
    tmp_path: Path,
) -> None:
    """A slow judgment should not be invisible until it lands."""
    run = RunDirectory.create(tmp_path, a_manifest())
    launcher = CountingLauncher()
    tools = ToolServer(asyncio.get_running_loop())
    agent = an_agent(run, launcher, tools)
    try:
        pending = asyncio.ensure_future(
            agent.intervene(
                node="root",
                trigger=InterventionTrigger.STALE,
                message="judge it",
                tools=NoTools(),
            )
        )
        while launcher.live < 1:
            await asyncio.sleep(0)
        in_flight = run.intervention_records()
        launcher.release.set()
        await pending
    finally:
        tools.close()
    assert [record.ended_at for record in in_flight] == [None]
    assert run.intervention_records()[0].ended_at is not None


async def test_the_intervention_id_sorts_by_when_it_was_made(tmp_path: Path) -> None:
    run = RunDirectory.create(tmp_path, a_manifest())
    launcher = CountingLauncher()
    launcher.release.set()
    tools = ToolServer(asyncio.get_running_loop())
    agent = an_agent(run, launcher, tools)
    try:
        for _ in range(2):
            await agent.intervene(
                node="root",
                trigger=InterventionTrigger.STALE,
                message="judge it",
                tools=NoTools(),
            )
    finally:
        tools.close()
    assert [r.intervention_id for r in run.intervention_records()] == [
        "0001-root",
        "0002-root",
    ]
