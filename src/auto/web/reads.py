"""Reading run state for the website: summaries, details, transcript tails.

Everything here returns JSON-ready dicts built through the same Pydantic
models the harness writes with, so the API cannot drift from the schemas. And
everything tolerates a directory in motion: the orchestrator is writing while
we read, so an unreadable file is skipped or served as absent rather than
crashing a request — atomic writes guarantee old-or-new, never half.

Graphs are the one piece that lives outside the state directory: beside their
tickets in the target repo, reachable from the manifest. The run's graph tree
is walked from the manifest's root node through each node's `graph` link — the
only inter-graph relation there is — so an unrelated effort directory in the
same repo never leaks into a run. A target repo that has since been deleted
just means no graphs; the run itself stays viewable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from auto.errors import UsageError
from auto.graph import GRAPH_FILENAME
from auto.model import Graph, Manifest, NodeStatus
from auto.owed import EFFORT_ROOT
from auto.run import RunDirectory, list_runs, load_run
from auto.session.events import StreamEvent


def load_run_graphs(manifest: Manifest) -> list[Graph]:
    """The run's graphs, walked root-first from the manifest's root node."""
    repo = Path(manifest.target_repo)
    graphs: list[Graph] = []
    pending = [manifest.root_node.graph] if manifest.root_node.graph else []
    seen = set()
    while pending:
        graph_id = pending.pop(0)
        if graph_id in seen:
            continue
        seen.add(graph_id)
        graph = _read_graph(repo / EFFORT_ROOT / graph_id / GRAPH_FILENAME)
        if graph is None:
            continue
        graphs.append(graph)
        pending.extend(
            node.graph for node in graph.nodes if node.graph is not None
        )
    return graphs


def _read_graph(path: Path) -> Graph | None:
    try:
        return Graph.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        return None


def _gates_with_responses(run: RunDirectory) -> list[dict[str, Any]]:
    """Every gate, oldest first, each carrying its response or None.

    A gate with a response the schema cannot read is served as unanswered —
    the same stance the orchestrator's own poll takes (ADR-0007): not yet an
    answer.
    """
    out = []
    for gate in sorted(run.gate_records(), key=lambda gate: gate.sequence):
        response = run.read_gate_response(gate.gate_id)
        entry = gate.model_dump(mode="json")
        entry["response"] = (
            response.model_dump(mode="json") if response is not None else None
        )
        out.append(entry)
    return out


def _node_counts(manifest: Manifest, graphs: list[Graph]) -> dict[str, int]:
    """Every node in the run — the root node included — bucketed by status."""
    statuses = [manifest.root_node.status] + [
        node.status for graph in graphs for node in graph.nodes
    ]
    return {
        "total": len(statuses),
        "done": statuses.count(NodeStatus.DONE),
        "running": statuses.count(NodeStatus.IN_PROGRESS),
        "review_pending": statuses.count(NodeStatus.REVIEW_PENDING),
        "pending": statuses.count(NodeStatus.PENDING),
        "failed": statuses.count(NodeStatus.FAILED),
    }


def _open_gates(gates: list[dict[str, Any]]) -> int:
    return sum(
        1
        for gate in gates
        if gate["response"] is None and gate["answered_at"] is None
    )


def run_summaries(state_dir: Path) -> list[dict[str, Any]]:
    """What the runs rail shows: one row per readable run, newest first."""
    out = []
    for manifest in list_runs(state_dir):
        try:
            run = load_run(state_dir, manifest.run_id)
            gates = _gates_with_responses(run)
        except UsageError:
            # The run vanished between the listing and this read; the rail
            # exists to show what is there, not to crash on what is not.
            continue
        graphs = load_run_graphs(manifest)
        out.append(
            {
                "run_id": manifest.run_id,
                "status": manifest.status.value,
                "route": manifest.route.value,
                "prompt": manifest.prompt,
                "target_repo": manifest.target_repo,
                "created_at": manifest.created_at.isoformat(),
                "ended_at": (
                    manifest.ended_at.isoformat()
                    if manifest.ended_at is not None
                    else None
                ),
                "spend_usd": manifest.driven_spend_usd
                + manifest.orchestrator_spend_usd,
                "nodes": _node_counts(manifest, graphs),
                "open_gates": _open_gates(gates),
            }
        )
    return out


def run_detail(state_dir: Path, run_id: str) -> dict[str, Any]:
    """Everything one run's page needs, in one read.

    Raises `UsageError` when the run does not exist; the caller turns that
    into a 404.
    """
    run = load_run(state_dir, run_id)
    manifest = run.read_manifest()
    graphs = load_run_graphs(manifest)
    gates = _gates_with_responses(run)
    return {
        "manifest": manifest.model_dump(mode="json"),
        "graphs": [graph.model_dump(mode="json") for graph in graphs],
        "sessions": [
            record.model_dump(mode="json")
            for record in sorted(
                run.session_records(), key=lambda record: record.started_at
            )
        ],
        "interventions": [
            record.model_dump(mode="json") for record in run.intervention_records()
        ],
        "reconciliations": [
            record.model_dump(mode="json") for record in run.reconciliation_records()
        ],
        "gates": gates,
        "nodes": _node_counts(manifest, graphs),
        "open_gates": _open_gates(gates),
    }


def transcript_tail(
    state_dir: Path, run_id: str, session_id: str, *, after: int
) -> dict[str, Any]:
    """The transcript's events past byte `after`, and where to resume.

    Incremental on purpose: the file grows while a session runs, and a long
    transcript should never be re-sent whole on every change. Only complete
    lines are consumed — a line still being flushed is left for the next call,
    so `offset` always lands on a line boundary. A malformed complete line is
    skipped rather than fatal: this reads a stream it does not own.
    """
    run = load_run(state_dir, run_id)
    path = run.transcript_path(session_id)
    if path.parent != run.transcripts_dir or not path.is_file():
        raise UsageError(f"no transcript for session {session_id}")
    events: list[StreamEvent] = []
    offset = max(0, after)
    with path.open("rb") as handle:
        handle.seek(offset)
        for raw in handle:
            if not raw.endswith(b"\n"):
                break
            offset += len(raw)
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
    return {"events": events, "offset": offset}
