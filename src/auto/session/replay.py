"""A launcher that replays recorded events instead of calling Claude.

The fixture format *is* the transcript capture format, so a captured
`transcripts/<session-id>.jsonl` from a live run is a fixture with no
conversion step. Everything above this seam — the CLI, the loop, dispatch,
every run-directory write — runs for real against it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Mapping, Sequence
from pathlib import Path

from auto.errors import AutoError
from auto.session.events import (
    BackgroundWork,
    StreamEvent,
    decode_events,
    is_result,
    split_turns,
)
from auto.session.protocol import LaunchSpec

Recording = Path | Sequence[StreamEvent]


class ReplayExhausted(AutoError):
    """The run asked for something the recording does not have.

    Either a node with no fixture, or another turn past the end of one — both
    mean the run under test has drifted from what was recorded.
    """


def _load(recording: Recording) -> list[StreamEvent]:
    if isinstance(recording, Path):
        return list(decode_events(recording.read_text(encoding="utf-8").splitlines()))
    return list(recording)


class ReplaySession:
    """A recorded session, replayed one turn at a time.

    It behaves like a real one on both counts that matter. The stream pauses at
    the `result` event and the session stays alive and idle until it is
    messaged or stopped — unless the turn ended with background work
    outstanding, in which case the session wakes *itself* and the next turn
    follows with nothing sent, exactly as the CLI does when a subagent or a
    backgrounded command reports (ADR-0013).
    """

    def __init__(
        self, spec: LaunchSpec, turns: Sequence[Sequence[StreamEvent]], owner: ReplayLauncher
    ) -> None:
        self._spec = spec
        self._turns = [list(turn) for turn in turns]
        self._owner = owner
        self._gate: asyncio.Queue[str | None] = asyncio.Queue()
        self._closed = False
        self.sent: list[str] = []

    @property
    def session_id(self) -> str:
        return self._spec.session_id

    async def events(self) -> AsyncGenerator[StreamEvent, None]:
        running = BackgroundWork()
        for turn in self._turns:
            for event in turn:
                running.absorb(event)
                yield event
            if not turn or not is_result(turn[-1]):
                # The recording stops mid-turn: the real process would have
                # exited here, so the stream ends rather than idling.
                return
            if running:
                # Work is still running, so the turn end was a boundary and not
                # a pause: the next turn arrives unbidden.
                continue
            if await self._gate.get() is None:
                return
        if running:
            # The recording runs out with work still outstanding: the task
            # reported to nobody, and a real process would be alive and silent
            # rather than gone. Idle until the run brings it down.
            await self._gate.get()
            return
        raise ReplayExhausted(
            f"node {self._spec.node_id!r} was messaged past the end of its "
            f"recording ({len(self._turns)} recorded turn(s))"
        )

    async def send(self, message: str) -> None:
        self.sent.append(message)
        self._owner.sent.append((self._spec.node_id, message))
        await self._gate.put(message)

    @property
    def closed(self) -> bool:
        return self._closed

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            await self._gate.put(None)

    async def terminate(self) -> None:
        await self.close()

    def kill(self) -> None:
        if not self._closed:
            self._closed = True
            self._gate.put_nowait(None)


class ReplayLauncher:
    """Feeds recorded events to a run and records what the run sent back."""

    def __init__(self, fixtures: Mapping[str, Recording]) -> None:
        self._turns = {
            node_id: split_turns(_load(recording))
            for node_id, recording in fixtures.items()
        }
        self.launched: list[LaunchSpec] = []
        self.sent: list[tuple[str, str]] = []
        self.sessions: list[ReplaySession] = []

    async def launch(self, spec: LaunchSpec) -> ReplaySession:
        if spec.node_id not in self._turns:
            raise ReplayExhausted(f"no recording for node {spec.node_id!r}")
        self.launched.append(spec)
        session = ReplaySession(spec, self._turns[spec.node_id], self)
        self.sessions.append(session)
        return session
