"""The stream-json codec: what the harness reads off a session, and what it
writes back to one."""

from __future__ import annotations

import pytest

from auto.session.events import (
    StreamProtocolError,
    decode_events,
    is_result,
    result_summary,
    telemetry_from_result,
    user_message_line,
)
from tests.conftest import assistant_event, init_event, result_event


def test_the_result_event_is_what_stale_means() -> None:
    assert is_result(result_event()) is True
    assert is_result(init_event()) is False
    assert is_result(assistant_event("hi")) is False


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
