"""Invoking one ephemeral orchestrator agent.

A fresh agent per intervention, never one per run. Two reasons, both
load-bearing: a single long-lived agent's trail would accumulate every node's
context until it was reading things irrelevant to the node in front of it, and
one agent cannot watch parallel nodes at once.

An invocation is a `claude -p` call through the same seam driven sessions go
through, with four things a driven session never gets — a pinned model, the
run's stable material appended to its system prompt, an inline strict MCP
config whose URL scopes it to a single node, and a tool allowlist that leaves
it able to read the target repo but not to touch it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from auto.model.intervention import InterventionRecord, InterventionTrigger
from auto.run import RunDirectory
from auto.session.events import is_result, result_summary, telemetry_from_result
from auto.session.protocol import Launcher, LaunchSpec
from auto.tools.harness import QUALIFIED_TOOL_NAMES, HarnessTools
from auto.tools.server import ToolServer

logger = logging.getLogger(__name__)

_UNSAFE_IN_A_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")

READ_ONLY_TOOLS = ("Read", "Glob", "Grep")
"""The agent reads the target repo — the answer policy says its docs outrank
the interviewer — and writes nothing to it. The orchestrator never writes a
tracker file, and this is the reason that holds under a confused agent."""

AGENT_TOOLS = (*QUALIFIED_TOOL_NAMES, *READ_ONLY_TOOLS)


class OrchestratorAgent:
    """Invokes the run's agent, one intervention at a time per node.

    Interventions on one node never overlap: the lock below serialises them,
    and the tool server refuses to open a second window on a node that already
    has one, so the invariant holds even if something else tries to break it.
    Interventions on *different* nodes are free to run side by side.
    """

    def __init__(
        self,
        *,
        run: RunDirectory,
        launcher: Launcher,
        tools: ToolServer,
        model: str,
        system_prompt: str,
        cwd: Path,
        clock: Callable[[], datetime],
        session_id_factory: Callable[[], str],
    ) -> None:
        self._run = run
        self._launcher = launcher
        self._tools = tools
        self._model = model
        self._system_prompt = system_prompt
        self._cwd = cwd
        self._clock = clock
        self._new_session_id = session_id_factory
        self._locks: dict[str, asyncio.Lock] = {}
        self._sequence = 0

    async def intervene(
        self,
        *,
        node: str,
        trigger: InterventionTrigger,
        message: str,
        tools: HarnessTools,
    ) -> InterventionRecord:
        """Run one intervention on one node and return what it did."""
        async with self._locks.setdefault(node, asyncio.Lock()):
            return await self._invoke(node, trigger, message, tools)

    async def _invoke(
        self,
        node: str,
        trigger: InterventionTrigger,
        message: str,
        tools: HarnessTools,
    ) -> InterventionRecord:
        self._sequence += 1
        record = InterventionRecord(
            intervention_id=self._intervention_id(node),
            node=node,
            trigger=trigger,
            model=self._model,
            session_id=self._new_session_id(),
            started_at=self._clock(),
        )
        logger.debug(
            "session start: orchestrator agent session %s judges node %s "
            "(intervention %s, trigger %s, model %s)",
            record.session_id,
            node,
            record.intervention_id,
            trigger.value,
            self._model,
        )
        # Written before the agent runs, so a slow judgment is visible while it
        # is being made rather than only once it has landed.
        self._run.write_intervention(record)

        with self._tools.intervention(self._run.run_id, node, tools) as scope:
            spec = LaunchSpec(
                node_id=node,
                session_id=record.session_id,
                cwd=self._cwd,
                message=message,
                model=self._model,
                append_system_prompt=self._system_prompt,
                mcp_config=self._tools.mcp_config(self._run.run_id, node),
                allowed_tools=AGENT_TOOLS,
                one_shot=True,
            )
            session = await self._launcher.launch(spec)
            stream = session.events()
            try:
                async for event in stream:
                    if is_result(event):
                        record.telemetry = telemetry_from_result(event)
                        record.prose = result_summary(event)
                        break
            finally:
                with contextlib.suppress(Exception):
                    await stream.aclose()
                await session.terminate()
            record.tool_calls = list(scope.tool_calls)

        record.ended_at = self._clock()
        logger.debug(
            "orchestrator agent session %s done on node %s: %s, $%.4f",
            record.session_id,
            node,
            ", ".join(call.tool for call in record.tool_calls) or "no tool calls",
            record.telemetry.cost_usd or 0.0,
        )
        self._run.write_intervention(record)
        return record

    def _intervention_id(self, node: str) -> str:
        return f"{self._sequence:04d}-{_UNSAFE_IN_A_FILENAME.sub('-', node)}"
