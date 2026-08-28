"""The session record: one file per `claude -p` conversation."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from auto.model.common import SCHEMA_VERSION, NodeType, SessionStatus


class Telemetry(BaseModel):
    """Read off the session's `result` event — the same event that means stale."""

    model_config = ConfigDict(extra="forbid")

    cost_usd: float | None = None
    num_turns: int | None = None
    duration_ms: int | None = None
    stop_reason: str | None = None
    terminal_reason: str | None = None
    is_error: bool | None = None
    usage: dict[str, Any] | None = None
    model_usage: dict[str, Any] | None = None
    permission_denials: list[Any] | None = None


class SessionRecord(BaseModel):
    """`sessions/<session-id>.json`.

    Written only by the orchestrator, transcribed from what it observed. The
    session itself never sees this file, or any other harness state.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=SCHEMA_VERSION)
    session_id: str
    node: str = Field(description="Node id this session resolves.")
    role: NodeType = Field(description="Named by entry skill.")
    ticket: str | None = Field(
        default=None, description="None for the root node, which has no ticket."
    )
    status: SessionStatus = SessionStatus.RUNNING
    started_at: datetime
    ended_at: datetime | None = None
    summary: str | None = Field(
        default=None,
        description="What this session produced. The orchestrator's own words "
        "once it completes the node; until then the session's latest final "
        "text, or why it ended when there was none.",
    )
    highlights: list[str] = Field(
        default_factory=list,
        description="Scannable headline facts, accumulated during the node's life.",
    )
    captured_transcript: str = Field(
        description="Path to the captured stream-json, relative to the run directory."
    )
    native_transcript: str | None = Field(
        default=None,
        description="Claude Code's own transcript under ~/.claude/projects/, if known.",
    )
    telemetry: Telemetry = Field(default_factory=Telemetry)
