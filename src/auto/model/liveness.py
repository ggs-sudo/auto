"""Orchestrator liveness: the process behind a run's `running` claim."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from auto.model.common import SCHEMA_VERSION


class Liveness(BaseModel):
    """`liveness.json`. The orchestrator process's pid and heartbeat.

    Written the moment the loop starts executing and refreshed while it runs,
    so a manifest claiming `running` can be checked against a recorded pid
    instead of being believed forever. Never removed: a terminal manifest
    status outranks it, and the last heartbeat is part of the run's history.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=SCHEMA_VERSION)
    pid: int = Field(description="The orchestrator process's pid.")
    started_at: datetime = Field(description="When the loop began executing.")
    heartbeat_at: datetime = Field(
        description="The last refresh. History, not the liveness check itself: "
        "whether the orchestrator is alive is read off the process table via "
        "the pid, never guessed from this timestamp's age."
    )
