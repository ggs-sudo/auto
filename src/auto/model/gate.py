"""Gates: where a run waits on a human, and what they answer with.

A gate is a pause nobody has answered yet, persisted under the run's `gates/`
directory so the website can surface it. Its response is a **sibling file**
beside it, and the write responsibilities are absolute: the orchestrator
writes gates and never responses, the website writes responses and never
gates. That split is what lets "who answered what" never be a question.

All four kinds are schema from day one — the website builds against these
models, not against what the loop happens to wire — even though only
prototype review raises gates today.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from auto.model.common import SCHEMA_VERSION


class GateKind(StrEnum):
    """The four places a run needs a human. Kinds of gate, not mechanisms."""

    PROTOTYPE_REVIEW = "prototype-review"
    TASK_COMPLETION = "task-completion"
    ESCALATED_QUESTION = "escalated-question"
    USER_PING = "user-ping"


class Gate(BaseModel):
    """`gates/<gate-id>.json`. Written only by the orchestrator.

    Gates are numbered run-wide and never reopened: a revision raises a fresh,
    separately numbered gate on the next stale point, so the gate sequence
    *is* the review history — round one's question survives to be read during
    round three.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=SCHEMA_VERSION)
    gate_id: str = Field(
        description="`<sequence>-<node>`, so the directory sorts as history."
    )
    sequence: int = Field(ge=1, description="Run-wide, in the order gates rose.")
    kind: GateKind
    node: str = Field(description="The run-wide id of the node waiting here.")
    question: str = Field(description="What the human is being asked.")
    artifact: str | None = Field(
        default=None,
        description="Pointer to what they should look at — a path or URL. "
        "A prototype review always carries one; other kinds may not.",
    )
    raised_at: datetime
    answered_at: datetime | None = Field(
        default=None,
        description="When the orchestrator took the response up to deliver it "
        "into the waiting session. None while the gate is open.",
    )


class GateDecision(StrEnum):
    """The structured half of a response. Everything else is free text."""

    APPROVE = "approve"
    REVISE = "revise"


class GateResponse(BaseModel):
    """`gates/<gate-id>.response.json` — the gate's sibling.

    Written only by the website (or, until it exists, a human hand). The
    decision is the one thing the harness reads; the text is carried to the
    waiting session verbatim and never parsed — "combine A and C" means
    something to the session and nothing to the harness.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=SCHEMA_VERSION)
    decision: GateDecision
    text: str = Field(
        default="",
        description="The human's own words, delivered verbatim. May be empty.",
    )
