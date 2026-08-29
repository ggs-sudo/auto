"""A generated fixture: run directories the website is built and tested against.

The generator produces a state directory holding three runs — one live with a
subgraph and all four gate kinds open or answered, one finished, one whose
process died mid-turn — plus the fake target repo their graphs live in. Every
file is written through the same Pydantic models the harness writes with, so
the fixture cannot drift from the schemas; `tests/test_fixture.py` additionally
validates every file against the checked-in `schemas/` to keep that claim
honest end to end.

Deterministic on purpose: fixed timestamps, readable ids. A test that fails on
this data fails the same way every time, and a screenshot of the website over
it is comparable across branches. Replaced by a real run snapshot after the
first end-to-end run.

    uv run python -m auto.fixture <directory>   # then: auto serve --state-dir <directory>/state
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from auto.graph import GRAPH_FILENAME, ISSUES_DIR
from auto.model import (
    Gate,
    GateDecision,
    GateKind,
    GateResponse,
    Graph,
    InterventionRecord,
    Manifest,
    NodeStatus,
    NodeType,
    Route,
    RunStatus,
    SessionRecord,
    SessionStatus,
)
from auto.model.config import ResolvedConfig
from auto.model.graph import GraphNode, TaskResolutionMode, TicketType
from auto.model.intervention import InterventionTrigger, ToolCall
from auto.model.manifest import RootNode
from auto.model.session import Telemetry
from auto.owed import EFFORT_ROOT
from auto.run import RunDirectory, write_atomically

STATE_DIR_NAME = "state"
TARGET_REPO_NAME = "target-repo"

LIVE_RUN_ID = "20260829-091500-checkout-revamp"
DONE_RUN_ID = "20260826-141000-signup-polish"
CRASHED_RUN_ID = "20260824-183000-import-contacts"

_BASE = datetime(2026, 8, 29, 9, 15, 0, tzinfo=UTC)


def _at(minutes: float) -> datetime:
    return _BASE + timedelta(minutes=minutes)


@dataclass(frozen=True)
class Fixture:
    """Where the generated pieces landed."""

    root: Path
    state_dir: Path
    target_repo: Path
    run_ids: tuple[str, ...]


def generate(root: Path) -> Fixture:
    """Write the whole fixture under `root`, replacing nothing that exists."""
    state_dir = root / STATE_DIR_NAME
    target_repo = root / TARGET_REPO_NAME
    target_repo.mkdir(parents=True, exist_ok=True)

    _write_live_run(state_dir, target_repo)
    _write_done_run(state_dir, target_repo)
    _write_crashed_run(state_dir, target_repo)

    return Fixture(
        root=root,
        state_dir=state_dir,
        target_repo=target_repo,
        run_ids=(LIVE_RUN_ID, DONE_RUN_ID, CRASHED_RUN_ID),
    )


# ---------------------------------------------------------------------------
# stream-json events, shaped as `claude -p --output-format stream-json` writes
# them — the transcript capture format, which is also the replay format.


def _init(session_id: str) -> dict[str, Any]:
    return {
        "type": "system",
        "subtype": "init",
        "session_id": session_id,
        "model": "claude-opus-5",
        "tools": ["Read", "Edit", "Write", "Bash"],
        "permissionMode": "bypassPermissions",
    }


def _assistant(session_id: str, text: str) -> dict[str, Any]:
    return {
        "type": "assistant",
        "session_id": session_id,
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }


def _tool_use(session_id: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "assistant",
        "session_id": session_id,
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "toolu_fixture", "name": tool, "input": args}],
        },
    }


def _tool_result(session_id: str, text: str) -> dict[str, Any]:
    return {
        "type": "user",
        "session_id": session_id,
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "toolu_fixture", "content": text}
            ],
        },
    }


def _result(
    session_id: str, text: str, *, cost_usd: float, num_turns: int
) -> dict[str, Any]:
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": text,
        "session_id": session_id,
        "num_turns": num_turns,
        "duration_ms": 128_000,
        "stop_reason": "end_turn",
        "terminal_reason": "completed",
        "total_cost_usd": cost_usd,
        "usage": {"input_tokens": 18_200, "output_tokens": 3_400},
        "modelUsage": {"claude-opus-5": {"costUSD": cost_usd}},
        "permission_denials": [],
    }


def _write_transcript(
    run: RunDirectory, session_id: str, events: list[dict[str, Any]]
) -> None:
    """Whole-file, not appended: regenerating over an existing fixture must
    replace the transcript, where the harness's own writer only ever adds."""
    write_atomically(
        run.transcript_path(session_id),
        "".join(json.dumps(event) + "\n" for event in events),
    )


def _telemetry(cost_usd: float, num_turns: int) -> Telemetry:
    return Telemetry(
        cost_usd=cost_usd,
        num_turns=num_turns,
        duration_ms=128_000,
        stop_reason="end_turn",
        terminal_reason="completed",
        is_error=False,
    )


# ---------------------------------------------------------------------------
# the target repo: tickets and graph snapshots, exactly where a run leaves them


def _write_ticket(
    repo: Path, effort: str, stem: str, *, ticket_type: str | None, blocked_by: str
) -> None:
    issues = repo / EFFORT_ROOT / effort / ISSUES_DIR
    issues.mkdir(parents=True, exist_ok=True)
    title = stem.split("-", 1)[1].replace("-", " ")
    type_line = f"Type: {ticket_type}\n" if ticket_type is not None else ""
    (issues / f"{stem}.md").write_text(
        f"# {title}\n\n{type_line}Blocked by: {blocked_by}\nStatus: open\n",
        encoding="utf-8",
    )


def _persist_graph(repo: Path, graph: Graph) -> None:
    path = repo / EFFORT_ROOT / graph.graph_id / GRAPH_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomically(path, graph.model_dump_json(indent=2) + "\n")


def _node(
    stem: str,
    effort: str,
    ticket_type: TicketType,
    *,
    status: NodeStatus,
    blocked_by: list[str] | None = None,
    session_id: str | None = None,
    task_mode: TaskResolutionMode | None = None,
    graph: str | None = None,
    gate: str | None = None,
) -> GraphNode:
    return GraphNode(
        node_id=stem,
        ticket=f"{EFFORT_ROOT}/{effort}/{ISSUES_DIR}/{stem}.md",
        ticket_type=ticket_type,
        task_mode=task_mode,
        blocked_by=blocked_by or [],
        status=status,
        session_id=session_id,
        graph=graph,
        gate=gate,
    )


# ---------------------------------------------------------------------------
# the three runs


def _manifest(
    run_id: str,
    *,
    route: Route,
    prompt: str,
    target_repo: Path,
    created_at: datetime,
    root: RootNode,
    status: RunStatus,
    ended_at: datetime | None = None,
    phase: str | None = None,
    driven: float = 0.0,
    orchestrator: float = 0.0,
) -> Manifest:
    return Manifest(
        run_id=run_id,
        route=route,
        prompt=prompt,
        target_repo=str(target_repo),
        worktree=str(target_repo),
        branch="main",
        head="4f2a9c1d8e0b7a6534120fedcba9876543210fed",
        dirty=False,
        config=ResolvedConfig(),
        created_at=created_at,
        ended_at=ended_at,
        status=status,
        phase=phase,
        root_node=root,
        driven_spend_usd=driven,
        orchestrator_spend_usd=orchestrator,
    )


def _write_live_run(state_dir: Path, repo: Path) -> None:
    """The run the website earns its keep on: parallel work, a subgraph, and
    every gate kind at once — three open, one already answered."""
    prompt = (
        "Rebuild the checkout flow: single-page payment form, Stripe under "
        "the hood, keep the legacy card vault importable."
    )
    root = RootNode(
        type=NodeType.WAYFINDER,
        prompt=prompt,
        status=NodeStatus.DONE,
        session_id="sess-root",
        graph="checkout-revamp",
    )
    manifest = _manifest(
        LIVE_RUN_ID,
        route=Route.WAYFINDER,
        prompt=prompt,
        target_repo=repo,
        created_at=_at(0),
        root=root,
        status=RunStatus.RUNNING,
        phase="executing the graph",
        driven=4.8312,
        orchestrator=0.4105,
    )
    run = RunDirectory.create(state_dir, manifest)
    run.write_orchestrator_prompt(
        "# Standing brief (fixture)\n\nGenerated by `auto.fixture`; the real "
        "run writes the agent's stable prompt here.\n"
    )

    # -- graphs, beside their tickets in the target repo ---------------------
    effort = "checkout-revamp"
    tickets: list[tuple[str, str | None, str]] = [
        ("0001-research-payment-providers", "research", "none"),
        ("0002-grill-checkout-flow", "grilling", "0001"),
        ("0003-provision-stripe-account", "task", "0001"),
        ("0004-prototype-payment-form", "prototype", "0001"),
        ("0005-wire-webhooks", None, "0002, 0003"),
        ("0006-import-legacy-cards", "task", "none"),
    ]
    for stem, ticket_type, blockers in tickets:
        _write_ticket(repo, effort, stem, ticket_type=ticket_type, blocked_by=blockers)
    graph = Graph(
        graph_id=effort,
        spawned_by="root",
        nodes=[
            _node(
                "0001-research-payment-providers",
                effort,
                TicketType.RESEARCH,
                status=NodeStatus.DONE,
                session_id="sess-research",
            ),
            _node(
                "0002-grill-checkout-flow",
                effort,
                TicketType.GRILLING,
                status=NodeStatus.DONE,
                blocked_by=["0001-research-payment-providers"],
                session_id="sess-grill",
                graph="checkout-flow",
            ),
            _node(
                "0003-provision-stripe-account",
                effort,
                TicketType.TASK,
                status=NodeStatus.REVIEW_PENDING,
                blocked_by=["0001-research-payment-providers"],
                session_id="sess-provision",
                task_mode=TaskResolutionMode.USER,
                gate="0002-checkout-revamp-0003-provision-stripe-account",
            ),
            _node(
                "0004-prototype-payment-form",
                effort,
                TicketType.PROTOTYPE,
                status=NodeStatus.REVIEW_PENDING,
                blocked_by=["0001-research-payment-providers"],
                session_id="sess-prototype",
                gate="0003-checkout-revamp-0004-prototype-payment-form",
            ),
            _node(
                "0005-wire-webhooks",
                effort,
                TicketType.IMPLEMENT,
                status=NodeStatus.PENDING,
                blocked_by=["0002-grill-checkout-flow", "0003-provision-stripe-account"],
            ),
            _node(
                "0006-import-legacy-cards",
                effort,
                TicketType.TASK,
                status=NodeStatus.IN_PROGRESS,
                session_id="sess-import",
                task_mode=TaskResolutionMode.AGENT,
            ),
        ],
    )
    _persist_graph(repo, graph)

    subgraph_id = "checkout-flow"
    for stem, ticket_type, blockers in [
        ("0001-implement-cart-summary", None, "none"),
        ("0002-implement-checkout-page", None, "0001"),
        ("0003-checkout-analytics", None, "0002"),
    ]:
        _write_ticket(repo, subgraph_id, stem, ticket_type=ticket_type, blocked_by=blockers)
    subgraph = Graph(
        graph_id=subgraph_id,
        spawned_by="checkout-revamp/0002-grill-checkout-flow",
        nodes=[
            _node(
                "0001-implement-cart-summary",
                subgraph_id,
                TicketType.IMPLEMENT,
                status=NodeStatus.DONE,
                session_id="sess-cart",
            ),
            _node(
                "0002-implement-checkout-page",
                subgraph_id,
                TicketType.IMPLEMENT,
                status=NodeStatus.REVIEW_PENDING,
                blocked_by=["0001-implement-cart-summary"],
                session_id="sess-checkout-page",
                gate="0004-checkout-flow-0002-implement-checkout-page",
            ),
            _node(
                "0003-checkout-analytics",
                subgraph_id,
                TicketType.IMPLEMENT,
                status=NodeStatus.PENDING,
                blocked_by=["0002-implement-checkout-page"],
            ),
        ],
    )
    _persist_graph(repo, subgraph)

    # -- gates: all four kinds, numbered run-wide ----------------------------
    run.write_gate(
        Gate(
            gate_id="0001-run",
            sequence=1,
            kind=GateKind.USER_PING,
            node=None,
            question="The map is charted: 6 tickets in checkout-revamp. Carrying on.",
            raised_at=_at(22),
            answered_at=_at(35),
        )
    )
    write_atomically(
        run.gate_response_path("0001-run"),
        GateResponse(decision=GateDecision.DISMISS, text="Seen — carry on.")
        .model_dump_json(indent=2)
        + "\n",
    )
    run.write_gate(
        Gate(
            gate_id="0002-checkout-revamp-0003-provision-stripe-account",
            sequence=2,
            kind=GateKind.TASK_COMPLETION,
            node="checkout-revamp/0003-provision-stripe-account",
            question=(
                "Only you can do this one: create the Stripe account and put "
                "the restricted key in 1Password. Reply with where the key "
                "lives and the account id."
            ),
            raised_at=_at(58),
        )
    )
    run.write_gate(
        Gate(
            gate_id="0003-checkout-revamp-0004-prototype-payment-form",
            sequence=3,
            kind=GateKind.PROTOTYPE_REVIEW,
            node="checkout-revamp/0004-prototype-payment-form",
            question=(
                "Three payment-form variants are up. B folds the address into "
                "one step; C splits card entry. Which direction?"
            ),
            artifact="http://localhost:5199/?variant=A",
            raised_at=_at(71),
        )
    )
    run.write_gate(
        Gate(
            gate_id="0004-checkout-flow-0002-implement-checkout-page",
            sequence=4,
            kind=GateKind.ESCALATED_QUESTION,
            node="checkout-flow/0002-implement-checkout-page",
            question=(
                "The legacy cart cookie is read by the mobile app too. "
                "Renaming it breaks released clients — accept a six-month "
                "dual-write, or break clients older than 3.2?"
            ),
            raised_at=_at(83),
        )
    )

    # -- sessions and their transcripts --------------------------------------
    def session(
        session_id: str,
        node: str,
        role: NodeType,
        *,
        ticket: str | None,
        status: SessionStatus,
        started: datetime,
        ended: datetime | None,
        summary: str | None,
        highlights: list[str],
        cost: float,
        turns: int,
        events: list[dict[str, Any]],
    ) -> None:
        record = SessionRecord(
            session_id=session_id,
            node=node,
            role=role,
            ticket=ticket,
            status=status,
            started_at=started,
            ended_at=ended,
            summary=summary,
            highlights=highlights,
            captured_transcript=run.relative_transcript_path(session_id),
            telemetry=_telemetry(cost, turns),
        )
        run.write_session(record)
        _write_transcript(run, session_id, events)

    session(
        "sess-root",
        "root",
        NodeType.WAYFINDER,
        ticket=None,
        status=SessionStatus.SUCCEEDED,
        started=_at(0),
        ended=_at(24),
        summary="Charted the map: 6 tickets under checkout-revamp.",
        highlights=[
            "Stripe over Adyen: existing account, EU entity already vetted",
            "6 tickets: 1 research, 1 grilling, 2 tasks, 1 prototype, 1 implement",
        ],
        cost=1.2140,
        turns=9,
        events=[
            _init("sess-root"),
            _assistant(
                "sess-root",
                "Reading the repo docs before charting. The checkout code is "
                "split across cart/ and billing/; the prompt wants one flow.",
            ),
            _tool_use("sess-root", "Read", {"file_path": "docs/architecture.md"}),
            _tool_result("sess-root", "# Architecture\n\ncart/ owns the basket…"),
            _result(
                "sess-root",
                "Map charted. Decision tickets written under .scratch/checkout-revamp/.",
                cost_usd=0.6,
                num_turns=4,
            ),
            _assistant(
                "sess-root",
                "Collapsing the map: /to-spec then /to-tickets in this session.",
            ),
            _result(
                "sess-root",
                "Tickets written: 0001–0006 under .scratch/checkout-revamp/issues/.",
                cost_usd=1.214,
                num_turns=9,
            ),
        ],
    )
    session(
        "sess-research",
        "checkout-revamp/0001-research-payment-providers",
        NodeType.RESEARCH,
        ticket=f"{EFFORT_ROOT}/checkout-revamp/{ISSUES_DIR}/0001-research-payment-providers.md",
        status=SessionStatus.SUCCEEDED,
        started=_at(26),
        ended=_at(41),
        summary="Compared Stripe, Adyen and Mollie; report recommends Stripe.",
        highlights=["Stripe: SetupIntents cover the vault-import path"],
        cost=0.7011,
        turns=6,
        events=[
            _init("sess-research"),
            _assistant("sess-research", "Researching payment providers per the ticket."),
            _tool_use(
                "sess-research",
                "Write",
                {"file_path": ".scratch/checkout-revamp/research/providers.md"},
            ),
            _tool_result("sess-research", "wrote 84 lines"),
            _result(
                "sess-research",
                "Report written to .scratch/checkout-revamp/research/providers.md.",
                cost_usd=0.7011,
                num_turns=6,
            ),
        ],
    )
    session(
        "sess-grill",
        "checkout-revamp/0002-grill-checkout-flow",
        NodeType.GRILL_WITH_DOCS,
        ticket=f"{EFFORT_ROOT}/checkout-revamp/{ISSUES_DIR}/0002-grill-checkout-flow.md",
        status=SessionStatus.SUCCEEDED,
        started=_at(43),
        ended=_at(66),
        summary="Grilled the checkout flow; spec and 3 tickets under checkout-flow/.",
        highlights=["One-page flow won: fewer abandonments in the research report"],
        cost=1.1030,
        turns=11,
        events=[
            _init("sess-grill"),
            _assistant(
                "sess-grill",
                "Q1: does the checkout page replace cart review, or follow it?",
            ),
            _result(
                "sess-grill",
                "Interview open: 1 of ~6 questions asked.",
                cost_usd=0.3,
                num_turns=2,
            ),
            _assistant(
                "sess-grill",
                "Spec agreed. Writing tickets under .scratch/checkout-flow/.",
            ),
            _result(
                "sess-grill",
                "Spec and tickets written: 0001–0003 under .scratch/checkout-flow/issues/.",
                cost_usd=1.103,
                num_turns=11,
            ),
        ],
    )
    session(
        "sess-provision",
        "checkout-revamp/0003-provision-stripe-account",
        NodeType.IMPLEMENT,
        ticket=f"{EFFORT_ROOT}/checkout-revamp/{ISSUES_DIR}/0003-provision-stripe-account.md",
        status=SessionStatus.RUNNING,
        started=_at(50),
        ended=None,
        summary="Hit the human-only wall: account creation needs a card and 2FA.",
        highlights=["Everything scriptable is done; the account itself needs a human"],
        cost=0.2204,
        turns=3,
        events=[
            _init("sess-provision"),
            _assistant(
                "sess-provision",
                "The ticket wants a Stripe account. I can prepare the env file "
                "and the webhook config, but account signup needs a human.",
            ),
            _result(
                "sess-provision",
                "Prepared .env.example and stripe/webhooks.toml; the account "
                "itself is human-only.",
                cost_usd=0.2204,
                num_turns=3,
            ),
        ],
    )
    session(
        "sess-prototype",
        "checkout-revamp/0004-prototype-payment-form",
        NodeType.PROTOTYPE,
        ticket=f"{EFFORT_ROOT}/checkout-revamp/{ISSUES_DIR}/0004-prototype-payment-form.md",
        status=SessionStatus.RUNNING,
        started=_at(52),
        ended=None,
        summary="Three variants built and serving; waiting on review.",
        highlights=["Variants A/B/C at localhost:5199, switchable via ?variant="],
        cost=0.9420,
        turns=7,
        events=[
            _init("sess-prototype"),
            _assistant("sess-prototype", "Building three payment-form variants."),
            _tool_use("sess-prototype", "Bash", {"command": "npm run dev"}),
            _tool_result("sess-prototype", "vite dev server on :5199"),
            _result(
                "sess-prototype",
                "Three variants up at http://localhost:5199/?variant=A.",
                cost_usd=0.942,
                num_turns=7,
            ),
        ],
    )
    session(
        "sess-import",
        "checkout-revamp/0006-import-legacy-cards",
        NodeType.IMPLEMENT,
        ticket=f"{EFFORT_ROOT}/checkout-revamp/{ISSUES_DIR}/0006-import-legacy-cards.md",
        status=SessionStatus.RUNNING,
        started=_at(48),
        ended=None,
        summary="Importing the legacy card vault via SetupIntents.",
        highlights=[
            "14,208 vault rows; 312 rejected by Luhn check so far",
            "Import is resumable: cursor kept in .scratch/import-cursor",
        ],
        cost=0.6507,
        turns=5,
        events=[
            _init("sess-import"),
            _assistant("sess-import", "Reading the vault schema before writing the importer."),
            _tool_use("sess-import", "Read", {"file_path": "billing/vault.py"}),
            _tool_result("sess-import", "class CardVault: …"),
            _result(
                "sess-import",
                "Importer written; dry run over the first 1,000 rows passes.",
                cost_usd=0.41,
                num_turns=3,
            ),
            _assistant(
                "sess-import",
                "Noted — updating the ticket with the Luhn-reject count and "
                "the cursor location before running the full import.",
            ),
            _tool_use("sess-import", "Edit", {"file_path": ".scratch/checkout-revamp/issues/0006-import-legacy-cards.md"}),
        ],
    )
    session(
        "sess-cart",
        "checkout-flow/0001-implement-cart-summary",
        NodeType.IMPLEMENT,
        ticket=f"{EFFORT_ROOT}/checkout-flow/{ISSUES_DIR}/0001-implement-cart-summary.md",
        status=SessionStatus.SUCCEEDED,
        started=_at(68),
        ended=_at(80),
        summary="Cart summary component implemented and committed.",
        highlights=[],
        cost=0.5120,
        turns=4,
        events=[
            _init("sess-cart"),
            _assistant("sess-cart", "Implementing the cart summary per the ticket."),
            _result(
                "sess-cart",
                "Done: CartSummary lands in cart/summary.tsx, tests green, committed.",
                cost_usd=0.512,
                num_turns=4,
            ),
        ],
    )
    session(
        "sess-checkout-page",
        "checkout-flow/0002-implement-checkout-page",
        NodeType.IMPLEMENT,
        ticket=f"{EFFORT_ROOT}/checkout-flow/{ISSUES_DIR}/0002-implement-checkout-page.md",
        status=SessionStatus.RUNNING,
        started=_at(81),
        ended=None,
        summary="Blocked on the cart-cookie compatibility call.",
        highlights=["Legacy cart cookie is read by mobile ≤3.1"],
        cost=0.4880,
        turns=4,
        events=[
            _init("sess-checkout-page"),
            _assistant(
                "sess-checkout-page",
                "The checkout page needs the cart cookie renamed, but grep "
                "says the mobile app reads it too. This is a compatibility "
                "decision I should not make alone.",
            ),
            _result(
                "sess-checkout-page",
                "Paused: the cookie rename is a released-client compatibility call.",
                cost_usd=0.488,
                num_turns=4,
            ),
        ],
    )

    # -- interventions: why the run did what it did --------------------------
    def intervention(
        intervention_id: str,
        node: str,
        *,
        trigger: InterventionTrigger,
        started: datetime,
        prose: str,
        tool_calls: list[ToolCall],
        session_id: str,
    ) -> None:
        run.write_intervention(
            InterventionRecord(
                intervention_id=intervention_id,
                node=node,
                trigger=trigger,
                model="claude-opus-5",
                session_id=session_id,
                started_at=started,
                ended_at=started + timedelta(seconds=40),
                prose=prose,
                tool_calls=tool_calls,
                telemetry=_telemetry(0.021, 1),
            )
        )

    intervention(
        "0001-root",
        "root",
        trigger=InterventionTrigger.STALE,
        started=_at(12),
        prose=(
            "The map has open questions the docs already answer. Answering "
            "the port question from docs/architecture.md and letting it carry on."
        ),
        tool_calls=[
            ToolCall(
                tool="send_to_session",
                arguments={
                    "message": (
                        "The docs answer this: checkout stays on the main app "
                        "port, no separate service. Carry on with the map."
                    )
                },
            )
        ],
        session_id="agent-0001",
    )
    intervention(
        "0002-root",
        "root",
        trigger=InterventionTrigger.STALE,
        started=_at(24),
        prose="The map is closed and the tickets are on disk. Emitting the graph.",
        tool_calls=[
            ToolCall(
                tool="emit_graph",
                arguments={
                    "effort": "checkout-revamp",
                    "tasks": [
                        {"ticket": "0003", "mode": "user"},
                        {"ticket": "0006", "mode": "agent"},
                    ],
                },
            ),
            ToolCall(
                tool="complete_node",
                arguments={"summary": "Charted; the tickets are the graph now."},
            ),
        ],
        session_id="agent-0002",
    )
    intervention(
        "0003-checkout-revamp-0006-import-legacy-cards",
        "checkout-revamp/0006-import-legacy-cards",
        trigger=InterventionTrigger.STALE,
        started=_at(55),
        prose=(
            "The importer works but the ticket was not updated — the reject "
            "count and cursor location live only in the transcript. Nudging."
        ),
        tool_calls=[
            ToolCall(
                tool="complete_node",
                arguments={"summary": "Importer written and dry-run clean."},
                refused=(
                    "node checkout-revamp/0006-import-legacy-cards is not "
                    "complete: it still owes the ticket's Status update"
                ),
            ),
            ToolCall(
                tool="send_to_session",
                arguments={
                    "message": (
                        "Before running the full import, update the ticket: "
                        "record the Luhn-reject count and where the resume "
                        "cursor lives, per the tracker convention."
                    ),
                    "highlights": ["14,208 vault rows; 312 rejected by Luhn check so far"],
                },
            ),
        ],
        session_id="agent-0003",
    )
    intervention(
        "0004-checkout-revamp-0004-prototype-payment-form",
        "checkout-revamp/0004-prototype-payment-form",
        trigger=InterventionTrigger.STALE,
        started=_at(71),
        prose="Three variants are serving. Marking the node reviewable and pinging.",
        tool_calls=[
            ToolCall(
                tool="prototype_ready",
                arguments={
                    "artifact": "http://localhost:5199/?variant=A",
                    "question": "B folds the address into one step; C splits card entry.",
                },
            )
        ],
        session_id="agent-0004",
    )
    intervention(
        "0005-checkout-flow-0002-implement-checkout-page",
        "checkout-flow/0002-implement-checkout-page",
        trigger=InterventionTrigger.STALE,
        started=_at(83),
        prose=(
            "The cookie rename breaks released mobile clients. That is a "
            "compatibility decision the answer policy reserves for the user."
        ),
        tool_calls=[
            ToolCall(
                tool="escalate_question",
                arguments={
                    "question": "Dual-write the cart cookie for six months, or break mobile ≤3.1?"
                },
            )
        ],
        session_id="agent-0005",
    )


def _write_done_run(state_dir: Path, repo: Path) -> None:
    """A finished run: everything terminal, still fully readable."""
    prompt = "Polish the signup flow: inline validation and a resend-email link."
    effort = "signup-polish"
    root = RootNode(
        type=NodeType.GRILL_WITH_DOCS,
        prompt=prompt,
        status=NodeStatus.DONE,
        session_id="sess-sp-root",
        graph=effort,
    )
    created = _at(-4210)  # a few days earlier
    manifest = _manifest(
        DONE_RUN_ID,
        route=Route.GRILL,
        prompt=prompt,
        target_repo=repo,
        created_at=created,
        root=root,
        status=RunStatus.DONE,
        ended_at=created + timedelta(minutes=147),
        driven=2.1408,
        orchestrator=0.1502,
    )
    run = RunDirectory.create(state_dir, manifest)

    for stem, blockers in [
        ("0001-inline-validation", "none"),
        ("0002-resend-email-link", "0001"),
    ]:
        _write_ticket(repo, effort, stem, ticket_type=None, blocked_by=blockers)
    _persist_graph(
        repo,
        Graph(
            graph_id=effort,
            spawned_by="root",
            nodes=[
                _node(
                    "0001-inline-validation",
                    effort,
                    TicketType.IMPLEMENT,
                    status=NodeStatus.DONE,
                    session_id="sess-sp-1",
                ),
                _node(
                    "0002-resend-email-link",
                    effort,
                    TicketType.IMPLEMENT,
                    status=NodeStatus.DONE,
                    blocked_by=["0001-inline-validation"],
                    session_id="sess-sp-2",
                ),
            ],
        ),
    )

    for session_id, node, role, ticket, started, ended, summary, cost, turns in [
        (
            "sess-sp-root",
            "root",
            NodeType.GRILL_WITH_DOCS,
            None,
            created,
            created + timedelta(minutes=31),
            "Grilled, spec agreed, 2 tickets written.",
            0.8,
            8,
        ),
        (
            "sess-sp-1",
            f"{effort}/0001-inline-validation",
            NodeType.IMPLEMENT,
            f"{EFFORT_ROOT}/{effort}/{ISSUES_DIR}/0001-inline-validation.md",
            created + timedelta(minutes=35),
            created + timedelta(minutes=88),
            "Inline validation on every signup field; tests green.",
            0.71,
            5,
        ),
        (
            "sess-sp-2",
            f"{effort}/0002-resend-email-link",
            NodeType.IMPLEMENT,
            f"{EFFORT_ROOT}/{effort}/{ISSUES_DIR}/0002-resend-email-link.md",
            created + timedelta(minutes=92),
            created + timedelta(minutes=145),
            "Resend link with a 60s cooldown; committed.",
            0.63,
            4,
        ),
    ]:
        record = SessionRecord(
            session_id=session_id,
            node=node,
            role=role,
            ticket=ticket,
            status=SessionStatus.SUCCEEDED,
            started_at=started,
            ended_at=ended,
            summary=summary,
            highlights=[],
            captured_transcript=run.relative_transcript_path(session_id),
            telemetry=_telemetry(cost, turns),
        )
        run.write_session(record)
        _write_transcript(
            run,
            session_id,
            [
                _init(session_id),
                _assistant(session_id, f"Working {node}."),
                _result(session_id, summary, cost_usd=cost, num_turns=turns),
            ],
        )


def _write_crashed_run(state_dir: Path, repo: Path) -> None:
    """A run whose process died mid-turn: manifest still says running, the
    transcript stops without a result event, and no graph was ever emitted.
    The website must show it anyway — a crash must not take the window with it."""
    prompt = "Import contacts from the old CRM export."
    root = RootNode(
        type=NodeType.WAYFINDER,
        prompt=prompt,
        status=NodeStatus.IN_PROGRESS,
        session_id="sess-ic-root",
    )
    created = _at(-6870)
    manifest = _manifest(
        CRASHED_RUN_ID,
        route=Route.WAYFINDER,
        prompt=prompt,
        target_repo=repo,
        created_at=created,
        root=root,
        status=RunStatus.RUNNING,
        phase="charting the map",
        driven=0.1893,
    )
    run = RunDirectory.create(state_dir, manifest)

    record = SessionRecord(
        session_id="sess-ic-root",
        node="root",
        role=NodeType.WAYFINDER,
        ticket=None,
        status=SessionStatus.RUNNING,
        started_at=created,
        summary=None,
        highlights=[],
        captured_transcript=run.relative_transcript_path("sess-ic-root"),
        telemetry=Telemetry(),
    )
    run.write_session(record)
    # The stream stops after the tool_use: no tool result, no result event.
    # That is what a crash looks like from the run directory.
    _write_transcript(
        run,
        "sess-ic-root",
        [
            _init("sess-ic-root"),
            _assistant("sess-ic-root", "Reading the CRM export format before charting."),
            _tool_use("sess-ic-root", "Read", {"file_path": "export/contacts.csv"}),
        ],
    )


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m auto.fixture <directory>", file=sys.stderr)
        return 2
    fixture = generate(Path(args[0]).expanduser())
    print(f"state dir:   {fixture.state_dir}")
    print(f"target repo: {fixture.target_repo}")
    for run_id in fixture.run_ids:
        print(f"run:         {run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
