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

from pydantic import ValidationError

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


def load_persisted(repo: Path, graph_id: str) -> Graph:
    """The persisted snapshot beside an effort's tickets, as a run left it.

    The takeover path's way in: only a graph a run already emitted can be
    continued, so nothing readable here is an error, not an empty graph.
    """
    path = graph_path(repo, graph_id)
    try:
        return Graph.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise GraphError(
            f"no readable graph at `{path}`: only a graph a run already "
            f"emitted can be continued, and none is there ({exc})"
        ) from exc


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


def ready(graph: Graph, graphs: Mapping[str, Graph]) -> list[GraphNode]:
    """The nodes a session could be dispatched for right now, in ticket order.

    Derived, never stored: pending, dispatchable (a task classified `user`, or
    not classified at all, has no entry skill), and with every blocker
    satisfied. A blocker satisfies a dependency only when it is complete *and*
    every graph beneath it is terminal, so whatever depended on a spawning
    node also waits for the subtree that node spawned. A blocker that resolves
    to no ticket is simply never satisfied.

    Dependency edges never cross graph boundaries: a blocker is looked up in
    this graph alone. `graphs` — every graph the run holds, keyed by id — is
    consulted only downward, for the subtrees beneath blockers.
    """
    return [
        node
        for node in graph.nodes
        if node.status is NodeStatus.PENDING
        and node.entry is not None
        and all(_satisfied(graph.node(ref), graphs) for ref in node.blocked_by)
    ]


def _satisfied(blocker: GraphNode | None, graphs: Mapping[str, Graph]) -> bool:
    """Whether this blocker satisfies a dependency: complete, subtree terminal.

    None — a reference that resolved to no ticket — never satisfies: the named
    ticket may simply not have been written yet.
    """
    if blocker is None:
        return False
    return blocker.status is NodeStatus.DONE and _subtree_terminal(blocker, graphs)


def _subtree_terminal(node: GraphNode, graphs: Mapping[str, Graph]) -> bool:
    """Whether every graph beneath this node is terminal, all the way down.

    Terminal means *complete* (ADR-0006): every node in the spawned graph must
    itself satisfy — done, its own subtree terminal in turn. A failed node
    beneath a blocker keeps the dependency unsatisfied for good, exactly as a
    failed blocker does in its own graph — work that depends on a subtree
    never runs over a subtree that went wrong. A spawned graph the run does
    not hold cannot be vouched for, so it blocks the same way.

    The recursion cannot cycle: a graph is emitted once, by one node, so the
    spawned-graph relation is a tree by construction.
    """
    if node.graph is None:
        return True
    below = graphs.get(node.graph)
    if below is None:
        return False
    return all(_satisfied(n, graphs) for n in below.nodes)


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

    def adopt(self, graph_id: str) -> Graph:
        """Take hold of a graph an earlier run already emitted, statuses and all.

        The takeover path's counterpart to `emit`: the persisted snapshot is
        read for the harness-only fields, then membership is re-derived from
        the tickets exactly as a tick would — so a ticket written since the
        run stopped joins on its own. A node the snapshot holds in flight is
        returned to pending with its session bookkeeping cleared: a continued
        run holds no live sessions, so nothing can actually be in progress.
        That is revival arithmetic, not reconciliation — no judgment about
        whether the recorded state is *true* happens here.

        Spawned subgraphs are adopted with it, recursively, so completion is
        judged over the same subtree the original run held.
        """
        if graph_id in self._graphs:
            raise GraphError(f"the graph `{graph_id}` is already held")
        persisted = load_persisted(self._repo, graph_id)
        for node in persisted.nodes:
            if node.status in (NodeStatus.IN_PROGRESS, NodeStatus.REVIEW_PENDING):
                node.status = NodeStatus.PENDING
                node.session_id = None
                node.gate = None
                node.nudge_count = 0
                node.missing_artifacts = []
        graph = derive(
            self._repo, graph_id, spawned_by=persisted.spawned_by, previous=persisted
        )
        self._graphs[graph_id] = graph
        self.persist(graph)
        for node in graph.nodes:
            if node.graph is not None and node.graph not in self._graphs:
                self.adopt(node.graph)
        return graph

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
            nodes = ready(graph, self._graphs)
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
