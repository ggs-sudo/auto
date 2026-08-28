"""The real launcher: an actual `claude -p` process, spoken to in stream-json.

Driven sessions launch with permissions **bypassed** and no model override.
Bypass rather than an allowlist because a permission denial inside a
third-party skill does not stop it — it silently routes around, which is the
failure mode that cannot be diagnosed afterwards.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shlex
from collections.abc import AsyncGenerator, Sequence
from pathlib import Path

from auto.errors import SessionLaunchError
from auto.session.events import StreamEvent, decode_events, user_message_line
from auto.session.protocol import LaunchSpec

DEFAULT_EXECUTABLE = "claude"
CLAUDE_BIN_ENV_VAR = "AUTO_CLAUDE_BIN"


def claude_executable() -> str | Sequence[str]:
    """`claude` on PATH, unless `$AUTO_CLAUDE_BIN` names something else.

    Shell-quoted, so it can carry arguments — which is what makes an
    end-to-end smoke test against a stand-in binary possible.
    """
    override = os.environ.get(CLAUDE_BIN_ENV_VAR)
    return shlex.split(override) if override else DEFAULT_EXECUTABLE

TERMINATE_GRACE_SECONDS = 5.0


def claude_argv(
    spec: LaunchSpec, executable: str | Sequence[str] = DEFAULT_EXECUTABLE
) -> list[str]:
    """The command line for one session.

    The opening message is deliberately *not* here: it goes over stdin as
    stream-json, which is what keeps the process alive past its first turn so
    the orchestrator can message it later. See the note in the README about the
    one thing this relies on that has not been verified against a live CLI.
    """
    argv = [executable] if isinstance(executable, str) else list(executable)
    argv += [
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--session-id",
        spec.session_id,
        "--dangerously-skip-permissions",
    ]
    if spec.max_budget_usd is not None:
        argv += ["--max-budget-usd", _format_amount(spec.max_budget_usd)]
    if spec.model is not None:
        argv += ["--model", spec.model]
    return argv


def _format_amount(amount: float) -> str:
    return f"{amount:g}"


class ClaudeCliSession:
    """One live `claude -p` process."""

    def __init__(
        self, spec: LaunchSpec, process: asyncio.subprocess.Process, argv: Sequence[str]
    ) -> None:
        self._spec = spec
        self.process = process
        self.argv = list(argv)
        self._stderr: list[str] = []
        self._stderr_task: asyncio.Task[None] | None = None
        self._stopped = False

    @property
    def session_id(self) -> str:
        return self._spec.session_id

    async def events(self) -> AsyncGenerator[StreamEvent, None]:
        assert self.process.stdout is not None
        while True:
            raw = await self.process.stdout.readline()
            if not raw:
                break
            for event in decode_events([raw.decode("utf-8", errors="replace")]):
                yield event
        await self.process.wait()
        if self.process.returncode not in (0, None) and not self._stopped:
            raise SessionLaunchError(
                f"claude exited {self.process.returncode} for node "
                f"{self._spec.node_id!r}: {self.stderr().strip() or '(no stderr)'}"
            )

    async def send(self, message: str) -> None:
        stdin = self.process.stdin
        if stdin is None or stdin.is_closing():
            raise SessionLaunchError(
                f"cannot message node {self._spec.node_id!r}: its session is gone"
            )
        stdin.write(user_message_line(message).encode("utf-8"))
        await stdin.drain()

    async def close(self) -> None:
        """Stop writing and let the session exit on its own."""
        self._stopped = True
        if self.process.stdin is not None and not self.process.stdin.is_closing():
            self.process.stdin.close()
        with contextlib.suppress(ProcessLookupError):
            await self.process.wait()

    async def terminate(self) -> None:
        """SIGTERM, then SIGKILL if it does not go quietly."""
        self._stopped = True
        if self.process.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            self.process.terminate()
        try:
            await asyncio.wait_for(self.process.wait(), TERMINATE_GRACE_SECONDS)
        except TimeoutError:
            self.kill()
            with contextlib.suppress(ProcessLookupError):
                await self.process.wait()

    def kill(self) -> None:
        self._stopped = True
        if self.process.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError, OSError):
            self.process.kill()

    def stderr(self) -> str:
        """Whatever the process wrote to stderr, for diagnosing a bad launch."""
        return "".join(self._stderr)

    def start_capturing_stderr(self) -> None:
        """Keep stderr drained so a chatty process cannot block on a full pipe."""
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def _drain_stderr(self) -> None:
        assert self.process.stderr is not None
        async for line in self.process.stderr:
            self._stderr.append(line.decode("utf-8", errors="replace"))


class ClaudeCliLauncher:
    """Launches real sessions."""

    def __init__(self, executable: str | Sequence[str] | None = None) -> None:
        self._executable = executable if executable is not None else claude_executable()

    async def launch(self, spec: LaunchSpec) -> ClaudeCliSession:
        argv = claude_argv(spec, self._executable)
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(spec.cwd),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise SessionLaunchError(f"cannot launch {argv[0]!r}: {exc}") from exc

        session = ClaudeCliSession(spec, process, argv)
        session.start_capturing_stderr()
        await session.send(spec.message)
        return session
