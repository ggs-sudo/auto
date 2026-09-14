"""Vocabulary shared by every meta file.

The names here are the glossary's names (see `CONTEXT.md`); nothing in the
harness invents a synonym for them.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1
"""Bumped when a persisted meta file changes shape incompatibly."""

ROOT_NODE_ID = "root"
"""The run's first session has no ticket, so it gets a reserved node id."""


class Route(StrEnum):
    """Which skill a run enters through. Chosen explicitly, never inferred."""

    GRILL = "grill"
    WAYFINDER = "wayfinder"
    TAKEOVER = "takeover"
    """The implement-only entry: `auto takeover` on an effort that has tickets
    but no run. Never chosen through `auto run` — the run is minted by the
    takeover itself."""

    @property
    def entry_skill(self) -> NodeType | None:
        """The node type — and so the skill — the route's root node runs.

        None for the takeover route: its root session is the reconciliation,
        which is the harness's own consultation and not a skill.
        """
        return _ROUTE_ENTRY_SKILLS.get(self)


class NodeType(StrEnum):
    """A node's type *is* its entry skill."""

    GRILL_WITH_DOCS = "grill-with-docs"
    WAYFINDER = "wayfinder"
    RESEARCH = "research"
    PROTOTYPE = "prototype"
    IMPLEMENT = "implement"

    @property
    def skill_invocation(self) -> str:
        """The slash command that enters this node's skill."""
        return f"/{self.value}"

    @property
    def monitored(self) -> bool:
        """Whether the orchestrator agent reads this node's trace in full.

        Grilling and wayfinder sessions spawn a graph, so their whole
        conversation is the material a judgment is made from. Every other node
        is autonomous: only what it did since the previous stale point matters.
        """
        return self in _MONITORED_NODE_TYPES


_MONITORED_NODE_TYPES = frozenset(
    {NodeType.GRILL_WITH_DOCS, NodeType.WAYFINDER}
)

_ROUTE_ENTRY_SKILLS: dict[Route, NodeType] = {
    Route.GRILL: NodeType.GRILL_WITH_DOCS,
    Route.WAYFINDER: NodeType.WAYFINDER,
}


class RunStatus(StrEnum):
    """A run's coarse state.

    `GATED` means strictly "nothing can proceed until a human acts"; a run only
    partially blocked stays `RUNNING`.
    """

    RUNNING = "running"
    GATED = "gated"
    DONE = "done"
    FAILED = "failed"
    ABORTED = "aborted"


class NodeStatus(StrEnum):
    """A node's status is its session's status.

    A node reaching `DONE` says nothing about a subgraph it may have spawned.
    """

    PENDING = "pending"
    IN_PROGRESS = "in-progress"
    REVIEW_PENDING = "review-pending"
    """Waiting at a gate, session alive and idle. Not terminal and not done:
    dependents stay blocked exactly as they would behind unfinished work."""

    DONE = "done"
    FAILED = "failed"


class SessionStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class NodeState(BaseModel):
    """What the harness holds about one node, whatever kind of node it is.

    These are the harness-only fields: everything else about a graph node is
    re-derived from its ticket every tick, but these have no life in the
    tracker files, so they are merged back in by ticket id — and for the root
    node, kept on the manifest.
    """

    model_config = ConfigDict(extra="forbid")

    status: NodeStatus = Field(default=NodeStatus.PENDING)
    session_id: str | None = Field(
        default=None, description="Join key to the session record."
    )
    nudge_count: int = Field(
        default=0,
        ge=0,
        description="Consecutive nudge points — stale points at which a "
        "completion was refused — with no shrink in the owed set. Any "
        "shrinking starts it again.",
    )
    missing_artifacts: list[str] = Field(
        default_factory=list,
        description="Owed artifacts the target repo cannot back up, as of the "
        "last stale point. What the nudge count is counting.",
    )
    graph: str | None = Field(
        default=None,
        description="The graph this node's session spawned — the effort "
        "directory name — or None while it has spawned none.",
    )
    gate: str | None = Field(
        default=None,
        description="The gate this node is waiting at — the gate id under the "
        "run's `gates/` directory — or None while no gate is open on it.",
    )

    def reset_to_pending(self) -> None:
        """Back to pending with the session bookkeeping cleared — what both
        revival arithmetic and a takeover correction mean by a reset."""
        self.status = NodeStatus.PENDING
        self.session_id = None
        self.gate = None
        self.nudge_count = 0
        self.missing_artifacts = []
