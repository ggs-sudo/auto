"""The stream-json codec.

`claude -p --output-format stream-json` writes newline-delimited JSON events;
`--input-format stream-json` reads user messages in the same shape. Everything
the harness knows about a session arrives through here, and two of those things
decide when a node may be judged: the `result` event, which ends a turn, and
`background_tasks_changed`, which says what the session still has running.

A turn boundary is not the same thing as a stale point. A session that ends its
turn with a subagent or a background command outstanding is *not* idle — the
CLI re-invokes it, unprompted and on this same stream, when the task reports.
Staleness is a turn boundary with nothing left running (ADR-0013).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Sequence
from typing import Any, cast

from auto.errors import AutoError
from auto.model.session import Telemetry

StreamEvent = dict[str, Any]

RESULT_EVENT_TYPE = "result"
SYSTEM_EVENT_TYPE = "system"
BACKGROUND_TASKS_SUBTYPE = "background_tasks_changed"


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
    """Whether this event ends a turn.

    Not a heuristic and not a quiet-period timer: the CLI computes it. It is
    *not* on its own the definition of stale — see `BackgroundWork`.
    """
    return event.get("type") == RESULT_EVENT_TYPE


class BackgroundWork:
    """What a session has running in the background, as the session reports it.

    `background_tasks_changed` carries the *whole* live set every time it fires
    — a snapshot, not a delta — so keeping up with it is assignment rather than
    bookkeeping, and a set that has drained arrives as an empty list. Both
    kinds of background work appear in it: `local_agent` for a subagent the
    session did not wait for, `local_bash` for a backgrounded command.

    This is the difference between a turn boundary and a stale point. While
    anything is outstanding the session is idle only for the moment: the CLI
    wakes it when the task reports, with no input from the harness, and reading
    on is the whole of what the harness has to do (ADR-0013).
    """

    def __init__(self) -> None:
        self._outstanding: tuple[str, ...] = ()

    def absorb(self, event: StreamEvent) -> None:
        """Take in one event. Only the snapshot event says anything."""
        if event.get("type") != SYSTEM_EVENT_TYPE:
            return
        if event.get("subtype") != BACKGROUND_TASKS_SUBTYPE:
            return
        tasks = event.get("tasks")
        if not isinstance(tasks, list):
            # A reshaped snapshot is read as no snapshot at all, rather than as
            # an empty one: the harness reads a stream it does not own, and
            # guessing "nothing is running" is the guess that kills a node.
            return
        self._outstanding = tuple(
            str(task["task_id"])
            for task in tasks
            if isinstance(task, dict) and task.get("task_id") is not None
        )

    @property
    def outstanding(self) -> tuple[str, ...]:
        """The task ids the session last said it had running."""
        return self._outstanding

    def __bool__(self) -> bool:
        return bool(self._outstanding)


def split_turns(events: Sequence[StreamEvent]) -> list[list[StreamEvent]]:
    """Cut a stream at its `result` events — one list per turn.

    A trailing group with no `result` is still a turn: it is a stream that
    stopped before the session went stale.
    """
    turns: list[list[StreamEvent]] = []
    current: list[StreamEvent] = []
    for event in events:
        current.append(event)
        if is_result(event):
            turns.append(current)
            current = []
    if current:
        turns.append(current)
    return turns


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
