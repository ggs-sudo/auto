"""The stream-json codec: what the harness reads off a session, and what it
writes back to one."""

from __future__ import annotations

from pathlib import Path

import pytest

from auto.session.events import (
    BackgroundWork,
    StreamProtocolError,
    decode_events,
    is_result,
    result_summary,
    telemetry_from_result,
    user_message_line,
)
from tests.conftest import (
    assistant_event,
    background_tasks_event,
    init_event,
    result_event,
)


def test_the_result_event_is_what_ends_a_turn() -> None:
    assert is_result(result_event()) is True
    assert is_result(init_event()) is False
    assert is_result(assistant_event("hi")) is False


def test_a_session_starts_with_nothing_running() -> None:
    assert not BackgroundWork()
    assert list(BackgroundWork().outstanding) == []


def test_the_snapshot_event_replaces_the_whole_outstanding_set() -> None:
    """It is a snapshot, not a delta: the last one said is all that is running."""
    running = BackgroundWork()
    running.absorb(background_tasks_event("a-spec"))
    assert list(running.outstanding) == ["a-spec"]
    running.absorb(background_tasks_event("a-spec", "a-standards"))
    assert list(running.outstanding) == ["a-spec", "a-standards"]
    running.absorb(background_tasks_event())
    assert list(running.outstanding) == []
    assert not running


def test_events_that_are_not_the_snapshot_say_nothing_about_what_is_running() -> None:
    running = BackgroundWork()
    running.absorb(background_tasks_event("a-spec"))
    for event in (init_event(), assistant_event("hi"), result_event()):
        running.absorb(event)
    assert list(running.outstanding) == ["a-spec"]


def test_a_reshaped_snapshot_is_read_as_no_snapshot_rather_than_an_empty_one() -> None:
    """Guessing "nothing is running" is the guess that kills a node."""
    running = BackgroundWork()
    running.absorb(background_tasks_event("a-spec"))
    running.absorb({"type": "system", "subtype": "background_tasks_changed"})
    running.absorb({"type": "system", "subtype": "background_tasks_changed", "tasks": 3})
    assert list(running.outstanding) == ["a-spec"]


def test_telemetry_comes_off_the_result_event() -> None:
    telemetry = telemetry_from_result(result_event(cost_usd=1.25, num_turns=7))
    assert telemetry.cost_usd == 1.25
    assert telemetry.num_turns == 7
    assert telemetry.stop_reason == "end_turn"
    assert telemetry.terminal_reason == "completed"
    assert telemetry.is_error is False
    assert telemetry.usage == {"input_tokens": 10, "output_tokens": 20}
    assert telemetry.model_usage == {"claude-opus-5": {"costUSD": 1.25}}


def test_telemetry_tolerates_a_sparse_result_event() -> None:
    telemetry = telemetry_from_result({"type": "result"})
    assert telemetry.cost_usd is None
    assert telemetry.num_turns is None


def test_the_result_text_is_the_sessions_summary() -> None:
    assert result_summary(result_event("Wrote three tickets.")) == (
        "Wrote three tickets."
    )
    assert result_summary({"type": "result"}) is None


def test_decoding_skips_blank_lines() -> None:
    lines = ['{"type": "system"}', "", "   ", '{"type": "result"}']
    assert list(decode_events(lines)) == [{"type": "system"}, {"type": "result"}]


def test_decoding_a_malformed_line_fails_loudly() -> None:
    with pytest.raises(StreamProtocolError, match="not JSON"):
        list(decode_events(["not json at all"]))


def test_decoding_a_non_object_line_fails_loudly() -> None:
    with pytest.raises(StreamProtocolError, match="not a JSON object"):
        list(decode_events(["[1, 2, 3]"]))


def test_a_message_to_a_session_is_one_stream_json_line() -> None:
    line = user_message_line("/wayfinder Add search.")
    assert line.endswith("\n")
    assert "\n" not in line[:-1]
    import json

    assert json.loads(line) == {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": "/wayfinder Add search."}],
        },
    }


def test_the_real_stream_that_cost_two_nodes_reads_as_work_still_running() -> None:
    """Every `background_tasks_changed` and `result` node 03 actually sent.

    Run 20260914-142308 failed this node at the second of those results, on the
    reasoning that a session which has ended its turn does nothing further on
    its own. Two review subagents were outstanding at both — so neither was a
    stale point, and the shapes here are the CLI's own, not the harness's idea
    of them (ADR-0013).
    """
    fixture = Path(__file__).parent / "fixtures" / "node-03-background-reviews.jsonl"
    running = BackgroundWork()
    judged = held = 0
    for event in decode_events(fixture.read_text().splitlines()):
        running.absorb(event)
        if is_result(event):
            if running:
                held += 1
            else:
                judged += 1

    assert (judged, held) == (0, 2), "both turn boundaries came with work running"
    assert list(running.outstanding) == ["a23ec3fcfd1a90a4c", "ad621467f4431e075"]
