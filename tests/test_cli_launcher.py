"""The real launcher: what it puts on the command line, and that its process
plumbing works — exercised against a stand-in for `claude`, never the real one."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from auto.errors import SessionLaunchError
from auto.session.cli_launcher import ClaudeCliLauncher, claude_argv
from auto.session.events import is_result
from auto.session.protocol import LaunchSpec

FAKE_CLAUDE = Path(__file__).parent / "stubs" / "fake_claude.py"


def a_spec(**overrides: object) -> LaunchSpec:
    defaults: dict[str, object] = {
        "node_id": "root",
        "session_id": "11111111-2222-4333-8444-555555555555",
        "cwd": Path.cwd(),
        "message": "/wayfinder Add search.",
        "max_budget_usd": 10.0,
    }
    defaults.update(overrides)
    return LaunchSpec(**defaults)  # type: ignore[arg-type]


def test_a_driven_session_streams_json_both_ways() -> None:
    argv = claude_argv(a_spec(), executable="claude")
    assert argv[0] == "claude"
    assert "-p" in argv
    assert argv[argv.index("--input-format") + 1] == "stream-json"
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in argv


def test_the_session_id_is_pre_assigned_not_parsed_back() -> None:
    argv = claude_argv(a_spec(), executable="claude")
    assert argv[argv.index("--session-id") + 1] == (
        "11111111-2222-4333-8444-555555555555"
    )


def test_permissions_are_bypassed() -> None:
    assert "--dangerously-skip-permissions" in claude_argv(a_spec())


def test_the_per_session_spend_ceiling_is_passed_to_the_session() -> None:
    argv = claude_argv(a_spec(max_budget_usd=2.5))
    assert argv[argv.index("--max-budget-usd") + 1] == "2.5"


def test_no_model_override_is_passed_to_a_driven_session() -> None:
    assert "--model" not in claude_argv(a_spec())


def test_a_model_is_passed_only_when_the_spec_pins_one() -> None:
    argv = claude_argv(a_spec(model="claude-opus-5"))
    assert argv[argv.index("--model") + 1] == "claude-opus-5"


def test_the_opening_message_is_not_on_the_command_line() -> None:
    argv = claude_argv(a_spec())
    assert "/wayfinder Add search." not in argv


def an_invocation(**overrides: object) -> LaunchSpec:
    """The other shape the seam launches: one ephemeral judgment."""
    defaults: dict[str, object] = {
        "message": "Judge this node.",
        "model": "claude-opus-5",
        "append_system_prompt": "You are the orchestrator.",
        "mcp_config": '{"mcpServers": {"harness": {"type": "http", "url": "http://x/"}}}',
        "one_shot": True,
        "max_budget_usd": None,
        "allowed_tools": ("mcp__harness__complete_node", "Read"),
    }
    defaults.update(overrides)
    return a_spec(**defaults)


def test_a_one_shot_invocation_takes_its_message_on_the_command_line() -> None:
    """Where slash-command expansion is documented, unlike over stdin."""
    argv = claude_argv(an_invocation())
    assert argv[argv.index("-p") + 1] == "Judge this node."
    assert "--input-format" not in argv


def test_a_one_shot_invocation_appends_to_the_system_prompt_rather_than_replacing() -> None:
    argv = claude_argv(an_invocation())
    assert argv[argv.index("--append-system-prompt") + 1] == "You are the orchestrator."


def test_the_mcp_config_is_inline_and_served_strictly() -> None:
    argv = claude_argv(an_invocation())
    assert "harness" in argv[argv.index("--mcp-config") + 1]
    assert "--strict-mcp-config" in argv


def test_a_driven_session_gets_neither_a_system_prompt_nor_tools() -> None:
    argv = claude_argv(a_spec())
    assert "--append-system-prompt" not in argv
    assert "--mcp-config" not in argv
    assert "--strict-mcp-config" not in argv


def test_an_allowlist_replaces_the_permission_bypass_rather_than_layering_on_it() -> None:
    """The orchestrator agent's limits have to be facts, not requests."""
    argv = claude_argv(an_invocation())
    assert "--dangerously-skip-permissions" not in argv
    assert argv[argv.index("--allowed-tools") + 1] == (
        "mcp__harness__complete_node,Read"
    )


async def test_a_one_shot_invocation_runs_its_turn_and_exits(tmp_path: Path) -> None:
    launcher = ClaudeCliLauncher(executable=[sys.executable, str(FAKE_CLAUDE)])
    session = await launcher.launch(an_invocation(cwd=tmp_path))
    events = [event async for event in session.events()]
    assert events[-1]["result"] == "received: Judge this node."
    assert session.process.returncode == 0


async def test_it_delivers_the_opening_message_and_streams_the_turn(
    tmp_path: Path,
) -> None:
    launcher = ClaudeCliLauncher(executable=[sys.executable, str(FAKE_CLAUDE)])
    session = await launcher.launch(a_spec(cwd=tmp_path))
    events = []
    async for event in session.events():
        events.append(event)
        if is_result(event):
            break
    assert events[0]["subtype"] == "init"
    assert events[-1]["result"] == "received: /wayfinder Add search."
    await session.close()


async def test_it_launches_in_the_target_repo(tmp_path: Path) -> None:
    launcher = ClaudeCliLauncher(executable=[sys.executable, str(FAKE_CLAUDE)])
    session = await launcher.launch(a_spec(cwd=tmp_path))
    async for event in session.events():
        break
    assert session.process.pid > 0
    await session.close()


async def test_a_live_session_can_be_messaged(tmp_path: Path) -> None:
    launcher = ClaudeCliLauncher(executable=[sys.executable, str(FAKE_CLAUDE)])
    session = await launcher.launch(a_spec(cwd=tmp_path))
    stream = session.events()
    async for event in stream:
        if is_result(event):
            break
    await session.send("/to-tickets")
    async for event in stream:
        if is_result(event):
            assert event["result"] == "received: /to-tickets"
            break
    await session.close()


async def test_an_event_line_larger_than_the_default_stream_limit_still_arrives(
    tmp_path: Path,
) -> None:
    """A single stream-json event can far exceed asyncio's 64 KiB readline
    limit — a big tool result, a base64 image — and must stream through
    rather than kill the run with LimitOverrunError."""
    big_message = "/wayfinder " + "x" * 200_000
    launcher = ClaudeCliLauncher(executable=[sys.executable, str(FAKE_CLAUDE)])
    session = await launcher.launch(a_spec(cwd=tmp_path, message=big_message))
    events = []
    async for event in session.events():
        events.append(event)
        if is_result(event):
            break
    assert events[-1]["result"] == f"received: {big_message}"
    await session.close()


async def test_closing_a_session_lets_it_exit(tmp_path: Path) -> None:
    launcher = ClaudeCliLauncher(executable=[sys.executable, str(FAKE_CLAUDE)])
    session = await launcher.launch(a_spec(cwd=tmp_path))
    async for event in session.events():
        if is_result(event):
            break
    await session.close()
    assert session.process.returncode == 0


async def test_terminating_a_session_stops_it(tmp_path: Path) -> None:
    launcher = ClaudeCliLauncher(executable=[sys.executable, str(FAKE_CLAUDE)])
    session = await launcher.launch(a_spec(cwd=tmp_path))
    async for event in session.events():
        if is_result(event):
            break
    await session.terminate()
    assert session.process.returncode is not None


async def test_a_session_that_will_not_start_reports_its_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_FAIL_TO_START", "1")
    launcher = ClaudeCliLauncher(executable=[sys.executable, str(FAKE_CLAUDE)])
    session = await launcher.launch(a_spec(cwd=tmp_path))
    with pytest.raises(SessionLaunchError, match="refusing to start"):
        async for _ in session.events():
            pass


async def test_a_missing_claude_binary_is_a_readable_error(tmp_path: Path) -> None:
    launcher = ClaudeCliLauncher(executable=["definitely-not-a-real-binary-xyz"])
    with pytest.raises(SessionLaunchError, match="definitely-not-a-real-binary-xyz"):
        await launcher.launch(a_spec(cwd=tmp_path))


async def test_two_sessions_can_run_at_once(tmp_path: Path) -> None:
    launcher = ClaudeCliLauncher(executable=[sys.executable, str(FAKE_CLAUDE)])
    specs = [a_spec(node_id=f"n{i}", session_id=f"id-{i}", cwd=tmp_path) for i in (1, 2)]
    sessions = await asyncio.gather(*(launcher.launch(spec) for spec in specs))
    for session in sessions:
        async for event in session.events():
            if is_result(event):
                break
        await session.close()
    assert {s.session_id for s in sessions} == {"id-1", "id-2"}


def test_the_claude_binary_can_be_pointed_elsewhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from auto.session.cli_launcher import claude_executable

    monkeypatch.delenv("AUTO_CLAUDE_BIN", raising=False)
    assert claude_executable() == "claude"

    monkeypatch.setenv("AUTO_CLAUDE_BIN", "uv run claude")
    assert claude_executable() == ["uv", "run", "claude"]
