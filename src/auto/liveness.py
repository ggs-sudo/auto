"""Whether the orchestrator behind a run is actually alive.

A manifest saying `running` is a claim, not a fact: the process behind it may
have crashed without writing a terminal status, and a claim nothing checks
would be believed forever. The orchestrator records its pid, and refreshes a
heartbeat, in the run directory; readers here check that pid against the
process table — a dead process is a fact, not a guess from timestamps.

Prefactor for takeover's concurrency guard: `running`-but-dead is a crashed
run, the prime takeover candidate, while a live pid refuses a second writer.
"""

from __future__ import annotations

import os

from auto.model import Liveness, Manifest, RunStatus

HEARTBEAT_SECONDS = 5.0
"""How often the loop refreshes `liveness.json` while it runs."""

CRASHED = "crashed"
"""The observed status of a run whose manifest claims a live orchestrator that
is not there. Never persisted: it is a verdict about the manifest, computed
fresh by whoever reads the run."""

LIVE_CLAIMS = frozenset({RunStatus.RUNNING, RunStatus.GATED})
"""The manifest statuses that claim an orchestrator process holds the run.
Everything else is terminal and stands on its own."""


def pid_alive(pid: int) -> bool:
    """Whether any process holds this pid. Signal 0 probes without touching.

    Any process: pid numbers are recycled, so a long-dead orchestrator's pid
    can be somebody else's by now. Alive-at-all is the ceiling of what a bare
    pid can say — takeover's guard, which needs a *matching* live
    orchestrator, will have to check more than the number.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # not ours to signal, but somebody's — alive
    return True


def orchestrator_alive(liveness: Liveness | None) -> bool:
    """Whether the orchestrator that recorded this liveness still runs.

    No record is a dead orchestrator: a live one writes its liveness before it
    drives anything, so a run claiming `running` with nothing behind the claim
    — a crash in the gap, or a run predating liveness — has no live loop
    either way.
    """
    return liveness is not None and pid_alive(liveness.pid)


def observed_status(manifest: Manifest, liveness: Liveness | None) -> str:
    """The manifest's status, checked against the process table.

    A terminal status is believed as written. A live claim is verified: when
    the recorded orchestrator is dead, the run crashed — however long ago —
    and is reported so rather than `running`.
    """
    if manifest.status in LIVE_CLAIMS and not orchestrator_alive(liveness):
        return CRASHED
    return manifest.status.value
