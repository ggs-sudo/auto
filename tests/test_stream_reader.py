"""Reading a session's stream with a deadline, and without losing events.

The loop has to be able to stop waiting on a stream — to notice an abort, or to
count how long a session has said nothing — and still mean to read the same
event afterwards. Everything below is about that seam holding.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest

from auto.orchestrate import _StreamReader
from auto.session.events import StreamEvent
from tests.conftest import assistant_event, result_event


async def _slowly(
    *events: StreamEvent, delay: float = 0.05
) -> AsyncGenerator[StreamEvent, None]:
    """A stream that makes the reader wait for every event it yields."""
    for event in events:
        await asyncio.sleep(delay)
        yield event


async def test_a_wait_that_expires_reports_quiet_rather_than_ended() -> None:
    reader = _StreamReader(_slowly(assistant_event("hi")))
    assert await reader.next(0.001) is None
    assert reader.ended is False
    await reader.aclose()


async def test_an_event_the_reader_waited_through_is_not_lost() -> None:
    """The read is kept across an expired wait; only the waiting is abandoned.

    Cancelling the read itself would be the easy mistake: the codec builds a
    long line across several awaits, so a cancelled read can drop what it has
    already taken off the pipe.
    """
    reader = _StreamReader(_slowly(assistant_event("first"), assistant_event("second")))

    for _ in range(200):
        event = await reader.next(0.001)
        if event is not None:
            break
    else:  # pragma: no cover - the stream is 50ms away, not never
        pytest.fail("the reader never delivered an event it had waited through")

    assert event == assistant_event("first")
    assert await reader.next(1.0) == assistant_event("second")
    await reader.aclose()


async def test_the_end_of_the_stream_is_told_apart_from_silence() -> None:
    reader = _StreamReader(_slowly(result_event(), delay=0.0))
    assert await reader.next(1.0) == result_event()
    assert await reader.next(1.0) is None
    assert reader.ended is True
    await reader.aclose()


async def test_a_reader_past_the_end_stays_past_it() -> None:
    reader = _StreamReader(_slowly(delay=0.0))
    assert await reader.next(1.0) is None
    assert reader.ended is True
    assert await reader.next(1.0) is None
    await reader.aclose()


async def test_closing_a_reader_mid_wait_leaves_nothing_running() -> None:
    reader = _StreamReader(_slowly(assistant_event("hi"), delay=30.0))
    assert await reader.next(0.001) is None
    await reader.aclose()
    assert [task for task in asyncio.all_tasks() if task is not asyncio.current_task()] == []
