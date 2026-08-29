"""The website's one write: a gate response, beside its gate.

ADR-0007's process boundary made literal. The server reads the whole run
directory but writes exactly one kind of file — `gates/<gate-id>.response.json`
— which is why answering here is indistinguishable from answering by hand.
The orchestrator's poll does the delivering; nothing here talks to a run.

Validation mirrors the poll's own reading rules (`GateLedger.response_to`):
a decision the gate's kind cannot accept would sit beside the gate forever,
"not yet an answer", so it is refused here instead of written. And a gate
answers exactly once — a response already on disk, or a gate already stamped
answered, turns a second submission into a conflict rather than an overwrite,
because the first answer may already be inside the waiting session.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from auto.errors import AutoError, UsageError
from auto.model import DECISIONS_FOR, GateResponse
from auto.run import load_run, write_atomically


class ResponseRejected(AutoError):
    """The submission itself is unusable — a bad body, or a verb the gate's
    kind cannot accept. Nothing was written."""


class GateAlreadyAnswered(AutoError):
    """The gate already has its answer; this one is refused, not layered on."""


def answer_gate(
    state_dir: Path, run_id: str, gate_id: str, payload: Any
) -> GateResponse:
    """Validate a submission and write it as the gate's sibling file.

    Raises `UsageError` for a run or gate that does not exist,
    `ResponseRejected` for a body or decision the gate cannot take, and
    `GateAlreadyAnswered` for a gate that has one.
    """
    run = load_run(state_dir, run_id)
    gate = next((g for g in run.gate_records() if g.gate_id == gate_id), None)
    if gate is None:
        raise UsageError(f"run {run_id} has no gate {gate_id}")
    try:
        response = GateResponse.model_validate(payload)
    except ValidationError as exc:
        raise ResponseRejected(str(exc)) from exc
    if response.decision not in DECISIONS_FOR[gate.kind]:
        raise ResponseRejected(
            f"a {gate.kind.value} gate cannot take `{response.decision.value}`"
        )
    if gate.answered_at is not None or run.gate_response_path(gate_id).exists():
        raise GateAlreadyAnswered(f"gate {gate_id} already has its answer")
    write_atomically(
        run.gate_response_path(gate_id), response.model_dump_json(indent=2) + "\n"
    )
    return response
