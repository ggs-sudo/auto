"""The run manifest: a run's identity, inputs, and overall status."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from auto.model.common import (
    ROOT_NODE_ID,
    SCHEMA_VERSION,
    NodeState,
    NodeType,
    Route,
    RunStatus,
)
from auto.model.config import ResolvedConfig


class RootNode(NodeState):
    """The run's first session, which predates every graph.

    Graphs live beside tickets in the target repo's effort directories, and at
    launch no effort directory exists — so the root node lives here instead,
    carrying the pasted prompt where every other node carries a ticket. Its
    `graph` is the run's first: the effort directory its session charted.

    On the takeover route the root has no entry skill (`type` is None) and
    carries the effort path where an ordinary root carries the pasted prompt:
    its session is the reconciliation itself, done when the graph is adopted.
    """

    node_id: str = Field(default=ROOT_NODE_ID)
    type: NodeType | None = Field(
        description="The node's type is its entry skill; None on the takeover "
        "route, whose root session is the reconciliation rather than a skill."
    )
    prompt: str = Field(
        description="The pasted prompt, verbatim — or, on the takeover route, "
        "the effort path."
    )


class Manifest(BaseModel):
    """`run.json`. Written only by the orchestrator, on the loop thread."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=SCHEMA_VERSION)
    run_id: str = Field(description="`YYYYMMDD-HHMMSS-<slug>`.")
    route: Route
    prompt: str = Field(description="The pasted prompt, verbatim.")
    target_repo: str = Field(description="Absolute path to the target repo.")
    worktree: str | None = Field(
        default=None,
        description="The git worktree root the target repo resolved to. Records "
        "the state the run was pointed at, never anything the harness did: it "
        "creates no branches and no worktrees.",
    )
    branch: str | None = Field(
        default=None, description="Checked-out branch, or None when detached."
    )
    head: str | None = Field(default=None, description="HEAD commit sha.")
    dirty: bool = Field(
        default=False,
        description="Whether the working tree had uncommitted changes at launch.",
    )
    config: ResolvedConfig
    created_at: datetime
    ended_at: datetime | None = None
    status: RunStatus = RunStatus.RUNNING
    phase: str | None = Field(
        default=None, description="Route-specific progress, orthogonal to status."
    )
    root_node: RootNode
    driven_spend_usd: float = Field(
        default=0.0, ge=0, description="Spend across driven sessions."
    )
    orchestrator_spend_usd: float = Field(
        default=0.0,
        ge=0,
        description="Spend across orchestrator-agent invocations, counted apart "
        "so the harness's own overhead is measurable.",
    )
