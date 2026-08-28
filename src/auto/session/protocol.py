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
    else — no obligations, no reminders, no harness vocabulary — `model` stays
    `None` so the session runs on whatever the user's setup would use, and
    every field below `model` stays unset: driven sessions get no harness tools
    and no altered system prompt.

    An ephemeral orchestrator-agent invocation is the other shape: `one_shot`,
    a pinned `model`, the run's stable material appended to the system prompt,
    an inline MCP config whose URL scopes it to a single node, and a tool
    allowlist that keeps it unable to write anything.
    """

    node_id: str
    session_id: str
    cwd: Path
    message: str
    max_budget_usd: float | None = None
    model: str | None = None
    append_system_prompt: str | None = None
    """Appended to the default system prompt, never replacing it."""

    mcp_config: str | None = None
    """Inline MCP config, as JSON. Always served strictly: it is the whole
    tool roster for the invocation, not an addition to a configured one."""

    allowed_tools: tuple[str, ...] | None = None
    """The only tools this session may use, or None to permit everything.

    An allowlist and a permission bypass are alternatives, not layers: a
    session with no allowlist has permissions bypassed, and a session with one
    is held to it. Driven sessions take the bypass, because a permission denial
    inside a third-party skill does not stop it — it silently routes around,
    which is the failure mode that cannot be diagnosed afterwards. The
    orchestrator agent takes the allowlist, because "its prose changes nothing"
    has to be a fact about what it *can* do and not a request in its prompt.
    """

    one_shot: bool = False
    """Whether the process runs a single turn and exits.

    A driven session is not one-shot: it takes its opening message on stdin as
    stream-json and stays alive and idle past its `result` event, which is what
    lets the orchestrator message it later. An orchestrator-agent invocation is
    ephemeral by definition, so it takes its message on the command line — the
    documented, verified way to invoke a skill — and exits when its turn ends.
    """


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
