"""Gates: a run pauses on a human without stopping anything else.

A gate sits on a node, and the blocked set derives downstream of that node
exactly as a failure's does. The gated session stays alive and idle; the
response file written beside the gate — by the website, never the orchestrator
— triggers an ordinary intervention that delivers the answer into the waiting
session. Everything below the launcher seam is real: the gate files, the MCP
tools, the poll, every write.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Sequence
from pathlib import Path
from typing import Any, ClassVar

import pytest

import auto.orchestrate as orchestrate
from auto.gates import GateLedger
from auto.graph import GraphStore, ready
from auto.model import (
    Gate,
    GateKind,
    Graph,
    InterventionTrigger,
    Manifest,
    NodeStatus,
    RunStatus,
)
from auto.orchestrate import Orchestrator, PreparedRun, execute_run, prepare_run
from auto.run import RunDirectory
from auto.tools.harness import (
    ESCALATE_QUESTION,
    HAND_TO_USER,
    PING_USER,
    PROTOTYPE_READY,
    ToolResult,
)
from auto.tools.server import ToolServer

from tests.agents import (
    HarnessLauncher,
    ScriptedAgent,
    completes,
    emits_and_completes,
    sends,
    tries_to_complete,
)
from tests.conftest import call_tool, one_turn, tool_text
from tests.test_dispatch import (
    EFFORT,
    charted,
    node_id,
    resolves,
    ticket_body,
    ticket_path,
)
from tests.test_orchestrate import a_request
from tests.test_run_directory import CREATED_AT
from tests.test_run_directory import a_manifest as run_directory_manifest
from tests.test_tool_server import RecordingTools

PROTO = "01-proto"

PROTO_RESOLVED = """# A ticket

**Type:** prototype

**Blocked by:** None (can start immediately)

**Status:** resolved

## Answer

Go with variant B.
"""


KEYS = "01-keys"

KEYS_DONE = """# A ticket

**Type:** task

**Blocked by:** None (can start immediately)

**Status:** done

## Answer

The key lives in 1Password under deploy/stripe.
"""


def hands(
    question: str = "Create the Stripe key and report where it lives.",
) -> ScriptedAgent:
    """An agent that judges the session to have hit the human-only wall."""
    return ScriptedAgent(calls=[(HAND_TO_USER, {"question": question})])


def escalates(question: str) -> ScriptedAgent:
    """An agent that judges a question policy-critical."""
    return ScriptedAgent(calls=[(ESCALATE_QUESTION, {"question": question})])


def readies(
    question: str = "Happy with the prototype?",
    artifact: str = "prototype/index.html",
) -> ScriptedAgent:
    """An agent that judges the build ready for the user's eyes."""
    return ScriptedAgent(
        calls=[(PROTOTYPE_READY, {"question": question, "artifact": artifact})]
    )


def respond(run: RunDirectory, gate: Gate, decision: str, text: str = "") -> None:
    """What the website will do one day: write the response beside the gate."""
    run.gate_response_path(gate.gate_id).write_text(
        json.dumps({"decision": decision, "text": text})
    )


async def eventually(check: Callable[[], bool], what: str) -> None:
    deadline = asyncio.get_running_loop().time() + 10.0
    while not check():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"never came true: {what}")
        await asyncio.sleep(0.005)


async def next_gate(run: RunDirectory, sequence: int) -> Gate:
    found: list[Gate] = []

    def raised() -> bool:
        found[:] = [g for g in run.gate_records() if g.sequence == sequence]
        return bool(found)

    await eventually(raised, f"gate {sequence} raised")
    return found[0]


def persisted_graph(target_repo: Path) -> Graph:
    return Graph.model_validate_json(
        (target_repo / ".scratch" / EFFORT / "graph.json").read_text()
    )


def a_gated_run(
    target_repo: Path,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    launcher: HarnessLauncher,
    *,
    announce: list[str] | None = None,
) -> tuple[PreparedRun, Orchestrator, asyncio.Task[Any]]:
    """The orchestrator running as a task, polling fast, beside the test."""
    monkeypatch.setattr(orchestrate, "GATE_POLL_SECONDS", 0.005)
    prepared = prepare_run(a_request(target_repo, state_dir))
    orchestrator = Orchestrator(
        prepared.run,
        prepared.manifest,
        launcher,
        announce=(announce.append if announce is not None else lambda _: None),
    )
    return prepared, orchestrator, asyncio.create_task(orchestrator.execute())


@pytest.fixture
async def server() -> AsyncIterator[ToolServer]:
    tools = ToolServer(asyncio.get_running_loop())
    try:
        yield tools
    finally:
        tools.close()


def a_store_with(
    repo: Path, *tickets: tuple[str, str], spawned_by: str = "root"
) -> GraphStore:
    for name, body in tickets:
        path = repo / ".scratch" / EFFORT / "issues" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    store = GraphStore(repo)
    store.emit(EFFORT, spawned_by=spawned_by)
    return store


def test_a_review_pending_node_blocks_its_dependents_and_nothing_else(
    tmp_path: Path,
) -> None:
    """A gate is a pause nobody has answered yet: downstream waits exactly as
    it would for an unfinished blocker, and unrelated nodes stay ready."""
    store = a_store_with(
        tmp_path,
        ("01-proto.md", ticket_body(type_line="prototype")),
        ("02-wire.md", ticket_body(blocked_by="01-proto")),
        ("03-docs.md", ticket_body()),
    )
    graph = store.graphs[0]
    proto = graph.node("01-proto")
    assert proto is not None
    proto.status = NodeStatus.REVIEW_PENDING

    held = {graph.graph_id: graph}
    assert [node.node_id for node in ready(graph, held)] == ["03-docs"]


def test_the_gate_pointer_and_review_status_survive_rederivation(
    tmp_path: Path,
) -> None:
    """Harness-only fields, merged back in by ticket id like every other."""
    store = a_store_with(tmp_path, ("01-proto.md", ticket_body(type_line="prototype")))
    node = store.graphs[0].node("01-proto")
    assert node is not None
    node.status = NodeStatus.REVIEW_PENDING
    node.gate = "0001-add-search-01-proto"

    store.tick()

    fresh = store.graphs[0].node("01-proto")
    assert fresh is not None
    assert fresh.status is NodeStatus.REVIEW_PENDING
    assert fresh.gate == "0001-add-search-01-proto"


async def test_prototype_ready_needs_a_question_and_an_artifact(
    server: ToolServer,
) -> None:
    """A review the user cannot see is not a review: the artifact pointer is
    the tool's to attach, so the schema demands it."""
    tools = RecordingTools()
    with server.intervention("r", "add-search/01-proto", tools):
        url = server.url_for("r", "add-search/01-proto")
        refused = await call_tool(url, PROTOTYPE_READY, {"question": "Look right?"})
        assert refused["isError"]
        assert "artifact" in tool_text(refused)

        landed = await call_tool(
            url,
            PROTOTYPE_READY,
            {"question": "Look right?", "artifact": "prototype/index.html"},
        )
        assert not landed["isError"]
    assert tools.readied == [("Look right?", "prototype/index.html", [])]


async def test_raising_a_gate_is_exclusive_with_the_other_terminal_moves(
    server: ToolServer,
) -> None:
    """A node is nudged onward, called finished, given up on, or parked at a
    gate — never two of those in one intervention."""
    tools = RecordingTools()
    with server.intervention("r", "add-search/01-proto", tools):
        url = server.url_for("r", "add-search/01-proto")
        landed = await call_tool(
            url,
            PROTOTYPE_READY,
            {"question": "Look right?", "artifact": "prototype/index.html"},
        )
        assert not landed["isError"]
        second = await call_tool(url, "send_to_session", {"message": "carry on"})
        assert second["isError"]
        assert PROTOTYPE_READY in tool_text(second)
    assert tools.sent == []


async def test_an_approved_review_lands_in_the_waiting_session_and_the_node_completes(
    target_repo: Path,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole prototype-review round trip: the gate file carries what the
    website needs, the ping fires, the session waits alive, the response is
    delivered into it, and the node completes once the decision is persisted."""
    announce: list[str] = []
    launcher = HarnessLauncher(
        {
            "root": one_turn("Charted."),
            node_id(PROTO): [*one_turn("Built it."), *one_turn("Recorded.")],
        },
        {
            "root": [emits_and_completes(EFFORT)],
            node_id(PROTO): [
                readies(),
                sends("Approved — go with variant B."),
                completes(),
            ],
        },
        writes={
            "root": [charted((f"{PROTO}.md", ticket_body(type_line="prototype")))],
            node_id(PROTO): [{}, {ticket_path(PROTO): PROTO_RESOLVED}],
        },
    )
    prepared, _, run_task = a_gated_run(
        target_repo, state_dir, monkeypatch, launcher, announce=announce
    )

    gate = await next_gate(prepared.run, 1)
    assert gate.kind is GateKind.PROTOTYPE_REVIEW
    assert gate.node == node_id(PROTO)
    assert gate.question == "Happy with the prototype?"
    assert gate.artifact == "prototype/index.html"
    assert gate.answered_at is None

    # The ping tells the user where to look and how to answer.
    assert any(
        gate.gate_id in ping
        and "prototype/index.html" in ping
        and str(prepared.run.gate_response_path(gate.gate_id)) in ping
        for ping in announce
    )

    # The node waits at the gate, pointer persisted for the website to follow.
    await eventually(
        lambda: persisted_graph(target_repo).node(PROTO).status  # type: ignore[union-attr]
        is NodeStatus.REVIEW_PENDING,
        "the node marked review-pending",
    )
    assert persisted_graph(target_repo).node(PROTO).gate == gate.gate_id  # type: ignore[union-attr]

    respond(prepared.run, gate, "approve", "Ship it.")
    manifest = await run_task

    assert manifest.status is RunStatus.DONE
    assert persisted_graph(target_repo).node(PROTO).status is NodeStatus.DONE  # type: ignore[union-attr]
    assert persisted_graph(target_repo).node(PROTO).gate is None  # type: ignore[union-attr]

    # The response reached the still-live session as an ordinary message.
    assert (node_id(PROTO), "Approved — go with variant B.") in launcher.sent

    # The delivery ran as its own intervention, response rendered in verbatim.
    delivery = [
        spec
        for spec in launcher.interventions
        if spec.node_id == node_id(PROTO) and "Ship it." in spec.message
    ]
    assert len(delivery) == 1
    records = prepared.run.intervention_records()
    assert [
        r.trigger for r in records if r.trigger is not InterventionTrigger.STALE
    ] == [InterventionTrigger.GATE_RESPONSE]

    # Answering stamped the gate; the response file stays the website's.
    stamped = prepared.run.gate_records()[0]
    assert stamped.answered_at is not None


async def test_a_revision_is_delivered_verbatim_and_the_next_build_gets_a_fresh_gate(
    target_repo: Path,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gate sequence is the review history: round one's gate survives,
    answered, and round two rises separately numbered. No revision cap."""
    launcher = HarnessLauncher(
        {
            "root": one_turn("Charted."),
            node_id(PROTO): [
                *one_turn("Built it."),
                *one_turn("Revised it."),
                *one_turn("Recorded."),
            ],
        },
        {
            "root": [emits_and_completes(EFFORT)],
            node_id(PROTO): [
                readies("Round one: happy?"),
                sends("Please combine A and C."),
                readies("Round two: happy now?", "prototype/v2.html"),
                sends("Approved."),
                completes(),
            ],
        },
        writes={
            "root": [charted((f"{PROTO}.md", ticket_body(type_line="prototype")))],
            node_id(PROTO): [{}, {}, {ticket_path(PROTO): PROTO_RESOLVED}],
        },
    )
    prepared, _, run_task = a_gated_run(target_repo, state_dir, monkeypatch, launcher)

    first = await next_gate(prepared.run, 1)
    respond(prepared.run, first, "revise", "combine A and C")

    second = await next_gate(prepared.run, 2)
    assert second.gate_id != first.gate_id
    assert second.question == "Round two: happy now?"
    respond(prepared.run, second, "approve")

    manifest = await run_task
    assert manifest.status is RunStatus.DONE

    # The free text reached the delivery agent verbatim, never parsed.
    deliveries = [
        spec.message
        for spec in launcher.interventions
        if "combine A and C" in spec.message
    ]
    assert len(deliveries) == 1
    # And the session itself was messaged between the two builds.
    assert (node_id(PROTO), "Please combine A and C.") in launcher.sent

    # Round one's request survives to be read during round three.
    history = prepared.run.gate_records()
    assert [g.sequence for g in history] == [1, 2]
    assert history[0].question == "Round one: happy?"
    assert all(g.answered_at is not None for g in history)


async def test_a_gate_blocks_only_downstream_and_the_rest_of_the_run_keeps_going(
    target_repo: Path,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """02 depends on the gated prototype and must not dispatch; 03 is
    unrelated and runs to done while the user is away."""
    launcher = HarnessLauncher(
        {
            "root": one_turn("Charted."),
            node_id(PROTO): [*one_turn("Built it."), *one_turn("Recorded.")],
            node_id("02-wire"): one_turn("Wired."),
            node_id("03-docs"): one_turn("Documented."),
        },
        {
            "root": [emits_and_completes(EFFORT)],
            node_id(PROTO): [readies(), sends("Approved."), completes()],
            node_id("02-wire"): [completes()],
            node_id("03-docs"): [completes()],
        },
        writes={
            "root": [
                charted(
                    (f"{PROTO}.md", ticket_body(type_line="prototype")),
                    ("02-wire.md", ticket_body(blocked_by=PROTO)),
                    ("03-docs.md", ticket_body()),
                )
            ],
            node_id(PROTO): [{}, {ticket_path(PROTO): PROTO_RESOLVED}],
            node_id("02-wire"): [
                {ticket_path("02-wire"): ticket_body(status="resolved")}
            ],
            node_id("03-docs"): [
                {ticket_path("03-docs"): ticket_body(status="resolved")}
            ],
        },
    )
    prepared, _, run_task = a_gated_run(target_repo, state_dir, monkeypatch, launcher)

    gate = await next_gate(prepared.run, 1)
    # Unrelated work runs to done while the gate stands open.
    await eventually(
        lambda: persisted_graph(target_repo).node("03-docs").status  # type: ignore[union-attr]
        is NodeStatus.DONE,
        "the unrelated node done while the gate is open",
    )
    assert persisted_graph(target_repo).node(PROTO).status is NodeStatus.REVIEW_PENDING  # type: ignore[union-attr]
    # The dependent has not even been dispatched.
    assert node_id("02-wire") not in {spec.node_id for spec in launcher.launched}

    respond(prepared.run, gate, "approve")
    manifest = await run_task
    assert manifest.status is RunStatus.DONE
    assert node_id("02-wire") in {spec.node_id for spec in launcher.launched}


class StatusSpyRun(RunDirectory):
    """Records every status the run writes, in order.

    The RUNNING window between two gates can be milliseconds wide — far too
    narrow to sample off disk — but the writes themselves are the behavior,
    and they are deterministic.
    """

    statuses: ClassVar[list[RunStatus]] = []

    def write_manifest(self, manifest: Manifest) -> None:
        type(self).statuses.append(manifest.status)
        super().write_manifest(manifest)


async def test_the_run_is_gated_only_while_nothing_can_proceed(
    target_repo: Path,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two prototypes gate side by side: the run is GATED. Answering one puts
    it back to RUNNING — partially blocked is not gated — and it gates again
    once only the unanswered node is left."""
    launcher = HarnessLauncher(
        {
            "root": one_turn("Charted."),
            node_id("01-red"): [*one_turn("Built red."), *one_turn("Recorded.")],
            node_id("02-blue"): [*one_turn("Built blue."), *one_turn("Recorded.")],
        },
        {
            "root": [emits_and_completes(EFFORT)],
            node_id("01-red"): [readies("Red ok?"), sends("Approved."), completes()],
            node_id("02-blue"): [readies("Blue ok?"), sends("Approved."), completes()],
        },
        writes={
            "root": [
                charted(
                    ("01-red.md", ticket_body(type_line="prototype")),
                    ("02-blue.md", ticket_body(type_line="prototype")),
                )
            ],
            node_id("01-red"): [{}, {ticket_path("01-red"): PROTO_RESOLVED}],
            node_id("02-blue"): [{}, {ticket_path("02-blue"): PROTO_RESOLVED}],
        },
    )
    monkeypatch.setattr(orchestrate, "GATE_POLL_SECONDS", 0.005)
    monkeypatch.setattr(StatusSpyRun, "statuses", [])
    prepared = prepare_run(a_request(target_repo, state_dir))
    run = StatusSpyRun(prepared.run.path)
    orchestrator = Orchestrator(
        run, prepared.manifest, launcher, announce=lambda _: None
    )
    run_task = asyncio.create_task(orchestrator.execute())

    first = await next_gate(run, 1)
    second = await next_gate(run, 2)
    await eventually(
        lambda: RunStatus.GATED in StatusSpyRun.statuses,
        "the run gated once every node in flight waits on a human",
    )
    stood_open = len(StatusSpyRun.statuses)

    red = first if first.node == node_id("01-red") else second
    respond(run, red, "approve")
    blue = second if red is first else first
    await eventually(
        lambda: RunStatus.GATED in StatusSpyRun.statuses[stood_open:],
        "gated again once only the unanswered gate is left",
    )
    # One answer un-gated the run — partially blocked is not gated — and only
    # the last node left waiting alone gated it again.
    answered_window = StatusSpyRun.statuses[stood_open:]
    assert RunStatus.RUNNING in answered_window
    assert answered_window.index(RunStatus.RUNNING) < answered_window.index(
        RunStatus.GATED
    )

    respond(run, blue, "approve")
    manifest = await run_task
    assert manifest.status is RunStatus.DONE


async def test_an_abort_while_gated_brings_the_run_down(
    target_repo: Path,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A gate is not a lock on the exit: the first interrupt ends the wait."""
    launcher = HarnessLauncher(
        {
            "root": one_turn("Charted."),
            node_id(PROTO): one_turn("Built it."),
        },
        {
            "root": [emits_and_completes(EFFORT)],
            node_id(PROTO): [readies()],
        },
        writes={
            "root": [charted((f"{PROTO}.md", ticket_body(type_line="prototype")))],
        },
    )
    prepared, orchestrator, run_task = a_gated_run(
        target_repo, state_dir, monkeypatch, launcher
    )

    await next_gate(prepared.run, 1)
    orchestrator.request_abort()
    manifest = await run_task
    assert manifest.status is RunStatus.ABORTED


def test_only_a_prototype_node_can_raise_a_review_gate(
    target_repo: Path, state_dir: Path
) -> None:
    """Refused in the tool layer, recorded on the intervention, and the
    refusal does not use the agent's one exclusive move up."""
    launcher = HarnessLauncher(
        {
            "root": one_turn("Charted."),
            node_id("01-index"): one_turn("Implemented."),
        },
        {
            "root": [emits_and_completes(EFFORT)],
            node_id("01-index"): [
                ScriptedAgent(
                    calls=[
                        (
                            PROTOTYPE_READY,
                            {"question": "Ok?", "artifact": "prototype/x.html"},
                        ),
                        ("complete_node", {"summary": "Implemented."}),
                    ]
                )
            ],
        },
        writes={
            "root": [charted(("01-index.md", ticket_body()))],
            node_id("01-index"): [
                {ticket_path("01-index"): ticket_body(status="resolved")}
            ],
        },
    )
    prepared = prepare_run(a_request(target_repo, state_dir))
    manifest = execute_run(prepared, launcher, announce=lambda _: None)
    assert manifest.status is RunStatus.DONE

    refusals = [
        call
        for record in prepared.run.intervention_records()
        for call in record.tool_calls
        if call.tool == PROTOTYPE_READY
    ]
    assert len(refusals) == 1
    assert refusals[0].refused is not None
    assert "prototype" in refusals[0].refused
    assert prepared.run.gate_records() == []


def test_gate_numbering_picks_up_from_what_the_run_directory_holds(
    state_dir: Path,
) -> None:
    """The directory is the review history; a ledger rebuilt over it — the
    resumability path — must never renumber what already happened."""
    run = RunDirectory.create(state_dir, run_directory_manifest())
    ledger = GateLedger(run, clock=lambda: CREATED_AT, announce=lambda _: None)
    ledger.raise_gate(
        kind=GateKind.PROTOTYPE_REVIEW, node="root", question="One?", artifact=None
    )
    ledger.raise_gate(
        kind=GateKind.PROTOTYPE_REVIEW, node="root", question="Two?", artifact=None
    )

    rebuilt = GateLedger(run, clock=lambda: CREATED_AT, announce=lambda _: None)
    third = rebuilt.raise_gate(
        kind=GateKind.PROTOTYPE_REVIEW, node="root", question="Three?", artifact=None
    )
    assert third.sequence == 3
    assert [g.sequence for g in run.gate_records()] == [1, 2, 3]


async def test_nothing_is_decided_before_the_answer_reaches_the_session(
    target_repo: Path,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Delivery is a precondition, not advice: even a node whose tracker
    files are all in place cannot be completed — or parked at a fresh gate —
    while the user's words have not been passed on."""
    launcher = HarnessLauncher(
        {
            "root": one_turn("Charted."),
            node_id(PROTO): [*one_turn("Built and recorded."), *one_turn("Noted.")],
        },
        {
            "root": [emits_and_completes(EFFORT)],
            node_id(PROTO): [
                readies(),
                # The delivery agent tries to skip straight to the verdicts.
                ScriptedAgent(
                    calls=[
                        ("complete_node", {"summary": "Approved, done."}),
                        (
                            PROTOTYPE_READY,
                            {"question": "Again?", "artifact": "prototype/x.html"},
                        ),
                        ("send_to_session", {"message": "Approved — ship it."}),
                    ]
                ),
                completes(),
            ],
        },
        writes={
            "root": [charted((f"{PROTO}.md", ticket_body(type_line="prototype")))],
            # Everything owed is on disk before the gate ever rises.
            node_id(PROTO): [{ticket_path(PROTO): PROTO_RESOLVED}],
        },
    )
    prepared, _, run_task = a_gated_run(target_repo, state_dir, monkeypatch, launcher)

    gate = await next_gate(prepared.run, 1)
    respond(prepared.run, gate, "approve", "Ship it.")
    manifest = await run_task
    assert manifest.status is RunStatus.DONE

    delivery = [
        record
        for record in prepared.run.intervention_records()
        if record.trigger is InterventionTrigger.GATE_RESPONSE
    ]
    assert len(delivery) == 1
    refused = {
        call.tool: call.refused
        for call in delivery[0].tool_calls
        if call.refused is not None
    }
    assert set(refused) == {"complete_node", PROTOTYPE_READY}
    assert all("deliver" in reason for reason in refused.values())
    # Only one gate ever rose, and the session did get the words.
    assert len(prepared.run.gate_records()) == 1
    assert (node_id(PROTO), "Approved — ship it.") in launcher.sent


def a_user_task_charted() -> dict[str, str]:
    return charted((f"{KEYS}.md", ticket_body(type_line="task")))


def emits_a_user_task() -> ScriptedAgent:
    return emits_and_completes(EFFORT, tasks=[{"ticket": KEYS, "mode": "user"}])


async def test_a_done_task_report_lands_in_the_session_and_the_node_completes(
    target_repo: Path,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The task-completion round trip: a user task is dispatched normally,
    its session hits the human-only wall, the gate rises with the ticket as
    its artifact, and the user's facts are delivered into the still-live
    session, which persists them before the node completes."""
    launcher = HarnessLauncher(
        {
            "root": one_turn("Charted."),
            node_id(KEYS): [
                *one_turn("I cannot create the Stripe key myself."),
                *one_turn("Recorded the facts in the ticket."),
            ],
        },
        {
            "root": [emits_a_user_task()],
            node_id(KEYS): [
                hands(),
                sends("Done — the key lives in 1Password under deploy/stripe."),
                completes(),
            ],
        },
        writes={
            "root": [a_user_task_charted()],
            node_id(KEYS): [{}, {ticket_path(KEYS): KEYS_DONE}],
        },
    )
    prepared, _, run_task = a_gated_run(target_repo, state_dir, monkeypatch, launcher)

    gate = await next_gate(prepared.run, 1)
    assert gate.kind is GateKind.TASK_COMPLETION
    assert gate.node == node_id(KEYS)
    assert gate.question == "Create the Stripe key and report where it lives."
    # The ticket itself is the artifact — attached by the harness, not asked
    # of the agent.
    assert gate.artifact == ticket_path(KEYS)

    respond(
        prepared.run,
        gate,
        "done",
        "The key lives in 1Password under deploy/stripe.",
    )
    manifest = await run_task

    assert manifest.status is RunStatus.DONE
    # The node was dispatched normally, through the implement skill.
    dispatched = [s for s in launcher.launched if s.node_id == node_id(KEYS)]
    assert dispatched[0].message == f"/implement {ticket_path(KEYS)}"
    # The facts reached the waiting session and it persisted them.
    assert (
        node_id(KEYS),
        "Done — the key lives in 1Password under deploy/stripe.",
    ) in launcher.sent
    assert "1Password" in (target_repo / ticket_path(KEYS)).read_text()
    assert persisted_graph(target_repo).node(KEYS).status is NodeStatus.DONE  # type: ignore[union-attr]
    assert prepared.run.gate_records()[0].answered_at is not None


async def test_a_done_task_cannot_complete_until_the_facts_are_written(
    target_repo: Path,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The verification half of the acceptance: after delivery, a completion
    is refused while the ticket is still open, and only the session writing
    the facts down unlocks it."""
    launcher = HarnessLauncher(
        {
            "root": one_turn("Charted."),
            node_id(KEYS): [
                *one_turn("I cannot do this myself."),
                *one_turn("Thanks, noted."),  # ...but writes nothing down.
                *one_turn("Recorded in the ticket now."),
            ],
        },
        {
            "root": [emits_a_user_task()],
            node_id(KEYS): [
                hands(),
                sends("Done — key is in 1Password under deploy/stripe."),
                tries_to_complete(then="Please record that in the ticket."),
                completes(),
            ],
        },
        writes={
            "root": [a_user_task_charted()],
            node_id(KEYS): [{}, {}, {ticket_path(KEYS): KEYS_DONE}],
        },
    )
    prepared, _, run_task = a_gated_run(target_repo, state_dir, monkeypatch, launcher)

    gate = await next_gate(prepared.run, 1)
    respond(prepared.run, gate, "done", "Key is in 1Password.")
    manifest = await run_task

    assert manifest.status is RunStatus.DONE
    refused = [
        call
        for record in prepared.run.intervention_records()
        for call in record.tool_calls
        if call.tool == "complete_node" and call.refused is not None
    ]
    assert len(refused) == 1
    assert "Status" in refused[0].refused  # type: ignore[operator]
    assert (node_id(KEYS), "Please record that in the ticket.") in launcher.sent


async def test_a_cannot_response_fails_the_node_cleanly(
    target_repo: Path,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`cannot` is consumed by the loop (ADR-0008): the node fails with the
    user's words as its account, nothing is delivered, its dependents stay
    blocked, and the rest of the run carries on."""
    launcher = HarnessLauncher(
        {
            "root": one_turn("Charted."),
            node_id(KEYS): one_turn("I cannot create the Stripe key myself."),
            node_id("03-docs"): one_turn("Documented."),
        },
        {
            "root": [
                emits_and_completes(
                    EFFORT, tasks=[{"ticket": KEYS, "mode": "user"}]
                )
            ],
            node_id(KEYS): [hands()],
            node_id("03-docs"): [completes()],
        },
        writes={
            "root": [
                charted(
                    (f"{KEYS}.md", ticket_body(type_line="task")),
                    ("02-wire.md", ticket_body(blocked_by=KEYS)),
                    ("03-docs.md", ticket_body()),
                )
            ],
            node_id("03-docs"): [resolves("03-docs")],
        },
    )
    prepared, _, run_task = a_gated_run(target_repo, state_dir, monkeypatch, launcher)

    gate = await next_gate(prepared.run, 1)
    respond(prepared.run, gate, "cannot", "We lost the Stripe account.")
    manifest = await run_task

    assert manifest.status is RunStatus.FAILED
    keys = persisted_graph(target_repo).node(KEYS)
    assert keys is not None
    assert keys.status is NodeStatus.FAILED
    assert keys.gate is None
    # The user's words are the failure's account.
    records = {r.node: r for r in prepared.run.session_records()}
    summary = records[node_id(KEYS)].summary
    assert summary is not None
    assert "cannot be done" in summary
    assert "We lost the Stripe account." in summary
    # Nothing was delivered — no gate-response intervention ever ran — and
    # the gate is stamped answered all the same.
    assert all(
        r.trigger is InterventionTrigger.STALE
        for r in prepared.run.intervention_records()
    )
    assert (node_id(KEYS), "We lost the Stripe account.") not in launcher.sent
    assert prepared.run.gate_records()[0].answered_at is not None
    # The dependent never dispatched; the unrelated node finished anyway.
    assert node_id("02-wire") not in {s.node_id for s in launcher.launched}
    assert persisted_graph(target_repo).node("03-docs").status is NodeStatus.DONE  # type: ignore[union-attr]


def test_only_a_user_task_can_be_handed_to_the_user(
    target_repo: Path, state_dir: Path
) -> None:
    """An agent-classified ticket's session is nudged, never handed over."""
    launcher = HarnessLauncher(
        {
            "root": one_turn("Charted."),
            node_id("01-index"): one_turn("Implemented."),
        },
        {
            "root": [emits_and_completes(EFFORT)],
            node_id("01-index"): [
                ScriptedAgent(
                    calls=[
                        (HAND_TO_USER, {"question": "Please do this yourself."}),
                        ("complete_node", {"summary": "Implemented."}),
                    ]
                )
            ],
        },
        writes={
            "root": [charted(("01-index.md", ticket_body()))],
            node_id("01-index"): [resolves("01-index")],
        },
    )
    prepared = prepare_run(a_request(target_repo, state_dir))
    manifest = execute_run(prepared, launcher, announce=lambda _: None)
    assert manifest.status is RunStatus.DONE

    refusals = [
        call
        for record in prepared.run.intervention_records()
        for call in record.tool_calls
        if call.tool == HAND_TO_USER
    ]
    assert len(refusals) == 1
    assert refusals[0].refused is not None
    assert "user" in refusals[0].refused
    assert prepared.run.gate_records() == []


async def test_an_escalated_answer_is_delivered_into_the_still_idle_session(
    target_repo: Path,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The answer policy's escape hatch: the planning session waits, alive,
    and the user's answer arrives as an ordinary turn of its conversation."""
    launcher = HarnessLauncher(
        {
            "root": [
                *one_turn("Stripe or Braintree? This locks the vendor in."),
                *one_turn("Charted."),
            ],
        },
        {
            "root": [
                escalates("Stripe or Braintree? Vendor lock-in either way."),
                sends("Stripe — we already run our billing there."),
                emits_and_completes(EFFORT),
            ],
        },
        writes={
            "root": [{}, charted(("01-index.md", ticket_body(status="resolved")))],
        },
    )
    prepared, _, run_task = a_gated_run(target_repo, state_dir, monkeypatch, launcher)

    gate = await next_gate(prepared.run, 1)
    assert gate.kind is GateKind.ESCALATED_QUESTION
    assert gate.node == "root"
    assert gate.artifact is None

    respond(prepared.run, gate, "answer", "Stripe. We already have the account.")
    manifest = await run_task

    assert manifest.status is RunStatus.DONE
    # The answer reached the waiting session as an ordinary message...
    assert ("root", "Stripe — we already run our billing there.") in launcher.sent
    # ...through a delivery intervention carrying the user's words verbatim.
    delivery = [
        spec
        for spec in launcher.interventions
        if "Stripe. We already have the account." in spec.message
    ]
    assert len(delivery) == 1
    assert prepared.run.gate_records()[0].answered_at is not None


def test_a_ping_is_run_level_blocks_nothing_and_needs_no_answer(
    target_repo: Path, state_dir: Path
) -> None:
    """The run finishes with the ping still standing: nothing polls it, and
    dismissing it is the website's business, not the harness's."""
    announce: list[str] = []
    launcher = HarnessLauncher(
        {"root": one_turn("Charted."), node_id("01-index"): one_turn()},
        {
            "root": [
                ScriptedAgent(
                    calls=[
                        (
                            PING_USER,
                            {"message": "Heads up: I chose SQLite over Postgres."},
                        ),
                        ("emit_graph", {"effort": EFFORT}),
                        ("complete_node", {"summary": "Charted."}),
                    ]
                )
            ],
            node_id("01-index"): [completes()],
        },
        writes={
            "root": [charted(("01-index.md", ticket_body()))],
            node_id("01-index"): [resolves("01-index")],
        },
    )
    prepared = prepare_run(a_request(target_repo, state_dir))
    manifest = execute_run(prepared, launcher, announce=announce.append)

    # The run never waited: no response file was ever written.
    assert manifest.status is RunStatus.DONE
    gates = prepared.run.gate_records()
    assert [g.kind for g in gates] == [GateKind.USER_PING]
    assert gates[0].node is None
    assert gates[0].gate_id.endswith("-run")
    assert gates[0].question == "Heads up: I chose SQLite over Postgres."
    assert gates[0].answered_at is None
    assert any("I chose SQLite" in ping and "dismiss" in ping for ping in announce)


async def test_a_decision_the_gates_kind_cannot_accept_is_not_yet_an_answer(
    target_repo: Path,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`revise` means nothing at a task-completion gate: the poll treats it
    as malformed (ADR-0007) and the node keeps waiting for a real answer."""
    launcher = HarnessLauncher(
        {
            "root": one_turn("Charted."),
            node_id(KEYS): one_turn("I cannot do this myself."),
        },
        {
            "root": [emits_a_user_task()],
            node_id(KEYS): [hands()],
        },
        writes={"root": [a_user_task_charted()]},
    )
    prepared, _, run_task = a_gated_run(target_repo, state_dir, monkeypatch, launcher)

    gate = await next_gate(prepared.run, 1)
    respond(prepared.run, gate, "revise", "combine A and C")
    await asyncio.sleep(0.1)  # many poll intervals
    assert persisted_graph(target_repo).node(KEYS).status is NodeStatus.REVIEW_PENDING  # type: ignore[union-attr]

    respond(prepared.run, gate, "cannot")
    manifest = await run_task
    assert manifest.status is RunStatus.FAILED
    assert persisted_graph(target_repo).node(KEYS).status is NodeStatus.FAILED  # type: ignore[union-attr]
