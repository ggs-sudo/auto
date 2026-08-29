"""The intervention record: one ephemeral orchestrator-agent invocation.

Every judgment the harness makes unattended leaves one of these behind, so a
surprising decision can be explained after the fact rather than reconstructed
from a transcript. The agent's prose is recorded but never load-bearing: only
its tool calls had any effect.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from auto.model.common import SCHEMA_VERSION
from auto.model.session import Telemetry


class InterventionTrigger(StrEnum):
    """What caused a fresh agent to be invoked.

    A gate response is not a second delivery path: it is the same primitive as
    a stale point, under its own trigger, with the response rendered into the
    invocation.
    """

    STALE = "stale"
    GATE_RESPONSE = "gate-response"


class ToolCall(BaseModel):
    """One harness-tool call, and whether it took effect.

    A refused call is recorded rather than dropped: what the agent tried to do
    and was not allowed to is the interesting half of a surprising decision.
    """

    model_config = ConfigDict(extra="forbid")

    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    refused: str | None = Field(
        default=None,
        description="Why the tool layer refused it, or None when it took effect.",
    )

    @property
    def accepted(self) -> bool:
        return self.refused is None


class InterventionRecord(BaseModel):
    """`interventions/<intervention-id>.json`.

    One per invocation, never one per run: the agent is ephemeral, so this file
    is the only place its reasoning survives.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=SCHEMA_VERSION)
    intervention_id: str = Field(
        description="`<sequence>-<node>`, so the directory sorts chronologically."
    )
    node: str = Field(description="The single node this invocation could act on.")
    trigger: InterventionTrigger
    model: str = Field(
        description="Pinned in configuration, recorded here, never inherited "
        "from the user's editor config."
    )
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
        description="Everything the invocation actually did. An empty list is "
        "valid and means the node is still working.",
    )
    telemetry: Telemetry = Field(default_factory=Telemetry)
