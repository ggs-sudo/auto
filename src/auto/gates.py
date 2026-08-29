"""Raising gates, and reading the answers that arrive beside them.

A gate is a pause nobody has answered yet, numbered run-wide so the gates
directory reads as history: a revision never reopens a gate, it gets a fresh
one, and round one's question survives to be read during round three.

The write split is absolute and this module keeps to it: the ledger writes
gate files and stamps answers onto them, and it never writes a response —
those are the website's (or, until it exists, a human hand's), and the
orchestrator only reads them. Raising a gate is also where the user is
pinged, because a review nobody knows to give is a run stalled for nothing.

Everything here runs on the orchestrator's loop thread, like every other
run-directory write.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime

from auto.model import Gate, GateKind, GateResponse
from auto.run import RunDirectory

Announce = Callable[[str], None]

_UNSAFE_IN_A_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")

PING = """\
gate {gate_id} ({kind}) — node {node} is waiting on you:
  {question}
  look at: {artifact}
  answer: write {response_path}"""


class GateLedger:
    """Every gate one run has raised, and the numbering that orders them."""

    def __init__(
        self,
        run: RunDirectory,
        *,
        clock: Callable[[], datetime],
        announce: Announce,
    ) -> None:
        self._run = run
        self._clock = clock
        self._announce = announce
        # Numbering picks up from whatever the directory already holds: the
        # gates on disk are the review history, and a ledger rebuilt over an
        # old run — the resumability path — must never renumber it.
        self._sequence = max(
            (gate.sequence for gate in run.gate_records()), default=0
        )

    def raise_gate(
        self, *, kind: GateKind, node: str, question: str, artifact: str | None
    ) -> Gate:
        """Persist a fresh gate and ping the user that a run is waiting."""
        self._sequence += 1
        gate = Gate(
            gate_id=f"{self._sequence:04d}-{_UNSAFE_IN_A_FILENAME.sub('-', node)}",
            sequence=self._sequence,
            kind=kind,
            node=node,
            question=question,
            artifact=artifact,
            raised_at=self._clock(),
        )
        self._run.write_gate(gate)
        self._announce(
            PING.format(
                gate_id=gate.gate_id,
                kind=kind.value,
                node=node,
                question=question,
                artifact=artifact if artifact is not None else "(nothing attached)",
                response_path=self._run.gate_response_path(gate.gate_id),
            )
        )
        return gate

    def response_to(self, gate: Gate) -> GateResponse | None:
        """The answer beside the gate, or None while nobody has written one."""
        return self._run.read_gate_response(gate.gate_id)

    def record_answered(self, gate: Gate) -> None:
        """Stamp the moment the orchestrator took the response up to deliver."""
        gate.answered_at = self._clock()
        self._run.write_gate(gate)
