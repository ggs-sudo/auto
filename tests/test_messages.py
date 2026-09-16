"""User messages: the user writing first, into a session or to the orchestrator.

The run below the launcher seam is real — the loop, the tools, every write —
and the messages are written the way the website writes them, through
`send_message`, against the live orchestrator this test is running (ADR-0014).
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

import auto.orchestrate as orchestrate
from auto.fixture import LIVE_RUN_ID, Fixture, generate
from auto.model import (
    InterventionTrigger,
    Liveness,
    MessageTarget,
    NodeStatus,
    RunStatus,
    UserMessage,
)
from auto.orchestrate import (
    GATE_PARKED_REFUSAL,
    SESSION_REPLACED_REFUSAL,
    Orchestrator,
    PreparedRun,
    prepare_run,
)
from auto.run import RunDirectory, load_run
from auto.session.events import USER_MESSAGE_KEY
from auto.web.app import create_app
from auto.web.message import send_message
from auto.web.reads import load_run_graphs
from tests.agents import HarnessLauncher, ScriptedAgent, completes, says_nothing, sends
from tests.conftest import dead_pid, one_turn, serving_turn
from tests.test_gates import escalates, respond
from tests.test_orchestrate import a_request


@pytest.fixture(autouse=True)
def quick_polls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fast looks, and graces long enough that nothing expires under test."""
    monkeypatch.setattr(orchestrate, "STREAM_POLL_SECONDS", 0.005)
    monkeypatch.setattr(orchestrate, "MESSAGE_POLL_SECONDS", 0.005)
    monkeypatch.setattr(orchestrate, "GATE_POLL_SECONDS", 0.005)
    monkeypatch.setattr(orchestrate, "IDLE_GRACE_SECONDS", 60.0)
    monkeypatch.setattr(orchestrate, "BACKGROUND_GRACE_SECONDS", 600.0)


async def eventually(check: Callable[[], bool], what: str) -> None:
    deadline = asyncio.get_running_loop().time() + 10.0
    while not check():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"never came true: {what}")
        await asyncio.sleep(0.005)


def a_live_run(
    target_repo: Path, state_dir: Path, launcher: HarnessLauncher
) -> tuple[PreparedRun, asyncio.Task[Any]]:
    prepared = prepare_run(a_request(target_repo, state_dir))
    orchestrator = Orchestrator(
        prepared.run, prepared.manifest, launcher, announce=lambda _: None
    )
    return prepared, asyncio.create_task(orchestrator.execute())


async def root_session(run: RunDirectory) -> str:
    """The root's session id, once the loop has dispatched and persisted it."""
    found: list[str] = []

    def dispatched() -> bool:
        node = run.read_manifest().root_node
        if node.session_id is not None and node.status is NodeStatus.IN_PROGRESS:
            found[:] = [node.session_id]
        return bool(found) and run.liveness_path.is_file()

    await eventually(dispatched, "the root dispatched")
    return found[0]


async def write(
    prepared: PreparedRun,
    target: MessageTarget,
    text: str,
    *,
    session_id: str | None = None,
) -> UserMessage:
    """What the website does: validate against the live run, then write."""
    session = session_id or await root_session(prepared.run)
    state_dir = prepared.run.path.parent.parent
    return send_message(
        state_dir,
        prepared.run.run_id,
        {"target": target.value, "node": "root", "session_id": session, "text": text},
    )


def write_raw(run: RunDirectory, message: UserMessage) -> None:
    """A message the website's own checks would have refused, as a hand writes it."""
    run.messages_dir.mkdir(parents=True, exist_ok=True)
    run.message_path(message.message_id).write_text(message.model_dump_json())


async def closed(run: RunDirectory, message: UserMessage) -> Any:
    await eventually(
        lambda: run.read_message_delivery(message.message_id) is not None,
        f"message {message.message_id} closed",
    )
    return run.read_message_delivery(message.message_id)


def holding_launcher(*agents: ScriptedAgent) -> HarnessLauncher:
    """A root session whose turn ends with a background task that never reports.

    Its turn end is judged at once; an agent that reads the session as waiting
    on that task leaves the boundary held, for the full grace, and a message
    from the user is what moves it along sooner.
    """
    return HarnessLauncher(
        {"root": serving_turn("Built it; the dev server is up.", "b-serve")},
        agents,
        writes=[
            {
                ".scratch/add-search/map.md": "## Destination\n",
                ".scratch/add-search/issues/01-index.md": "# 01\n",
            }
        ],
    )


async def judged(run: RunDirectory, count: int) -> None:
    """Wait until this many interventions have run to their end."""
    await eventually(
        lambda: len(run.intervention_records()) >= count
        and all(r.ended_at is not None for r in run.intervention_records()),
        f"{count} intervention(s) judged",
    )


async def test_a_message_to_the_orchestrator_unsticks_a_held_turn_end(
    target_repo: Path, state_dir: Path
) -> None:
    launcher = holding_launcher(
        says_nothing("Taking the server for a build still running; leaving it."),
        completes("The user confirmed the build is done."),
    )
    prepared, run_task = a_live_run(target_repo, state_dir, launcher)
    await judged(prepared.run, 1)

    message = await write(
        prepared, MessageTarget.ORCHESTRATOR, "It's finished, mark it done."
    )
    manifest = await asyncio.wait_for(run_task, 10.0)

    assert manifest.status is RunStatus.DONE
    stale, record = prepared.run.intervention_records()
    assert stale.trigger is InterventionTrigger.STALE
    assert record.trigger is InterventionTrigger.USER_MESSAGE
    assert record.message_id == message.message_id
    delivery = prepared.run.read_message_delivery(message.message_id)
    assert delivery is not None and delivery.refused is None
    assert delivery.intervention_id == record.intervention_id

    # The agent was told what the user said, and the truth about the session.
    spec = launcher.interventions[-1]
    assert "It's finished, mark it done." in spec.message
    assert "background task(s) still outstanding" in spec.message
    assert "- `local_bash`: serve b-serve" in spec.message
    # The held turn's cost landed once, not once per judgment.
    assert manifest.driven_spend_usd == pytest.approx(0.42)


async def test_a_silent_reply_to_the_user_leaves_the_node_where_it_was(
    target_repo: Path, state_dir: Path
) -> None:
    launcher = holding_launcher(
        says_nothing("Taking the server for a build still running; leaving it."),
        says_nothing("It is waiting on its dev server; nothing to do yet."),
        completes(),
    )
    prepared, run_task = a_live_run(target_repo, state_dir, launcher)
    await judged(prepared.run, 1)

    first = await write(prepared, MessageTarget.ORCHESTRATOR, "What is it doing?")
    await closed(prepared.run, first)
    await asyncio.sleep(0.05)
    assert not run_task.done(), "a question to the orchestrator never ends the node"
    assert prepared.run.read_manifest().root_node.status is NodeStatus.IN_PROGRESS

    await write(prepared, MessageTarget.ORCHESTRATOR, "Fine, complete it.")
    manifest = await asyncio.wait_for(run_task, 10.0)

    assert manifest.status is RunStatus.DONE
    assert [r.trigger for r in prepared.run.intervention_records()] == [
        InterventionTrigger.STALE,
        InterventionTrigger.USER_MESSAGE,
        InterventionTrigger.USER_MESSAGE,
    ]
    assert manifest.driven_spend_usd == pytest.approx(0.42)


async def test_a_message_into_the_session_goes_in_at_its_turn_end_and_is_transcribed(
    target_repo: Path, state_dir: Path
) -> None:
    """The session's idle after a silent stale intervention; the user's words
    wake it, show in its transcript as theirs, and the next turn is judged."""
    launcher = HarnessLauncher(
        {"root": [*one_turn("Built. Waiting for review."), *one_turn("Recorded.")]},
        [says_nothing("Waiting on the user."), completes()],
        writes=[
            {
                ".scratch/add-search/map.md": "## Destination\n",
                ".scratch/add-search/issues/01-index.md": "# 01\n",
            }
        ],
    )
    prepared, run_task = a_live_run(target_repo, state_dir, launcher)
    await eventually(
        lambda: len(prepared.run.intervention_records()) == 1
        and prepared.run.intervention_records()[0].ended_at is not None,
        "the first stale point judged",
    )

    message = await write(prepared, MessageTarget.SESSION, "approved")
    manifest = await asyncio.wait_for(run_task, 10.0)

    assert manifest.status is RunStatus.DONE
    assert ("root", "approved") in launcher.sent
    delivery = prepared.run.read_message_delivery(message.message_id)
    assert delivery is not None and delivery.refused is None

    session_id = prepared.run.session_records()[0].session_id
    lines = [
        json.loads(line)
        for line in prepared.run.transcript_path(session_id).read_text().splitlines()
    ]
    [marked] = [line for line in lines if USER_MESSAGE_KEY in line]
    assert marked[USER_MESSAGE_KEY] == message.message_id
    assert marked["message"]["content"][0]["text"] == "approved"

    # The judgment after it saw the user's words as the user's.
    assert "the user, directly: approved" in launcher.interventions[-1].message


async def test_a_message_against_a_replaced_session_is_never_delivered(
    target_repo: Path, state_dir: Path
) -> None:
    launcher = holding_launcher(
        says_nothing("Taking the server for a build still running; leaving it."),
        completes(),
    )
    prepared, run_task = a_live_run(target_repo, state_dir, launcher)
    session = await root_session(prepared.run)
    await judged(prepared.run, 1)

    stale = UserMessage(
        message_id="20260101T000000000000-aaaaaa",
        node="root",
        session_id="an-earlier-session",
        target=MessageTarget.SESSION,
        text="approved",
        sent_at=datetime.now(UTC),
    )
    write_raw(prepared.run, stale)
    delivery = await closed(prepared.run, stale)
    assert delivery.refused == SESSION_REPLACED_REFUSAL.format(
        session="an-earlier-session", node="root"
    )
    assert launcher.sent == []

    await write(
        prepared, MessageTarget.ORCHESTRATOR, "Complete it.", session_id=session
    )
    assert (await asyncio.wait_for(run_task, 10.0)).status is RunStatus.DONE


async def test_a_node_parked_at_a_gate_refuses_messages_for_its_gate(
    target_repo: Path, state_dir: Path
) -> None:
    launcher = HarnessLauncher(
        {"root": [*one_turn("Postgres or SQLite?"), *one_turn("Using Postgres.")]},
        [escalates("Postgres or SQLite?"), sends("Postgres."), completes()],
        writes=[
            {
                ".scratch/add-search/map.md": "## Destination\n",
                ".scratch/add-search/issues/01-index.md": "# 01\n",
            }
        ],
    )
    prepared, run_task = a_live_run(target_repo, state_dir, launcher)
    await eventually(lambda: bool(prepared.run.gate_records()), "the gate raised")
    [gate] = prepared.run.gate_records()
    session = prepared.run.read_manifest().root_node.session_id
    assert session is not None

    # The website refuses up front...
    with pytest.raises(Exception, match="answer the gate"):
        await write(prepared, MessageTarget.SESSION, "Postgres.", session_id=session)
    # ...and the loop refuses what reaches it anyway.
    raw = UserMessage(
        message_id="20260101T000000000000-bbbbbb",
        node="root",
        session_id=session,
        target=MessageTarget.SESSION,
        text="Postgres.",
        sent_at=datetime.now(UTC),
    )
    write_raw(prepared.run, raw)
    delivery = await closed(prepared.run, raw)
    assert delivery.refused == GATE_PARKED_REFUSAL.format(
        node="root", gate=gate.gate_id
    )

    respond(prepared.run, gate, "answer", "Postgres.")
    assert (await asyncio.wait_for(run_task, 10.0)).status is RunStatus.DONE
    assert launcher.sent == [("root", "Postgres.")]


# --- the endpoint -----------------------------------------------------------


@pytest.fixture
def fixture(tmp_path: Path) -> Fixture:
    return generate(tmp_path)


@pytest.fixture
def client(fixture: Fixture) -> TestClient:
    return TestClient(create_app(fixture.state_dir))


def hold_live(fixture: Fixture, pid: int | None = None) -> RunDirectory:
    run = load_run(fixture.state_dir, LIVE_RUN_ID)
    now = datetime.now(UTC)
    run.write_liveness(
        Liveness(
            pid=os.getpid() if pid is None else pid,
            started_at=now,
            heartbeat_at=now,
        )
    )
    return run


def nodes_of(run: RunDirectory) -> list[tuple[str, Any]]:
    manifest = run.read_manifest()
    return [("root", manifest.root_node)] + [
        (f"{graph.graph_id}/{node.node_id}", node)
        for graph in load_run_graphs(manifest)
        for node in graph.nodes
    ]


MESSAGES = f"/api/runs/{LIVE_RUN_ID}/messages"


def test_a_message_writes_one_file_and_shows_in_the_run(
    client: TestClient, fixture: Fixture
) -> None:
    run = hold_live(fixture)
    node, state = next(
        (key, n)
        for key, n in nodes_of(run)
        if n.status is NodeStatus.IN_PROGRESS and n.gate is None and n.session_id
    )
    before = set(run.path.rglob("*"))

    posted = client.post(
        MESSAGES,
        json={
            "target": "orchestrator",
            "node": node,
            "session_id": state.session_id,
            "text": "Raise the review.",
        },
    )

    assert posted.status_code == 201
    written = posted.json()
    path = run.message_path(written["message_id"])
    assert set(run.path.rglob("*")) - before == {run.messages_dir, path}
    [shown] = client.get(f"/api/runs/{LIVE_RUN_ID}").json()["messages"]
    assert shown["text"] == "Raise the review."
    assert shown["delivery"] is None


def test_messages_nothing_could_deliver_are_refused(
    client: TestClient, fixture: Fixture
) -> None:
    run = hold_live(fixture)
    nodes = nodes_of(run)
    live_key, live = next(
        (k, n)
        for k, n in nodes
        if n.status is NodeStatus.IN_PROGRESS and n.gate is None
    )
    gated_key, gated = next((k, n) for k, n in nodes if n.gate is not None)

    def post(**over: Any) -> Any:
        body = {
            "target": "session",
            "node": live_key,
            "session_id": live.session_id,
            "text": "hello",
            **over,
        }
        return client.post(MESSAGES, json=body)

    assert post(node=gated_key, session_id=gated.session_id or "x").status_code == 409
    assert "answer the gate" in post(node=gated_key).json()["error"]
    assert post(session_id="someone-else").status_code == 409
    assert post(text="   ").status_code == 400
    assert post(target="nobody").status_code == 400
    assert post(node="effort/no-such-node").status_code == 404

    hold_live(fixture, pid=dead_pid())
    refused = post()
    assert refused.status_code == 409
    assert "no live orchestrator" in refused.json()["error"]
    assert not run.messages_dir.exists()
