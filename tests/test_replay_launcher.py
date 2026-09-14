"""The replay launcher drives a run from recorded events, in exactly the format
the harness captures transcripts in, and records what was sent back."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from auto.session.events import is_result
from auto.session.protocol import LaunchSpec
from auto.session.replay import ReplayExhausted, ReplayLauncher
from tests.conftest import drained_turn, one_turn, waiting_turn, write_fixture


def a_spec(node_id: str = "root", session_id: str = "s-1") -> LaunchSpec:
    return LaunchSpec(
        node_id=node_id,
        session_id=session_id,
        cwd=Path("/tmp/target"),
        message="/wayfinder Add search.",
    )


async def drain(session: object) -> list[dict[str, object]]:
    events = []
    async for event in session.events():  # type: ignore[attr-defined]
        events.append(event)
        if is_result(event):
            break
    return events


async def test_it_replays_a_recorded_turn_up_to_the_result_event() -> None:
    launcher = ReplayLauncher({"root": one_turn()})
    session = await launcher.launch(a_spec())
    assert await drain(session) == one_turn()
    await session.close()


async def test_a_fixture_can_be_a_captured_transcript_on_disk(
    tmp_path: Path,
) -> None:
    fixture = write_fixture(tmp_path / "root.jsonl", one_turn())
    launcher = ReplayLauncher({"root": fixture})
    session = await launcher.launch(a_spec())
    assert await drain(session) == one_turn()
    await session.close()


async def test_the_session_reports_the_id_it_was_launched_with() -> None:
    launcher = ReplayLauncher({"root": one_turn()})
    session = await launcher.launch(a_spec(session_id="assigned-id"))
    assert session.session_id == "assigned-id"
    await session.close()


async def test_it_records_every_launch() -> None:
    launcher = ReplayLauncher({"root": one_turn()})
    await launcher.launch(a_spec())
    assert [spec.node_id for spec in launcher.launched] == ["root"]
    assert launcher.launched[0].message == "/wayfinder Add search."


async def test_it_records_what_was_sent_back() -> None:
    launcher = ReplayLauncher(
        {"root": [*one_turn("First."), *one_turn("Second.")]}
    )
    session = await launcher.launch(a_spec())
    stream = session.events()
    async for event in stream:
        if is_result(event):
            break
    await session.send("Write the tickets now.")
    async for event in stream:
        if is_result(event):
            break
    await session.close()
    assert launcher.sent == [("root", "Write the tickets now.")]


async def test_a_session_stays_alive_after_its_result_event() -> None:
    launcher = ReplayLauncher(
        {"root": [*one_turn("First."), *one_turn("Second.")]}
    )
    session = await launcher.launch(a_spec())
    stream = session.events()
    first = []
    async for event in stream:
        first.append(event)
        if is_result(event):
            break
    assert len(first) == 3

    await session.send("Carry on.")
    second = []
    async for event in stream:
        second.append(event)
        if is_result(event):
            break
    assert second == one_turn("Second.")
    await session.close()


async def test_closing_a_session_ends_its_event_stream() -> None:
    launcher = ReplayLauncher(
        {"root": [*one_turn("First."), *one_turn("Second.")]}
    )
    session = await launcher.launch(a_spec())
    stream = session.events()
    async for event in stream:
        if is_result(event):
            break
    await session.close()
    assert [event async for event in stream] == []


async def test_terminating_a_session_ends_its_event_stream() -> None:
    launcher = ReplayLauncher({"root": one_turn()})
    session = await launcher.launch(a_spec())
    stream = session.events()
    async for event in stream:
        if is_result(event):
            break
    await session.terminate()
    assert [event async for event in stream] == []


async def test_sending_past_the_end_of_a_recording_fails_loudly() -> None:
    launcher = ReplayLauncher({"root": one_turn()})
    session = await launcher.launch(a_spec())
    stream = session.events()
    async for event in stream:
        if is_result(event):
            break
    await session.send("One more thing.")
    with pytest.raises(ReplayExhausted, match="root"):
        async for _ in stream:
            pass


async def test_launching_an_unrecorded_node_fails_loudly() -> None:
    launcher = ReplayLauncher({"root": one_turn()})
    with pytest.raises(ReplayExhausted, match="other-node"):
        await launcher.launch(a_spec(node_id="other-node"))


async def test_a_recording_that_stops_before_stale_ends_the_stream() -> None:
    """A truncated recording is a process that exited, not a session idling."""
    truncated = one_turn()[:-1]
    launcher = ReplayLauncher({"root": truncated})
    session = await launcher.launch(a_spec())
    assert [event async for event in session.events()] == truncated
    await session.close()


async def test_a_turn_that_ends_with_work_running_is_followed_without_a_message() -> None:
    """The CLI wakes such a session itself, so the replayed one must too.

    A replay that made the harness send a message to get the next turn would
    hide exactly the bug this models: a turn boundary is not a stale point
    while background work is outstanding (ADR-0013).
    """
    recording = [
        *waiting_turn("Reviews are running.", "a-spec"),
        *drained_turn("Reviews are back."),
    ]
    launcher = ReplayLauncher({"root": recording})
    session = await launcher.launch(a_spec())

    seen = []
    async for event in session.events():
        seen.append(event)
        if len(seen) == len(recording):
            break

    assert seen == recording
    assert session.sent == [], "the second turn arrived unbidden"
    await session.close()


async def test_a_recording_that_runs_out_with_work_running_idles_rather_than_ends() -> None:
    """A task that reports to nobody leaves a live, silent process behind."""
    launcher = ReplayLauncher({"root": waiting_turn("Reviews are running.", "a-spec")})
    session = await launcher.launch(a_spec())
    stream = session.events()
    async for event in stream:
        if is_result(event):
            break

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(anext(stream), 0.05)

    await session.close()
