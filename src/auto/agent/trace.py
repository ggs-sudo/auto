"""What an ephemeral agent is shown of the session it is judging.

Depth is a property of the node's type, not a setting. A **monitored** node —
grilling, wayfinder — spawns a graph out of its whole conversation, so the
whole conversation is the material a judgment is made from. An **autonomous**
node is read from the previous stale point: the turn that just ended, which at
the first stale point is the whole session anyway.

One turn is still capable of being enormous. It is truncated from the *middle*,
so what the session set out to do and what it finally wrote both survive — the
two ends are where the judgment lives, and the tool traffic between them is
what is safe to lose.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from auto.model.common import NodeType
from auto.session.events import StreamEvent, is_result, split_turns

MAX_TURN_CHARS = 24_000
"""Where a single turn stops being worth reading in full."""

MAX_TOOL_RESULT_CHARS = 2_000
"""A file a session read is not the session's thinking. Enough to recognise it."""


def turns_for(node_type: NodeType, events: Sequence[StreamEvent]) -> list[list[StreamEvent]]:
    """The turns this node's type is judged on."""
    turns = split_turns(events)
    if node_type.monitored or not turns:
        return turns
    return turns[-1:]


def render(
    node_type: NodeType,
    events: Sequence[StreamEvent],
    *,
    max_turn_chars: int = MAX_TURN_CHARS,
) -> str:
    """The trace as prose the agent reads, one block per turn."""
    turns = turns_for(node_type, events)
    if not turns:
        return "(the session has produced nothing yet)"
    blocks = [
        truncate_middle(render_turn(turn), max_turn_chars) for turn in turns
    ]
    if len(blocks) == 1:
        return blocks[0]
    return "\n\n".join(
        f"### Turn {number}\n\n{block}"
        for number, block in enumerate(blocks, start=1)
    )


def render_turn(events: Sequence[StreamEvent]) -> str:
    """One turn, flattened to text. Stream bookkeeping is dropped."""
    lines = [rendered for event in events if (rendered := render_event(event))]
    return "\n\n".join(lines)


def render_event(event: StreamEvent) -> str | None:
    """One event, or None when it says nothing a reader needs."""
    if is_result(event):
        return _render_result(event)
    kind = event.get("type")
    if kind == "assistant":
        return _render_blocks("session", event)
    if kind == "user":
        return _render_blocks("you", event)
    return None


def _render_blocks(speaker: str, event: StreamEvent) -> str | None:
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return f"{speaker}: {content}" if content.strip() else None
    if not isinstance(content, list):
        return None
    parts = [rendered for block in content if (rendered := _render_block(block))]
    if not parts:
        return None
    return f"{speaker}: " + "\n".join(parts)


def _render_block(block: Any) -> str | None:
    if not isinstance(block, dict):
        return None
    kind = block.get("type")
    if kind == "text":
        text = block.get("text")
        return text.strip() if isinstance(text, str) and text.strip() else None
    if kind == "tool_use":
        return f"[used {block.get('name')}: {_compact(block.get('input'))}]"
    if kind == "tool_result":
        return f"[tool result: {_compact(block.get('content'))}]"
    return None


def _render_result(event: StreamEvent) -> str:
    stop = event.get("stop_reason") or event.get("subtype") or "unknown"
    parts = [f"[the session's turn ended: {stop}"]
    turns = event.get("num_turns")
    if isinstance(turns, int):
        parts.append(f", {turns} turns")
    if event.get("is_error"):
        parts.append(", and the CLI reported an error")
    return "".join(parts) + "]"


def _compact(value: Any) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, default=str)
        except (TypeError, ValueError):
            text = str(value)
    return truncate_middle(" ".join(text.split()), MAX_TOOL_RESULT_CHARS)


def truncate_middle(text: str, limit: int) -> str:
    """Keep both ends, drop the middle, and say how much went.

    Silent truncation is worse than none: an agent reading a trimmed trace has
    to know it is trimmed, or it will judge a session on evidence it was never
    shown.
    """
    if limit <= 0 or len(text) <= limit:
        return text
    notice = "\n\n… {} characters elided from the middle …\n\n"
    room = limit - len(notice.format(len(text)))
    if room <= 0:
        return notice.format(len(text)).strip()
    head = room // 2
    tail = room - head
    elided = len(text) - head - tail
    return text[:head] + notice.format(elided) + text[len(text) - tail :]
