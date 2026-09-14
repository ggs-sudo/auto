"""The real launcher: an actual `claude -p` process, spoken to in stream-json.

Driven sessions launch with permissions **bypassed** and no model override.
Bypass rather than an allowlist because a permission denial inside a
third-party skill does not stop it — it silently routes around, which is the
failure mode that cannot be diagnosed afterwards.

The orchestrator agent is the opposite case and launches under an explicit
allowlist: it runs a prompt the harness wrote, so a denial is a bug in the
harness rather than a third party going quiet, and the allowlist is what makes
"an agent that rambles cannot corrupt state" true rather than merely asked for.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shlex
from collections.abc import AsyncGenerator, Sequence
from pathlib import Path

from auto.errors import SessionLaunchError
from auto.session.events import StreamEvent, decode_events, user_message_line
from auto.session.protocol import LaunchSpec

logger = logging.getLogger(__name__)

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


async def _readline_unbounded(reader: asyncio.StreamReader) -> bytes:
    """One line, however long.

    `StreamReader.readline` refuses lines past its 64 KiB limit, and a single
    stream-json event — a fat tool result, a base64 screenshot — routinely is.
    No limit is big enough to be a fact rather than a bet, so accumulate
    through `LimitOverrunError` instead of raising the ceiling.
    """
    chunks: list[bytes] = []
    while True:
        try:
            chunks.append(await reader.readuntil(b"\n"))
            break
        except asyncio.IncompleteReadError as exc:
            chunks.append(exc.partial)
            break
        except asyncio.LimitOverrunError as exc:
            chunks.append(await reader.readexactly(exc.consumed))
    return b"".join(chunks)


def claude_argv(
    spec: LaunchSpec, executable: str | Sequence[str] = DEFAULT_EXECUTABLE
) -> list[str]:
    """The command line for one session.

    For a driven session the opening message is deliberately *not* here: it
    goes over stdin as stream-json, which is what keeps the process alive past
    its first turn so the orchestrator can message it later. See the note in
    the README about the one thing this relies on that has not been verified
    against a live CLI. A one-shot invocation has nothing to stay alive for, so
    its message goes on the command line, where skill expansion is documented.
    """
    argv = [executable] if isinstance(executable, str) else list(executable)
    argv += ["-p"]
    if spec.one_shot:
        argv += [spec.message]
    else:
        argv += ["--input-format", "stream-json"]
    argv += [
        "--output-format",
        "stream-json",
        "--verbose",
        "--session-id",
        spec.session_id,
    ]
    if spec.allowed_tools is None:
        argv += ["--dangerously-skip-permissions"]
    else:
        argv += ["--allowed-tools", ",".join(spec.allowed_tools)]
    if spec.max_budget_usd is not None:
        argv += ["--max-budget-usd", _format_amount(spec.max_budget_usd)]
    if spec.model is not None:
        argv += ["--model", spec.model]
    if spec.append_system_prompt is not None:
        argv += ["--append-system-prompt", spec.append_system_prompt]
    if spec.mcp_config is not None:
        # Strict, always: the inline config *is* the invocation's tool roster,
        # so nothing the user happens to have configured can join it.
        argv += ["--mcp-config", spec.mcp_config, "--strict-mcp-config"]
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
            raw = await _readline_unbounded(self.process.stdout)
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

    async def finish_input(self) -> None:
        """Say there is nothing more to send, without waiting for the exit."""
        if self.process.stdin is not None and not self.process.stdin.is_closing():
            self.process.stdin.close()

    async def close(self) -> None:
        """Stop writing and let the session exit on its own."""
        self._stopped = True
        await self.finish_input()
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
        while True:
            line = await _readline_unbounded(self.process.stderr)
            if not line:
                break
            self._stderr.append(line.decode("utf-8", errors="replace"))


class ClaudeCliLauncher:
    """Launches real sessions."""

    def __init__(self, executable: str | Sequence[str] | None = None) -> None:
        self._executable = executable if executable is not None else claude_executable()

    async def launch(self, spec: LaunchSpec) -> ClaudeCliSession:
        argv = claude_argv(spec, self._executable)
        logger.debug(
            "spawning claude session %s for node %s in %s: %s",
            spec.session_id,
            spec.node_id,
            spec.cwd,
            shlex.join(argv),
        )
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

        logger.debug(
            "claude session %s is up: pid %s", spec.session_id, process.pid
        )
        session = ClaudeCliSession(spec, process, argv)
        session.start_capturing_stderr()
        if spec.one_shot:
            # The message was on the command line; closing stdin is what tells
            # the process there is no further turn coming.
            await session.finish_input()
        else:
            await session.send(spec.message)
        return session
