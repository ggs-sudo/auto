"""The website's one destructive write: deleting a run.

`respond.py` adds a file beside a gate; this takes a whole run directory
away, which makes the concurrency question sharper rather than different.
The rule is takeover's, reused: a run held by a *live* orchestrator is never
deleted — removing the directory out from under the process still recording
into it would corrupt the run mid-write — while a run whose `running` claim
has a dead pid behind it is a crashed run, exactly the kind worth clearing
off the rail. A manifest the current schema cannot read claims nothing, so it
deletes too; an unreadable run is otherwise undeletable from the website.

Only the run's state directory goes. The graphs and tickets under the target
repo's `.scratch/` are the repo's content, derived beside the tickets they
belong to and not the harness's to remove, so a deleted run leaves them
exactly as they were.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from auto.errors import AutoError, UsageError
from auto.liveness import LIVE_CLAIMS, orchestrator_alive
from auto.run import load_run


class RunIsLive(AutoError):
    """A live orchestrator holds this run; it is refused, not raced."""


def delete_run(state_dir: Path, run_id: str) -> None:
    """Remove one run's state directory.

    Raises `UsageError` for a run that does not exist, and `RunIsLive` for a
    run an orchestrator process is still writing.
    """
    run = load_run(state_dir, run_id)
    try:
        status = run.read_manifest().status
    except UsageError:
        status = None
    liveness = run.read_liveness()
    if status in LIVE_CLAIMS and orchestrator_alive(liveness):
        assert liveness is not None  # a dead None would not be alive
        raise RunIsLive(
            f"run {run_id} is held by a live orchestrator (pid {liveness.pid}); "
            "stop it before deleting the run"
        )
    shutil.rmtree(run.path)
