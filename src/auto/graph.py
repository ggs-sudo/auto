"""Deriving execution graphs from tracker files, and holding them for a run.

Membership, edges, types and first-seen completion all come from the ticket
files under `.scratch/<effort>/issues/`, re-read every tick — that is what
lets tickets written mid-run join the graph without anything restarting. The
harness-only fields (`NodeState`: status, session id, nudge bookkeeping, the
spawned graph, a task's classification) have no life in those files, so they
are merged back in by ticket id, and once a node is held by the harness its
status is authoritative: a `Status:` line matters only the first time a ticket
is seen, when one already resolved is imported as done.

Readiness is derived, never stored. The persisted `graph.json` beside the
tickets is a snapshot for readers — the one documented exception to sessions
never reading harness state, though no stock skill ever looks at it.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Mapping
from pathlib import Path

from auto.errors import AutoError
from auto.model import (
    Graph,
    GraphNode,
    NodeStatus,
    TaskResolutionMode,
    TicketType,
)
from auto.owed import CLOSED_OUT_STATUS, EFFORT_ROOT
from auto.run import require_owner_thread, write_atomically

GRAPH_FILENAME = "graph.json"
ISSUES_DIR = "issues"

TYPE_LINE = re.compile(r"^[ \t]*\**type\**[ \t]*:\**[ \t]*([a-z-]+)", re.I | re.M)
"""The tracker's `Type:` line, bare or bolded, as skills actually write it."""

BLOCKED_BY_LINE = re.compile(
    r"^[ \t]*\**blocked[ \t]+by\**[ \t]*:\**[ \t]*(.*)$", re.I | re.M
)

_NO_BLOCKERS = re.compile(r"^\s*none\b", re.I)
"""`Blocked by: None (can start immediately)` — the shape the templates write."""

_TICKET_TYPES = {member.value: member for member in TicketType}


class GraphError(AutoError):
    """A graph could not be emitted as asked. Reported, never fatal."""


def parse_ticket_type(text: str) -> TicketType:
    """The ticket's type, from its `Type:` line.

    No line at all is an implementation ticket — a spec's tickets are typed by
    having no wayfinder type. An unrecognised value is treated the same way:
    every ticket is work, and implement is the type that means only that.
    """
    match = TYPE_LINE.search(text)
    if match is None:
        return TicketType.IMPLEMENT
    return _TICKET_TYPES.get(match.group(1).lower(), TicketType.IMPLEMENT)


def ticket_stem(reference: str) -> str:
    """A loose ticket reference — `01`, `01-index.md`, a path, backticked —
    reduced to the bare stem that names a node."""
    return reference.strip().strip("`*").split("/")[-1].removesuffix(".md")


def parse_blockers(text: str) -> list[str]:
    """What the ticket's `Blocked by:` line names, normalised to ticket stems.

    What a reference *resolves to* is the derivation's job, not the parser's.
    """
    match = BLOCKED_BY_LINE.search(text)
    if match is None or _NO_BLOCKERS.match(match.group(1)):
        return []
    return [stem for token in match.group(1).split(",") if (stem := ticket_stem(token))]


def _resolve(ref: str, stems: list[str]) -> str:
    """The stem a blocker reference names, or the reference itself when none.

    An unresolved reference is kept rather than dropped: a ticket that says it
    is blocked by something the graph cannot find is blocked, not free — the
    named ticket may simply not have been written yet.
    """
    if ref in stems:
        return ref
    for stem in stems:
        if stem.startswith(f"{ref}-"):
            return stem
    if ref.isdigit():
        for stem in stems:
            lead = re.match(r"\d+", stem)
            if lead is not None and int(lead.group()) == int(ref):
                return stem
    return ref


def graph_path(repo: Path, graph_id: str) -> Path:
    """Where the graph lives: beside the tickets it is derived from."""
    return repo / EFFORT_ROOT / graph_id / GRAPH_FILENAME


def _issues_dir(repo: Path, graph_id: str) -> Path:
    return repo / EFFORT_ROOT / graph_id / ISSUES_DIR


def derive(
    repo: Path,
    graph_id: str,
    *,
    spawned_by: str,
    previous: Graph | None = None,
) -> Graph:
    """One graph, read fresh from its effort directory.

    `previous` is the harness's current hold on the graph; its node objects
    are reused in place rather than copied, so a reference taken at dispatch
    stays good across every later tick — several nodes are in flight at once,
    and each writes its terminal status through that reference. A ticket the
    harness does not hold is new, and only then does the ticket's own
    `Status:` line speak: one already closed out is imported as done.
    """
    tickets: list[tuple[str, str]] = []
    for path in sorted(_issues_dir(repo, graph_id).glob("*.md")):
        try:
            tickets.append((path.stem, path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError):
            continue

    held = {node.node_id: node for node in previous.nodes} if previous else {}
    stems = [stem for stem, _ in tickets]
    nodes = []
    for stem, text in tickets:
        ticket_type = parse_ticket_type(text)
        blocked_by = [_resolve(ref, stems) for ref in parse_blockers(text)]
        prior = held.get(stem)
        if prior is not None:
            # The held object itself, its derived fields refreshed: the
            # harness-only fields need no copying, and every reference to the
            # node — a dispatch in flight, above all — stays valid.
            prior.ticket_type = ticket_type
            prior.blocked_by = blocked_by
            nodes.append(prior)
            continue
        node = GraphNode(
            node_id=stem,
            ticket=f"{EFFORT_ROOT}/{graph_id}/{ISSUES_DIR}/{stem}.md",
            ticket_type=ticket_type,
            blocked_by=blocked_by,
        )
        if CLOSED_OUT_STATUS.search(text) is not None:
            node.status = NodeStatus.DONE
        nodes.append(node)
    return Graph(graph_id=graph_id, spawned_by=spawned_by, nodes=nodes)


def ready(graph: Graph) -> list[GraphNode]:
    """The nodes a session could be dispatched for right now, in ticket order.

    Derived, never stored: pending, dispatchable (a task classified `user`, or
    not classified at all, has no entry skill), and with every blocker done —
    including blockers that resolve to no ticket, which are simply never done.

    A blocker's own status is all that is consulted today. The glossary asks
    for more — a dependency is satisfied only when every graph beneath the
    blocker is terminal too — and that subtree check arrives with subgraph
    tracking (#14), not here.
    """
    done = {node.node_id for node in graph.nodes if node.status is NodeStatus.DONE}
    return [
        node
        for node in graph.nodes
        if node.status is NodeStatus.PENDING
        and node.entry is not None
        and all(blocker in done for blocker in node.blocked_by)
    ]


class GraphStore:
    """Every graph the run holds, and the authority over their harness fields.

    Written only on the loop thread, like the run directory: the guard makes a
    violation loud rather than a corrupted file.
    """

    def __init__(self, repo: Path) -> None:
        self._repo = repo
        self._graphs: dict[str, Graph] = {}
        self._owner_thread = threading.get_ident()

    @property
    def graphs(self) -> list[Graph]:
        return list(self._graphs.values())

    def emit(
        self,
        graph_id: str,
        *,
        spawned_by: str,
        task_modes: Mapping[str, TaskResolutionMode] | None = None,
    ) -> Graph:
        """Derive an effort's graph for the first time and take hold of it.

        Every `task` ticket must be classified here and now — the agent is
        already reading the tickets to emit the graph, so classification costs
        no extra session, and a task without a mode could never dispatch.
        """
        if graph_id in self._graphs:
            raise GraphError(
                f"a graph for `{graph_id}` was already emitted; it is "
                "re-derived from the tickets on every tick, so new tickets "
                "join it on their own"
            )
        if not any(_issues_dir(self._repo, graph_id).glob("*.md")):
            raise GraphError(
                f"no tickets at `{EFFORT_ROOT}/{graph_id}/{ISSUES_DIR}/`: a "
                "graph is derived from ticket files, and there are none to "
                "derive it from"
            )
        graph = derive(self._repo, graph_id, spawned_by=spawned_by)
        self._classify(graph, task_modes or {})
        self._graphs[graph_id] = graph
        self.persist(graph)
        return graph

    def _classify(
        self, graph: Graph, task_modes: Mapping[str, TaskResolutionMode]
    ) -> None:
        for ref, mode in task_modes.items():
            node = graph.node(ticket_stem(ref))
            if node is None:
                raise GraphError(
                    f"`{ref}` names no ticket in `{graph.graph_id}`"
                )
            if node.ticket_type is not TicketType.TASK:
                raise GraphError(
                    f"`{ref}` is not a `task` ticket ({node.ticket_type.value}); "
                    "only tasks take a resolution mode"
                )
            node.task_mode = mode
        unclassified = [
            node.node_id
            for node in graph.nodes
            if node.ticket_type is TicketType.TASK and node.task_mode is None
        ]
        if unclassified:
            raise GraphError(
                "every `task` ticket needs a resolution mode (agent, user or "
                "undefined); missing: " + ", ".join(unclassified)
            )

    def tick(self) -> None:
        """Re-derive every held graph from its tickets, and persist each.

        This is what lets a ticket written mid-run join the graph without
        anything restarting: membership is the directory's, fresh each time.
        """
        for graph_id, held in list(self._graphs.items()):
            fresh = derive(
                self._repo, graph_id, spawned_by=held.spawned_by, previous=held
            )
            self._graphs[graph_id] = fresh
            self.persist(fresh)

    def persist(self, graph: Graph) -> None:
        require_owner_thread(self._owner_thread, f"writing {GRAPH_FILENAME}")
        write_atomically(
            graph_path(self._repo, graph.graph_id),
            graph.model_dump_json(indent=2) + "\n",
        )

    def claim_next_ready(self) -> tuple[Graph, GraphNode] | None:
        """The first dispatchable node by ticket number, claimed on the way out.

        Claiming — marking the node in progress here, before any session
        exists — is what lets a dispatcher filling several slots at once be
        handed distinct nodes rather than the same first one every call.
        """
        for graph in self._graphs.values():
            nodes = ready(graph)
            if nodes:
                nodes[0].status = NodeStatus.IN_PROGRESS
                return graph, nodes[0]
        return None

    def persist_current(self, graph_id: str) -> None:
        """Persist the store's current hold on one graph.

        A dispatch persists through the id rather than a `Graph` it captured,
        because the graph object is replaced on every tick: only the node
        objects inside it are stable, and a snapshot taken at dispatch would
        write stale membership back over what later ticks discovered.
        """
        self.persist(self._graphs[graph_id])

    def all_done(self) -> bool:
        """Whether every node in every held graph is complete."""
        return all(graph.done() for graph in self._graphs.values())
