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

from auto.model.common import SCHEMA_VERSION, NodeStatus
from auto.model.intervention import ToolCall
from auto.model.session import Telemetry


class ReconciliationVerdict(StrEnum):
    """What the takeover agent concluded about the effort's recorded state."""

    CLEAN = "clean"
    """The recorded state and the target repo agree; nothing needed correcting."""

    CORRECTED = "corrected"
    """The recorded state disagreed with the repo; the record's corrections
    made it agree, and execution resumed over the corrected state."""


class Correction(BaseModel):
    """One graph-node status the takeover agent corrected, and why.

    The record's answer to "what changed": a quiet reconciliation is evidence
    of health only because a correcting one accounts for itself here.
    """

    model_config = ConfigDict(extra="forbid")

    node: str = Field(
        description="The corrected node, as `<graph>/<node-id>` — the graph "
        "is the effort's own or a subgraph beneath it."
    )
    prior_status: NodeStatus = Field(description="What the graph recorded.")
    new_status: NodeStatus = Field(description="What the correction made it.")
    evidence: str = Field(
        description="What the repo shows that contradicts the prior status, "
        "as the agent gave it in the correcting tool call."
    )


class TicketCorrection(BaseModel):
    """One lying ticket `Status:` line the takeover agent corrected, and why.

    The one write the harness ever makes to a tracker file (ADR-0010): the
    line rewritten to an open status and a note appended, so a from-scratch
    derivation cannot re-import the lie. Everything else in the ticket stays
    what sessions wrote.
    """

    model_config = ConfigDict(extra="forbid")

    node: str = Field(
        description="The node whose ticket was corrected, as `<graph>/<node-id>`."
    )
    ticket: str = Field(description="The ticket file's repo-relative path.")
    prior_status: str = Field(description="What the lying line said, verbatim.")
    new_status: str = Field(description="What the correction made it.")
    evidence: str = Field(
        description="What the repo shows that contradicts the closed-out line, "
        "as the agent gave it in the correcting tool call."
    )


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
        description="One line per thing examined — every graph in the "
        "effort's subtree, gathered deterministically by the harness before "
        "the agent was consulted, never by the agent."
    )
    verdict: ReconciliationVerdict | None = Field(
        default=None,
        description="The verdict the agent landed as a tool call, or None when "
        "it landed none — in which case the takeover stopped instead of resuming.",
    )
    corrections: list[Correction] = Field(
        default_factory=list,
        description="Every graph-node status the consultation corrected, in "
        "the order the corrections landed. Empty for a clean effort.",
    )
    ticket_corrections: list[TicketCorrection] = Field(
        default_factory=list,
        description="Every ticket `Status:` line the consultation corrected, "
        "in the order the corrections landed. Empty for a clean effort.",
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
