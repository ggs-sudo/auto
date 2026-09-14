"""The serve API, driven over the generated fixture run directory.

A directory on disk is already a boundary, so no injection point is
introduced: the tests generate the fixture, stand the app over it, and speak
HTTP. The filesystem watcher is deliberately untested — change detection is
the tracker's tick, and the tests call it directly.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from auto.fixture import CRASHED_RUN_ID, DONE_RUN_ID, LIVE_RUN_ID, Fixture, generate
from auto.gates import GateLedger
from auto.model import Gate, GateKind, Liveness
from auto.run import load_run, write_atomically
from auto.web.app import Broadcast, _sse, create_app
from auto.web.changes import ChangeTracker, RunChange
from auto.web.erase import delete_run


@pytest.fixture
def fixture(tmp_path: Path) -> Fixture:
    return generate(tmp_path)


@pytest.fixture
def client(fixture: Fixture) -> TestClient:
    # Not entered as a context manager: that would run the lifespan and start
    # the watcher, and change detection is tested through the tick directly.
    return TestClient(create_app(fixture.state_dir))


def test_every_run_is_listed_newest_first(client: TestClient) -> None:
    runs = client.get("/api/runs").json()
    assert [run["run_id"] for run in runs] == [
        LIVE_RUN_ID,
        DONE_RUN_ID,
        CRASHED_RUN_ID,
    ]


def test_the_rail_row_carries_counts_gates_and_spend(client: TestClient) -> None:
    live = client.get("/api/runs").json()[0]
    assert live["status"] == "running"
    assert live["route"] == "wayfinder"
    # 1 root + 6 in checkout-revamp + 3 in checkout-flow
    assert live["nodes"] == {
        "total": 10,
        "done": 4,
        "running": 1,
        "review_pending": 3,
        "pending": 2,
        "failed": 0,
    }
    # Four gates rose; the ping was answered.
    assert live["open_gates"] == 3
    assert live["spend_usd"] == pytest.approx(4.8312 + 0.4105)


def test_run_detail_holds_the_whole_run(client: TestClient) -> None:
    detail = client.get(f"/api/runs/{LIVE_RUN_ID}").json()
    assert detail["manifest"]["run_id"] == LIVE_RUN_ID
    assert [graph["graph_id"] for graph in detail["graphs"]] == [
        "checkout-revamp",
        "checkout-flow",
    ]
    subgraph = detail["graphs"][1]
    assert subgraph["spawned_by"] == "checkout-revamp/0002-grill-checkout-flow"
    assert len(detail["sessions"]) == 8
    assert len(detail["interventions"]) == 5
    assert [gate["sequence"] for gate in detail["gates"]] == [1, 2, 3, 4]
    answered = detail["gates"][0]
    assert answered["kind"] == "user-ping"
    assert answered["response"]["decision"] == "dismiss"
    assert all(gate["response"] is None for gate in detail["gates"][1:])
    assert detail["open_gates"] == 3
    assert "version" in detail


def test_headline_facts_are_served_while_a_node_still_runs(
    client: TestClient,
) -> None:
    detail = client.get(f"/api/runs/{LIVE_RUN_ID}").json()
    running = [
        session
        for session in detail["sessions"]
        if session["status"] == "running" and session["highlights"]
    ]
    assert running, "no running session carries headline facts"


def test_finished_and_crashed_runs_stay_viewable(client: TestClient) -> None:
    done = client.get(f"/api/runs/{DONE_RUN_ID}").json()
    assert done["manifest"]["status"] == "done"
    assert done["nodes"]["total"] == 3

    crashed = client.get(f"/api/runs/{CRASHED_RUN_ID}").json()
    assert crashed["manifest"]["status"] == "running"
    assert crashed["graphs"] == []  # it died before charting anything


def test_a_run_that_does_not_exist_is_a_404(client: TestClient) -> None:
    assert client.get("/api/runs/20990101-000000-nope").status_code == 404


def test_the_transcript_tail_is_incremental(
    client: TestClient, fixture: Fixture
) -> None:
    url = f"/api/runs/{LIVE_RUN_ID}/transcripts/sess-import"
    first = client.get(url).json()
    assert [event["type"] for event in first["events"]][:2] == ["system", "assistant"]
    assert first["offset"] > 0

    again = client.get(url, params={"after": first["offset"]}).json()
    assert again["events"] == []
    assert again["offset"] == first["offset"]

    # The session writes more; only what is past the offset comes back.
    path = (
        fixture.state_dir
        / "runs"
        / LIVE_RUN_ID
        / "transcripts"
        / "sess-import.jsonl"
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "assistant", "message": {}}) + "\n")
        handle.write('{"type": "resu')  # a line still being flushed
    tail = client.get(url, params={"after": first["offset"]}).json()
    assert [event["type"] for event in tail["events"]] == ["assistant"]

    # The half-written line was not consumed; completing it serves it.
    with path.open("a", encoding="utf-8") as handle:
        handle.write('lt"}\n')
    finished = client.get(url, params={"after": tail["offset"]}).json()
    assert [event["type"] for event in finished["events"]] == ["result"]


def test_the_tail_rejects_what_it_should(client: TestClient) -> None:
    assert (
        client.get(f"/api/runs/{LIVE_RUN_ID}/transcripts/no-such-session").status_code
        == 404
    )
    assert (
        client.get(
            f"/api/runs/{LIVE_RUN_ID}/transcripts/sess-import",
            params={"after": "not-a-number"},
        ).status_code
        == 400
    )
    traversal = client.get(f"/api/runs/{LIVE_RUN_ID}/transcripts/..%2Frun")
    assert traversal.status_code == 404


def test_the_tick_notices_state_dir_and_graph_changes(fixture: Fixture) -> None:
    tracker = ChangeTracker(fixture.state_dir)
    tracker.tick()  # baseline
    assert tracker.tick() == []

    session_path = (
        fixture.state_dir / "runs" / LIVE_RUN_ID / "sessions" / "sess-import.json"
    )
    record = json.loads(session_path.read_text(encoding="utf-8"))
    record["highlights"].append("full import is running")
    write_atomically(session_path, json.dumps(record, indent=2) + "\n")

    changes = tracker.tick()
    assert [change.run_id for change in changes] == [LIVE_RUN_ID]
    version = changes[0].version
    assert tracker.tick() == []

    # A graph moves in the target repo — outside the state dir entirely.
    graph_path = fixture.target_repo / ".scratch" / "checkout-revamp" / "graph.json"
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    graph["nodes"][5]["status"] = "done"
    write_atomically(graph_path, json.dumps(graph, indent=2) + "\n")

    changes = tracker.tick()
    assert LIVE_RUN_ID in [change.run_id for change in changes]
    live = next(change for change in changes if change.run_id == LIVE_RUN_ID)
    assert live.version == version + 1


async def test_a_change_reaches_every_subscriber_as_a_framed_event() -> None:
    """The stream's own pieces, tested directly: an SSE response never ends,
    so driving it over a test client would block on the open stream."""
    broadcast = Broadcast()
    with broadcast.subscribe() as one, broadcast.subscribe() as two:
        broadcast.publish(RunChange(LIVE_RUN_ID, 7))
        assert await one.get() == RunChange(LIVE_RUN_ID, 7)
        assert await two.get() == RunChange(LIVE_RUN_ID, 7)
    broadcast.publish(RunChange(LIVE_RUN_ID, 8))  # nobody listening: no error

    framed = _sse("change", {"run_id": LIVE_RUN_ID, "version": 7})
    assert framed == (
        'event: change\ndata: {"run_id": "%s", "version": 7}\n\n' % LIVE_RUN_ID
    )


def test_the_site_itself_is_served(client: TestClient) -> None:
    page = client.get("/")
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]


# ---------------------------------------------------------------------------
# Answering gates: the website's one write.

LIVE_GATES = f"/api/runs/{LIVE_RUN_ID}/gates"
REVIEW_GATE = "0003-checkout-revamp-0004-prototype-payment-form"
TASK_GATE = "0002-checkout-revamp-0003-provision-stripe-account"
QUESTION_GATE = "0004-checkout-flow-0002-implement-checkout-page"


def test_answering_writes_the_sibling_file_and_nothing_else(
    client: TestClient, fixture: Fixture
) -> None:
    run_dir = fixture.state_dir / "runs" / LIVE_RUN_ID
    before = {path for path in run_dir.rglob("*")}

    posted = client.post(
        f"{LIVE_GATES}/{REVIEW_GATE}/response",
        json={"decision": "revise", "text": "Combine A and C."},
    )
    assert posted.status_code == 201

    response_path = run_dir / "gates" / f"{REVIEW_GATE}.response.json"
    assert {path for path in run_dir.rglob("*")} - before == {response_path}
    written = json.loads(response_path.read_text(encoding="utf-8"))
    assert written["decision"] == "revise"
    assert written["text"] == "Combine A and C."


def test_a_submitted_response_is_what_the_orchestrators_poll_reads(
    client: TestClient, fixture: Fixture
) -> None:
    client.post(
        f"{LIVE_GATES}/{QUESTION_GATE}/response",
        json={"decision": "answer", "text": "Dual-write for six months."},
    )
    # The poll is GateLedger.response_to: the same read, kind validation and all.
    run = load_run(fixture.state_dir, LIVE_RUN_ID)
    ledger = GateLedger(
        run, clock=lambda: datetime.now(timezone.utc), announce=lambda _: None
    )
    gate = next(g for g in run.gate_records() if g.gate_id == QUESTION_GATE)
    response = ledger.response_to(gate)
    assert response is not None
    assert response.text == "Dual-write for six months."


def test_an_answered_gate_clears_in_the_view(client: TestClient) -> None:
    assert client.get(f"/api/runs/{LIVE_RUN_ID}").json()["open_gates"] == 3
    client.post(
        f"{LIVE_GATES}/{TASK_GATE}/response",
        json={"decision": "done", "text": "Key in 1Password, acct_123."},
    )
    detail = client.get(f"/api/runs/{LIVE_RUN_ID}").json()
    assert detail["open_gates"] == 2
    answered = next(g for g in detail["gates"] if g["gate_id"] == TASK_GATE)
    assert answered["response"]["decision"] == "done"


def test_a_ping_can_be_dismissed(client: TestClient, fixture: Fixture) -> None:
    run = load_run(fixture.state_dir, LIVE_RUN_ID)
    run.write_gate(
        Gate(
            gate_id="0005-run",
            sequence=5,
            kind=GateKind.USER_PING,
            node=None,
            question="Budget half spent.",
            raised_at=datetime.now(timezone.utc),
        )
    )
    posted = client.post(
        f"{LIVE_GATES}/0005-run/response", json={"decision": "dismiss"}
    )
    assert posted.status_code == 201
    assert run.read_gate_response("0005-run") is not None


def test_a_decision_the_kind_cannot_accept_is_refused(
    client: TestClient, fixture: Fixture
) -> None:
    posted = client.post(
        f"{LIVE_GATES}/{REVIEW_GATE}/response",
        json={"decision": "done", "text": "wrong verb for a review"},
    )
    assert posted.status_code == 400
    response_path = (
        fixture.state_dir
        / "runs"
        / LIVE_RUN_ID
        / "gates"
        / f"{REVIEW_GATE}.response.json"
    )
    assert not response_path.exists()


def test_a_gate_answers_exactly_once(client: TestClient) -> None:
    # 0001-run already carries a response and an answered_at stamp — and
    # answered-ness outranks verb validity, so even a wrong verb hears 409.
    assert (
        client.post(
            f"{LIVE_GATES}/0001-run/response", json={"decision": "dismiss"}
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"{LIVE_GATES}/0001-run/response", json={"decision": "revise"}
        ).status_code
        == 409
    )
    first = client.post(
        f"{LIVE_GATES}/{REVIEW_GATE}/response",
        json={"decision": "approve", "text": ""},
    )
    assert first.status_code == 201
    again = client.post(
        f"{LIVE_GATES}/{REVIEW_GATE}/response",
        json={"decision": "revise", "text": "changed my mind"},
    )
    assert again.status_code == 409


def test_answering_rejects_what_it_should(client: TestClient) -> None:
    assert (
        client.post(
            f"/api/runs/20990101-000000-nope/gates/{REVIEW_GATE}/response",
            json={"decision": "approve"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"{LIVE_GATES}/no-such-gate/response", json={"decision": "approve"}
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"{LIVE_GATES}/{REVIEW_GATE}/response", json={"text": "no decision"}
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"{LIVE_GATES}/{REVIEW_GATE}/response",
            json={"decision": "self-destruct"},
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"{LIVE_GATES}/{REVIEW_GATE}/response", content=b"not json"
        ).status_code
        == 400
    )


def test_two_simultaneous_answers_have_one_winner(tmp_path: Path) -> None:
    """The race the answered-ness pre-check cannot see: both submissions pass
    it, and the exclusive write is what keeps the loser from overwriting."""
    from auto.web.respond import GateAlreadyAnswered, _write_exclusively

    target = tmp_path / "0001-node.response.json"
    _write_exclusively(target, '{"decision": "approve"}\n')
    with pytest.raises(GateAlreadyAnswered):
        _write_exclusively(target, '{"decision": "revise"}\n')
    assert json.loads(target.read_text(encoding="utf-8"))["decision"] == "approve"


# ---------------------------------------------------------------------------
# Deleting runs: the website's one destructive write.


def test_deleting_a_run_takes_its_directory_and_nothing_else(
    client: TestClient, fixture: Fixture
) -> None:
    run_dir = fixture.state_dir / "runs" / DONE_RUN_ID
    assert run_dir.is_dir()

    assert client.delete(f"/api/runs/{DONE_RUN_ID}").status_code == 204

    assert not run_dir.exists()
    assert [run["run_id"] for run in client.get("/api/runs").json()] == [
        LIVE_RUN_ID,
        CRASHED_RUN_ID,
    ]
    assert client.get(f"/api/runs/{DONE_RUN_ID}").status_code == 404
    # The effort's graphs and tickets are the target repo's, not the run's.
    assert (fixture.target_repo / ".scratch").is_dir()


def test_a_crashed_run_can_be_cleared_off_the_rail(client: TestClient) -> None:
    """Its manifest still claims `running`; nothing is behind the claim."""
    assert client.get(f"/api/runs/{CRASHED_RUN_ID}").json()["manifest"][
        "status"
    ] == "running"
    assert client.delete(f"/api/runs/{CRASHED_RUN_ID}").status_code == 204


def test_a_run_a_live_orchestrator_holds_is_refused(
    client: TestClient, fixture: Fixture
) -> None:
    run = load_run(fixture.state_dir, LIVE_RUN_ID)
    now = datetime.now(timezone.utc)
    run.write_liveness(Liveness(pid=os.getpid(), started_at=now, heartbeat_at=now))

    refused = client.delete(f"/api/runs/{LIVE_RUN_ID}")
    assert refused.status_code == 409
    assert "live orchestrator" in refused.json()["error"]
    assert run.manifest_path.is_file()


def test_a_run_whose_manifest_cannot_be_read_still_deletes(
    client: TestClient, fixture: Fixture
) -> None:
    """Nothing else can act on it; leaving it on the rail forever is worse."""
    run_dir = fixture.state_dir / "runs" / DONE_RUN_ID
    write_atomically(run_dir / "run.json", "{}\n")
    assert client.get(f"/api/runs/{DONE_RUN_ID}").status_code == 404

    assert client.delete(f"/api/runs/{DONE_RUN_ID}").status_code == 204
    assert not run_dir.exists()


def test_deleting_a_run_that_does_not_exist_is_a_404(
    client: TestClient, fixture: Fixture
) -> None:
    assert client.delete("/api/runs/20990101-000000-nope").status_code == 404
    # A traversal names no run — the route only matches one path segment —
    # so it is refused and nothing under the state directory moves.
    runs_dir = fixture.state_dir / "runs"
    before = set(runs_dir.iterdir())
    assert client.delete("/api/runs/..%2F..%2Fruns").status_code >= 400
    assert set(runs_dir.iterdir()) == before


def test_a_deleted_run_notifies_every_watching_client(fixture: Fixture) -> None:
    tracker = ChangeTracker(fixture.state_dir)
    tracker.tick()  # baseline

    delete_run(fixture.state_dir, DONE_RUN_ID)

    assert [change.run_id for change in tracker.tick()] == [DONE_RUN_ID]
