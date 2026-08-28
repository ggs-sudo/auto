"""The stream-json codec.

`claude -p --output-format stream-json` writes newline-delimited JSON events;
`--input-format stream-json` reads user messages in the same shape. Everything
the harness knows about a session arrives through here, and the `result` event
is the one that matters most: it *is* stale.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from typing import Any, cast

from auto.errors import AutoError
from auto.model.session import Telemetry

StreamEvent = dict[str, Any]

RESULT_EVENT_TYPE = "result"


class StreamProtocolError(AutoError):
    """A session wrote something that is not the stream-json we expect."""


def decode_events(lines: Iterable[str]) -> Iterator[StreamEvent]:
    """Parse stream-json lines. Blank lines are noise; anything else must parse."""
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise StreamProtocolError(
                f"session stream line is not JSON: {stripped[:200]!r}"
            ) from exc
        if not isinstance(event, dict):
            raise StreamProtocolError(
                f"session stream line is not a JSON object: {stripped[:200]!r}"
            )
        yield cast(StreamEvent, event)


def is_result(event: StreamEvent) -> bool:
    """Whether this event is the turn boundary — the definition of stale.

    Not a heuristic and not a quiet-period timer: the CLI computes it.
    """
    return event.get("type") == RESULT_EVENT_TYPE


def telemetry_from_result(event: StreamEvent) -> Telemetry:
    """Everything the result event says about what the turn cost and how it ended.

    Every field is coerced defensively: the harness reads a stream it does not
    own, and a missing or reshaped field must not take a run down.
    """
    return Telemetry(
        cost_usd=_optional_float(event.get("total_cost_usd")),
        num_turns=_optional_int(event.get("num_turns")),
        duration_ms=_optional_int(event.get("duration_ms")),
        stop_reason=_optional_str(event.get("stop_reason")),
        terminal_reason=_optional_str(event.get("terminal_reason")),
        is_error=_optional_bool(event.get("is_error")),
        usage=_optional_dict(event.get("usage")),
        model_usage=_optional_dict(event.get("modelUsage")),
        permission_denials=_optional_list(event.get("permission_denials")),
    )


def result_summary(event: StreamEvent) -> str | None:
    """The session's final assistant text, as the result event carries it."""
    return _optional_str(event.get("result"))


def user_message_line(text: str) -> str:
    """One line of stream-json input: how the harness talks to a live session."""
    return (
        json.dumps(
            {
                "type": "user",
                "message": {"role": "user", "content": [{"type": "text", "text": text}]},
            }
        )
        + "\n"
    )


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _optional_dict(value: Any) -> dict[str, Any] | None:
    return cast(dict[str, Any], value) if isinstance(value, dict) else None


def _optional_list(value: Any) -> list[Any] | None:
    return value if isinstance(value, list) else None


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    return float(value) if isinstance(value, (int, float)) else None
