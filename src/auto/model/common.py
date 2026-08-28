"""Vocabulary shared by every meta file.

The names here are the glossary's names (see `CONTEXT.md`); nothing in the
harness invents a synonym for them.
"""

from __future__ import annotations

from enum import StrEnum

SCHEMA_VERSION = 1
"""Bumped when a persisted meta file changes shape incompatibly."""

ROOT_NODE_ID = "root"
"""The run's first session has no ticket, so it gets a reserved node id."""


class Route(StrEnum):
    """Which skill a run enters through. Chosen explicitly, never inferred."""

    GRILL = "grill"
    WAYFINDER = "wayfinder"

    @property
    def entry_skill(self) -> NodeType:
        """The node type — and so the skill — the route's root node runs."""
        return _ROUTE_ENTRY_SKILLS[self]


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
    DONE = "done"
    FAILED = "failed"


class SessionStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
