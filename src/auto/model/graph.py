"""The execution graph: one per effort directory, derived from its tickets.

The persisted file (`.scratch/<effort>/graph.json`, gitignored) is a snapshot,
not a source of truth. Membership, edges and types are re-derived from the
ticket files every tick; only the harness-only fields on `NodeState` — status,
session id, nudge bookkeeping, the spawned graph — survive a re-derivation,
merged back in by ticket id. Readiness is never here at all: it is derived
fresh from statuses and edges each time it is needed.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from auto.model.common import SCHEMA_VERSION, NodeState, NodeStatus, NodeType


class TicketType(StrEnum):
    """What a ticket's `Type:` line says it is.

    The wayfinder vocabulary plus `implement` for the tickets that have no
    `Type:` line at all — a spec's implementation tickets, which are typed by
    their absence of a type.
    """

    RESEARCH = "research"
    PROTOTYPE = "prototype"
    GRILLING = "grilling"
    TASK = "task"
    IMPLEMENT = "implement"


class TaskResolutionMode(StrEnum):
    """Which of three ways a `task` ticket gets resolved.

    Decided by the orchestrator agent when it emits the graph, never by the
    loop and never at dispatch time.
    """

    AGENT = "agent"
    """Work a session can do alone."""

    USER = "user"
    """Work only a human can do. Raises a task-completion gate once gates
    exist; until then it is simply never dispatchable."""

    UNDEFINED = "undefined"
    """Not really work at all but a milestone the map surfaced before it could
    be specified — resolved by a grilling session in its place."""


_ENTRY: dict[TicketType, NodeType] = {
    TicketType.RESEARCH: NodeType.RESEARCH,
    TicketType.PROTOTYPE: NodeType.PROTOTYPE,
    TicketType.GRILLING: NodeType.GRILL_WITH_DOCS,
    TicketType.IMPLEMENT: NodeType.IMPLEMENT,
}

_TASK_ENTRY: dict[TaskResolutionMode, NodeType] = {
    TaskResolutionMode.AGENT: NodeType.IMPLEMENT,
    TaskResolutionMode.UNDEFINED: NodeType.GRILL_WITH_DOCS,
}


class GraphNode(NodeState):
    """One ticket, as the graph holds it."""

    node_id: str = Field(
        description="The ticket file's stem — what `Blocked by:` lines name."
    )
    ticket: str = Field(
        description="The ticket file's path, relative to the target repo."
    )
    ticket_type: TicketType = Field(
        description="From the ticket's `Type:` line; `implement` when it has none."
    )
    task_mode: TaskResolutionMode | None = Field(
        default=None,
        description="How a `task` ticket resolves, classified at emit time. "
        "None for every other type, and for a task nothing has classified yet.",
    )
    blocked_by: list[str] = Field(
        default_factory=list,
        description="Node ids this ticket's `Blocked by:` line names. A name "
        "matching no ticket stays here unresolved and keeps the node blocked.",
    )

    @property
    def entry(self) -> NodeType | None:
        """The skill a session resolving this node enters through.

        None means no session can resolve it: a task classified `user`, or one
        not yet classified at all. An undefined task's entry is grilling — the
        grilling ticket emitted in its place *is* this node, re-typed.
        """
        if self.ticket_type is not TicketType.TASK:
            return _ENTRY[self.ticket_type]
        if self.task_mode is None:
            return None
        return _TASK_ENTRY.get(self.task_mode)


class Graph(BaseModel):
    """`.scratch/<effort>/graph.json` — beside the tickets it came from.

    The graph id *is* the effort directory name, so nothing has to invent an
    identifier. Node order is ticket-file order, which the tracker's own
    "first by number wins" convention makes the dispatch order among peers.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=SCHEMA_VERSION)
    graph_id: str = Field(description="The effort directory name under `.scratch/`.")
    spawned_by: str = Field(
        description="The run-wide id of the node whose session wrote these tickets."
    )
    nodes: list[GraphNode] = Field(default_factory=list)

    def node(self, node_id: str) -> GraphNode | None:
        return next((n for n in self.nodes if n.node_id == node_id), None)

    def done(self) -> bool:
        """Whether every node in this graph is complete."""
        return all(node.status is NodeStatus.DONE for node in self.nodes)
