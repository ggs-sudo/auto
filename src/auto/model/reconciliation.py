"""The reconciliation record: one takeover's audit of one effort.

Numbered run-wide like gates, so the reconciliations directory reads as the
effort's takeover history: what was examined each time someone pointed
`auto takeover` at it, and what the consulted agent concluded. The agent's
prose is recorded but never load-bearing — the verdict counts only as the
tool call that landed it.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from auto.model.common import SCHEMA_VERSION
from auto.model.intervention import ToolCall
from auto.model.session import Telemetry


class ReconciliationVerdict(StrEnum):
    """What the takeover agent concluded about the effort's recorded state."""

    CLEAN = "clean"
    """The recorded state and the target repo agree; nothing needed correcting."""


class ReconciliationRecord(BaseModel):
    """`reconciliations/<reconciliation-id>.json`.

    One per takeover consultation. Written before the agent runs — so a slow
    judgment is visible while it is being made — and rewritten once it ends.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=SCHEMA_VERSION)
    reconciliation_id: str = Field(
        description="`<sequence>-<effort>`, so the directory sorts chronologically."
    )
    sequence: int = Field(ge=1, description="Run-wide, continued across takeovers.")
    effort: str = Field(description="The effort directory name under `.scratch/`.")
    examined: list[str] = Field(
        description="One line per thing examined — gathered deterministically "
        "by the harness before the agent was consulted, never by the agent."
    )
    verdict: ReconciliationVerdict | None = Field(
        default=None,
        description="The verdict the agent landed as a tool call, or None when "
        "it landed none — in which case the takeover stopped instead of resuming.",
    )
    model: str = Field(description="The model the consultation ran on.")
    session_id: str = Field(description="The ephemeral agent's own session id.")
    started_at: datetime
    ended_at: datetime | None = None
    prose: str | None = Field(
        default=None,
        description="What the agent said. Recorded for the reader; it has no "
        "effect on any state.",
    )
    tool_calls: list[ToolCall] = Field(
        default_factory=list,
        description="Everything the consultation actually did, refusals included.",
    )
    telemetry: Telemetry = Field(default_factory=Telemetry)
