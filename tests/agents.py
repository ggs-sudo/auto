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

    async def close(self) -> None:
        return None

    async def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None


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


class HarnessLauncher:
    """One launcher for both shapes: replayed sessions, scripted judgments."""

    def __init__(
        self,
        sessions: Mapping[str, Recording],
        agents: Sequence[ScriptedAgent] = (),
        writes: Writes | Mapping[str, Writes] = (),
    ) -> None:
        """`writes` applies to every driven session, or — keyed by node id —
        to each its own, which is what a run of several nodes needs."""
        self._sessions = ReplayLauncher(sessions)
        self._agents = list(agents)
        self._writes = writes
        self.interventions: list[LaunchSpec] = []
        self.tool_results: list[dict[str, Any]] = []

    @property
    def launched(self) -> list[LaunchSpec]:
        return self._sessions.launched

    @property
    def sent(self) -> list[tuple[str, str]]:
        return self._sessions.sent

    @property
    def sessions(self) -> list[ReplaySession]:
        return self._sessions.sessions

    async def launch(self, spec: LaunchSpec) -> Any:
        if not spec.one_shot:
            session = await self._sessions.launch(spec)
            writes = (
                self._writes.get(spec.node_id, ())
                if isinstance(self._writes, Mapping)
                else self._writes
            )
            if not writes:
                return session
            return WritingSession(session, spec.cwd, writes)
        if not self._agents:
            raise ReplayExhausted(
                f"node {spec.node_id!r} went stale more times than the test "
                "scripted judgments for"
            )
        self.interventions.append(spec)
        return ScriptedAgentSession(spec, self._agents.pop(0), self)
