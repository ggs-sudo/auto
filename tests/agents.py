"""A scripted orchestrator agent that really speaks MCP.

Driven sessions replay recorded events; there is nothing to record for an
orchestrator-agent invocation, because what it *does* is not in its stream —
it is in the tool calls it makes. So this stands in for the agent's judgment
only, and makes those calls for real, over HTTP, against the URL its inline
config names. A test that says "the agent completed the node" therefore
exercises the whole tool path: the scoping, the preconditions, the writes.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from auto.session.protocol import LaunchSpec
from auto.session.replay import Recording, ReplayExhausted, ReplayLauncher, ReplaySession
from auto.tools.harness import SERVER_NAME
from tests.conftest import assistant_event, call_tool, init_event, mcp_request, result_event


@dataclass(frozen=True)
class ScriptedAgent:
    """What one ephemeral invocation decides to do."""

    calls: Sequence[tuple[str, dict[str, Any]]] = ()
    prose: str = "Judged."
    cost_usd: float = 0.02


def sends(message: str, **extra: Any) -> ScriptedAgent:
    return ScriptedAgent(calls=[("send_to_session", {"message": message, **extra})])


def completes(summary: str = "The node produced what it owed.", **extra: Any) -> ScriptedAgent:
    return ScriptedAgent(calls=[("complete_node", {"summary": summary, **extra})])


def emits_and_completes(
    effort: str = "add-search",
    tasks: Sequence[Mapping[str, str]] = (),
    summary: str = "Charted; the tickets are the graph now.",
) -> ScriptedAgent:
    """The shape every finished planning node takes: emit the graph, complete."""
    return ScriptedAgent(
        calls=[
            ("emit_graph", {"effort": effort, "tasks": [dict(t) for t in tasks]}),
            ("complete_node", {"summary": summary}),
        ]
    )


def says_nothing(prose: str = "Still working; leaving it alone.") -> ScriptedAgent:
    """A valid intervention that calls no tools."""
    return ScriptedAgent(calls=(), prose=prose)


def reports_clean(
    summary: str = "The recorded state and the repo agree.",
) -> ScriptedAgent:
    """A takeover consultation that lands the clean verdict."""
    return ScriptedAgent(calls=[("report_effort_clean", {"summary": summary})])


def fails(reason: str = "the work cannot be done at all", **extra: Any) -> ScriptedAgent:
    return ScriptedAgent(calls=[("fail_node", {"reason": reason, **extra})])


def tries_to_complete(
    summary: str = "It says it is finished.", then: str | None = None
) -> ScriptedAgent:
    """An agent that judges the node finished, and nudges if it is refused.

    The shape every intervention on a session that stopped short takes: the
    completion is refused for want of a tracker file, and the only move left
    is a message.
    """
    calls: list[tuple[str, dict[str, Any]]] = [("complete_node", {"summary": summary})]
    if then is not None:
        calls.append(("send_to_session", {"message": then}))
    return ScriptedAgent(calls=calls)


def url_from(mcp_config: str | None) -> str:
    assert mcp_config is not None, "an orchestrator invocation gets an inline MCP config"
    servers = json.loads(mcp_config)["mcpServers"]
    url: str = servers[SERVER_NAME]["url"]
    return url


class ScriptedAgentSession:
    """One ephemeral `claude -p`, stood in for."""

    def __init__(self, spec: LaunchSpec, script: ScriptedAgent, owner: HarnessLauncher) -> None:
        self._spec = spec
        self._script = script
        self._owner = owner
        self._down = False

    @property
    def session_id(self) -> str:
        return self._spec.session_id

    async def events(self) -> AsyncGenerator[dict[str, Any], None]:
        url = url_from(self._spec.mcp_config)
        await mcp_request(url, "initialize", {"protocolVersion": "2025-06-18"})
        yield init_event(self._spec.session_id)
        for name, arguments in self._script.calls:
            self._owner.tool_results.append(await call_tool(url, name, arguments))
        yield assistant_event(self._script.prose, self._spec.session_id)
        yield result_event(
            self._script.prose,
            session_id=self._spec.session_id,
            cost_usd=self._script.cost_usd,
            num_turns=1,
        )

    async def send(self, message: str) -> None:
        raise AssertionError("an ephemeral invocation is never messaged")

    def _mark_down(self) -> None:
        if not self._down:
            self._down = True
            self._owner.intervention_down()

    async def close(self) -> None:
        self._mark_down()

    async def terminate(self) -> None:
        self._mark_down()

    def kill(self) -> None:
        self._mark_down()


class WritingSession:
    """A replayed session that lays down tracker files as it takes its turns.

    A real session writes to the target repo while it works, and the owed
    artifact check reads that repo — so a recording alone cannot stand in for
    one. The fixture says what each turn leaves behind, and it is laid down at
    the moment that turn begins: entry `n` when the session is messaged into
    turn `n`, entry `0` when it is launched.
    """

    def __init__(
        self,
        inner: ReplaySession,
        repo: Path,
        writes: Sequence[Mapping[str, str]],
    ) -> None:
        self._inner = inner
        self._repo = repo
        self._writes = list(writes)
        self._turn = 0

    @property
    def session_id(self) -> str:
        return self._inner.session_id

    async def events(self) -> AsyncGenerator[dict[str, Any], None]:
        self._lay_down(0)
        async for event in self._inner.events():
            yield event

    async def send(self, message: str) -> None:
        self._turn += 1
        self._lay_down(self._turn)
        await self._inner.send(message)

    async def close(self) -> None:
        await self._inner.close()

    async def terminate(self) -> None:
        await self._inner.terminate()

    def kill(self) -> None:
        self._inner.kill()

    def _lay_down(self, turn: int) -> None:
        for path, body in (
            self._writes[turn] if turn < len(self._writes) else {}
        ).items():
            file = self._repo / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(body)


Writes = Sequence[Mapping[str, str]]
"""What a session lays down, one entry per turn."""

Agents = Sequence[ScriptedAgent] | Mapping[str, Sequence[ScriptedAgent]]
"""Scripted judgments: one shared queue, or — keyed by node id — one queue per
node, which is what a run dispatching several nodes at once needs, because the
order their interventions fire in is the scheduler's business, not the test's."""


class TrackedSession:
    """A driven session, counted while it is live.

    What the concurrency cap bounds is driven sessions, so the gauge lives on
    exactly those: up at launch, down the first time the loop brings it down.
    """

    def __init__(self, inner: Any, owner: HarnessLauncher) -> None:
        self._inner = inner
        self._owner = owner
        self._down = False
        owner.session_up()

    @property
    def session_id(self) -> str:
        sid: str = self._inner.session_id
        return sid

    def events(self) -> AsyncGenerator[dict[str, Any], None]:
        generator: AsyncGenerator[dict[str, Any], None] = self._inner.events()
        return generator

    async def send(self, message: str) -> None:
        await self._inner.send(message)

    def _mark_down(self) -> None:
        if not self._down:
            self._down = True
            self._owner.session_down()

    async def close(self) -> None:
        self._mark_down()
        await self._inner.close()

    async def terminate(self) -> None:
        self._mark_down()
        await self._inner.terminate()

    def kill(self) -> None:
        self._mark_down()
        self._inner.kill()


class HarnessLauncher:
    """One launcher for both shapes: replayed sessions, scripted judgments."""

    def __init__(
        self,
        sessions: Mapping[str, Recording],
        agents: Agents = (),
        writes: Writes | Mapping[str, Writes] = (),
    ) -> None:
        """`agents` and `writes` apply in order to every node alike, or —
        keyed by node id — to each its own, which is what a run of several
        nodes at once needs."""
        self._sessions = ReplayLauncher(sessions)
        self._agents: list[ScriptedAgent] | dict[str, list[ScriptedAgent]] = (
            {node: list(queue) for node, queue in agents.items()}
            if isinstance(agents, Mapping)
            else list(agents)
        )
        self._writes = writes
        self.interventions: list[LaunchSpec] = []
        self.tool_results: list[dict[str, Any]] = []
        self.live_driven = 0
        self.max_live_driven = 0
        """The most driven sessions ever live at once — what the cap bounds."""
        self.live_interventions = 0
        self.max_live_interventions = 0
        self.interventions_live_driven: list[int] = []
        """How many driven sessions were live as each intervention launched."""

    @property
    def launched(self) -> list[LaunchSpec]:
        return self._sessions.launched

    @property
    def sent(self) -> list[tuple[str, str]]:
        return self._sessions.sent

    @property
    def sessions(self) -> list[ReplaySession]:
        return self._sessions.sessions

    def session_up(self) -> None:
        self.live_driven += 1
        self.max_live_driven = max(self.max_live_driven, self.live_driven)

    def session_down(self) -> None:
        self.live_driven -= 1

    def intervention_down(self) -> None:
        self.live_interventions -= 1

    def _next_agent(self, node_id: str) -> ScriptedAgent:
        queue = (
            self._agents.get(node_id, [])
            if isinstance(self._agents, dict)
            else self._agents
        )
        if not queue:
            raise ReplayExhausted(
                f"node {node_id!r} went stale more times than the test "
                "scripted judgments for"
            )
        return queue.pop(0)

    async def launch(self, spec: LaunchSpec) -> Any:
        if not spec.one_shot:
            session: Any = await self._sessions.launch(spec)
            writes = (
                self._writes.get(spec.node_id, ())
                if isinstance(self._writes, Mapping)
                else self._writes
            )
            if writes:
                session = WritingSession(session, spec.cwd, writes)
            return TrackedSession(session, self)
        script = self._next_agent(spec.node_id)
        self.interventions.append(spec)
        self.interventions_live_driven.append(self.live_driven)
        self.live_interventions += 1
        self.max_live_interventions = max(
            self.max_live_interventions, self.live_interventions
        )
        return ScriptedAgentSession(spec, script, self)
