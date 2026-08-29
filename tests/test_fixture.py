"""The fixture generator: a run directory the schemas can vouch for.

The generator writes through the Pydantic models, so these tests validate the
files it produces against the checked-in `schemas/` with an independent
validator — the claim "validated against the generated schemas" should not
rest on the same library that wrote the files.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from auto.fixture import CRASHED_RUN_ID, DONE_RUN_ID, LIVE_RUN_ID, Fixture, generate
from auto.model import Gate, GateKind, Graph, Manifest, SessionRecord, SessionStatus
from auto.run import RESPONSE_SUFFIX

SCHEMAS_DIR = Path(__file__).resolve().parents[1] / "schemas"


@pytest.fixture(scope="module")
def fixture(tmp_path_factory: pytest.TempPathFactory) -> Fixture:
    return generate(tmp_path_factory.mktemp("fixture"))


def _validator(name: str) -> Draft202012Validator:
    schema = json.loads((SCHEMAS_DIR / name).read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def _validate_all(paths: list[Path], schema: str) -> None:
    validator = _validator(schema)
    assert paths, f"nothing to validate against {schema}"
    for path in paths:
        errors = list(
            validator.iter_errors(json.loads(path.read_text(encoding="utf-8")))
        )
        assert not errors, f"{path} violates {schema}: {errors[0].message}"


def test_every_file_validates_against_the_checked_in_schemas(
    fixture: Fixture,
) -> None:
    runs = fixture.state_dir / "runs"
    gate_files = [
        p
        for p in runs.glob("*/gates/*.json")
        if not p.name.endswith(RESPONSE_SUFFIX)
    ]
    _validate_all(list(runs.glob("*/run.json")), "manifest.schema.json")
    _validate_all(list(runs.glob("*/sessions/*.json")), "session-record.schema.json")
    _validate_all(list(runs.glob("*/interventions/*.json")), "intervention.schema.json")
    _validate_all(gate_files, "gate.schema.json")
    _validate_all(
        list(runs.glob(f"*/gates/*{RESPONSE_SUFFIX}")), "gate-response.schema.json"
    )
    _validate_all(
        list((fixture.target_repo / ".scratch").glob("*/graph.json")),
        "graph.schema.json",
    )


def test_the_live_run_covers_a_subgraph_and_all_four_gate_kinds(
    fixture: Fixture,
) -> None:
    run_dir = fixture.state_dir / "runs" / LIVE_RUN_ID
    manifest = Manifest.model_validate_json(
        (run_dir / "run.json").read_text(encoding="utf-8")
    )
    root_graph = Graph.model_validate_json(
        (fixture.target_repo / ".scratch" / str(manifest.root_node.graph) / "graph.json")
        .read_text(encoding="utf-8")
    )
    spawned = [n.graph for n in root_graph.nodes if n.graph is not None]
    assert spawned, "the live run's graph spawns no subgraph"
    subgraph = Graph.model_validate_json(
        (fixture.target_repo / ".scratch" / spawned[0] / "graph.json")
        .read_text(encoding="utf-8")
    )
    assert subgraph.spawned_by.startswith(f"{root_graph.graph_id}/")

    gates = [
        Gate.model_validate_json(p.read_text(encoding="utf-8"))
        for p in sorted((run_dir / "gates").glob("*.json"))
        if not p.name.endswith(RESPONSE_SUFFIX)
    ]
    assert {g.kind for g in gates} == set(GateKind)
    ping = next(g for g in gates if g.kind is GateKind.USER_PING)
    assert ping.node is None, "a ping is run-level and waits on no node"


def test_headline_facts_sit_on_a_still_running_session(fixture: Fixture) -> None:
    run_dir = fixture.state_dir / "runs" / LIVE_RUN_ID
    records = [
        SessionRecord.model_validate_json(p.read_text(encoding="utf-8"))
        for p in (run_dir / "sessions").glob("*.json")
    ]
    running = [r for r in records if r.status is SessionStatus.RUNNING and r.highlights]
    assert running, "no running session carries headline facts"


def test_the_crashed_run_stops_mid_turn(fixture: Fixture) -> None:
    transcripts = fixture.state_dir / "runs" / CRASHED_RUN_ID / "transcripts"
    lines = [
        json.loads(line)
        for path in transcripts.glob("*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert lines and lines[-1]["type"] != "result"


def test_regeneration_is_deterministic(tmp_path: Path) -> None:
    """Same root, same bytes: fixed timestamps, no randomness, idempotent.

    The manifests embed the target repo's absolute path, so determinism is
    per-root — which is all a stable test or screenshot needs.
    """
    first = generate(tmp_path)
    assert first.run_ids == (LIVE_RUN_ID, DONE_RUN_ID, CRASHED_RUN_ID)
    before = {
        path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    }
    generate(tmp_path)
    after = {
        path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    }
    assert before == after
