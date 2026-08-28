"""The one seam in the system.

Everything the harness does that is not pure computation funnels through a
single act: launching a `claude -p` process and exchanging stream-json with it.
Long-lived driven sessions and short ephemeral orchestrator-agent invocations
both go through here, so a test can drive an entire run for real above this
line and still never call Claude.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from auto.session.events import StreamEvent


@dataclass(frozen=True)
class LaunchSpec:
    """Everything needed to start one session.

    For a driven session `message` is the entry skill's invocation and nothing
    else — no obligations, no reminders, no harness vocabulary — and `model`
    stays `None` so the session runs on whatever the user's setup would use.
    """

    node_id: str
    session_id: str
    cwd: Path
    message: str
    max_budget_usd: float | None = None
    model: str | None = None


@runtime_checkable
class LaunchedSession(Protocol):
    """A live session: a stream of events out, messages in, and a way to stop it."""

    @property
    def session_id(self) -> str: ...

    def events(self) -> AsyncGenerator[StreamEvent, None]:
        """Every stream-json event, in order, until the session ends.

        A generator rather than a bare iterator, so a caller that stops reading
        early can close the stream instead of leaving it dangling.

        A `result` event does not end the stream: with stream-json input the
        session stays alive and idle, waiting to be messaged.
        """

    async def send(self, message: str) -> None:
        """Deliver a message to the live session — one line on its stdin."""

    async def close(self) -> None:
        """Let the session finish: stop writing to it and wait for it to exit."""

    async def terminate(self) -> None:
        """Stop the session now, giving it a chance to shut down cleanly."""

    def kill(self) -> None:
        """Stop the session immediately, without awaiting anything.

        Synchronous so a second interrupt can use it: the process must be dead
        before the harness exits, and there may be no loop left to await on.
        """


class Launcher(Protocol):
    """Starts sessions. One protocol, two implementations: real and replay."""

    async def launch(self, spec: LaunchSpec) -> LaunchedSession: ...
