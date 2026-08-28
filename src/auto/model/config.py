"""Resolved configuration, as copied into a run manifest.

Defaults live here; `auto.config` layers the user config file and the
per-invocation overrides on top and hands the result to the run.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_CONCURRENCY = 4
DEFAULT_SESSION_BUDGET_USD = 10.0
DEFAULT_RUN_BUDGET_USD = 100.0
DEFAULT_ORCHESTRATOR_MODEL = "claude-opus-5"


class ResolvedConfig(BaseModel):
    """What a run actually ran under — recorded so it need not be
    reconstructed from shell history."""

    model_config = ConfigDict(extra="forbid")

    concurrency: int = Field(
        default=DEFAULT_CONCURRENCY,
        ge=1,
        description="How many driven sessions may run at once. Orchestrator "
        "interventions are not counted against this.",
    )
    session_budget_usd: float = Field(
        default=DEFAULT_SESSION_BUDGET_USD,
        gt=0,
        description="Spend ceiling passed to each driven session.",
    )
    run_budget_usd: float = Field(
        default=DEFAULT_RUN_BUDGET_USD,
        gt=0,
        description="Spend ceiling for the whole run, driven and orchestrator "
        "spend together.",
    )
    orchestrator_model: str = Field(
        default=DEFAULT_ORCHESTRATOR_MODEL,
        min_length=1,
        description="Pinned rather than inherited, so a run's behaviour does "
        "not change with the user's editor config.",
    )
