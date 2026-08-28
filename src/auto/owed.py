"""What a node type owes, and what of it is still missing.

The harness knows what each node type should leave behind — its **tracker
files** and the edits inside them. The session never does, and is never told:
a dispatch is the entry skill's invocation and its ticket and nothing else, so
every obligation in this table is checked *after* a turn has ended and none of
it is ever asked for up front.

The table is used exactly twice, and both uses are here rather than restated:

- It is rendered into the orchestrator agent's prompt, beside the target
  repo's own tracker doc verbatim, so that a nudge reads like a user
  explaining the repo's convention rather than the harness leaking through.
- It is a **precondition in the tool layer**: `complete_node` is refused while
  anything is missing, and says what is absent. Verification is not advice to
  the agent; once a completion is refused the agent's only remaining move is
  to nudge.

The paths are the local-markdown tracker's, which is what a target repo is
assumed to carry (see `CONTEXT.md`, *Target repo*, and the preflight warning
when its tracker doc does not mention `.scratch/`).

The check is delivery since dispatch, not provenance: an artifact counts only
when it is satisfied by a file that is new or changed since the node's
dispatch-time **baseline** was taken. Without that, a target repo carrying an
earlier effort's `.scratch/` would satisfy a fresh node's owed set before its
session had written anything, and the node could complete on work the run
never did. Whether the *session* did the work is the orchestrator agent's
judgment, read off the trace; this module only answers whether the repo can
back that judgment up.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from auto.model.common import NodeType
from auto.preflight import TRACKER_DOC_PATH

EFFORT_ROOT = ".scratch"
"""One `<effort>` directory per effort, holding its map or spec and its issues."""

TICKET = "{ticket}"
"""Stands in, inside a pattern, for the path of the node's own ticket."""

RESOLVED_STATUS = re.compile(
    r"^[ \t]*\**status\**[ \t]*:\**[ \t]*resolved\b", re.I | re.M
)
"""The tracker's `Status:` line, in the shapes skills actually write it —
bare, or bolded as the ticket template writes it."""

ANSWER_HEADING = re.compile(r"^[ \t]*#{1,6}[ \t]*answer\b", re.I | re.M)
"""The `## Answer` heading a resolution appends to a ticket."""

CLOSED_OUT_STATUS = re.compile(
    r"^[ \t]*\**status\**[ \t]*:\**[ \t]*(resolved|done|completed?|closed)\b",
    re.I | re.M,
)
"""A `Status:` line recording the ticket as finished.

Wider than `resolved` on purpose: the tracker doc scopes `resolved` to
wayfinder child tickets, and an implementation ticket's terminal vocabulary is
the skill's own choice — so the harness accepts any of the words a session
plausibly closes one out with rather than failing every implement node over a
synonym."""


Baseline = Mapping[Path, tuple[int, int]]
"""What could already satisfy a node's artifacts at dispatch: path → (mtime,
size).

A file recorded here and untouched since cannot be what the node delivered, so
it is excluded when the owed set is measured. One that has been rewritten —
even to the same paths, as a re-run over the same effort directory does —
counts, because its stat no longer matches."""

EMPTY_BASELINE: Baseline = {}


@dataclass(frozen=True)
class OwedArtifact:
    """One thing a node type leaves behind, and how to tell that it did."""

    key: str
    """Short, stable name. What the nudge budget counts and refusals list."""

    owes: str
    """What is owed, in the tracker's own vocabulary. Read by the agent."""

    within: str
    """Glob relative to the target repo. `{ticket}` is the node's own ticket."""

    matching: re.Pattern[str] | None = None
    """What the file must say. `None` when its existence is the whole of it."""

    @property
    def needs_a_ticket(self) -> bool:
        return TICKET in self.within

    def delivered(self, repo: Path, ticket: str | None, baseline: Baseline) -> bool:
        """Whether the target repo can back this artifact up right now.

        A file the baseline holds unchanged does not count — except the node's
        own ticket, which exists before dispatch by design and is judged on the
        edits inside it rather than on being new.
        """
        return any(
            self._satisfied(path)
            for path in self._candidates(repo, ticket)
            if self.needs_a_ticket or baseline.get(path) != _stamp(path)
        )

    def _candidates(self, repo: Path, ticket: str | None) -> Iterable[Path]:
        if not self.needs_a_ticket:
            return repo.glob(self.within)
        if ticket is None:
            return ()
        # A ticket path is a path, not a pattern: globbing it would trip over
        # any character a slug happens to contain.
        return [repo / self.within.replace(TICKET, ticket)]

    def _satisfied(self, path: Path) -> bool:
        if not path.is_file():
            return False
        if self.matching is None:
            return True
        try:
            return self.matching.search(path.read_text(encoding="utf-8")) is not None
        except (OSError, UnicodeDecodeError):
            return False


_MAP = OwedArtifact(
    key="map",
    owes=f"the map for this effort, at `{EFFORT_ROOT}/<effort>/map.md`",
    within=f"{EFFORT_ROOT}/*/map.md",
)

_SPEC = OwedArtifact(
    key="spec",
    owes=f"the spec this conversation produced, at `{EFFORT_ROOT}/<effort>/spec.md`",
    within=f"{EFFORT_ROOT}/*/spec.md",
)

_TICKETS = OwedArtifact(
    key="tickets",
    owes=(
        "the tickets it broke the work into, one file each under "
        f"`{EFFORT_ROOT}/<effort>/issues/`"
    ),
    within=f"{EFFORT_ROOT}/*/issues/*.md",
)

_ANSWER = OwedArtifact(
    key="answer",
    owes="what it found, written into its own ticket under an `## Answer` heading",
    within=TICKET,
    matching=ANSWER_HEADING,
)

_RESOLVED = OwedArtifact(
    key="resolved",
    owes="its own ticket's `Status:` line set to `resolved`",
    within=TICKET,
    matching=RESOLVED_STATUS,
)

_CLOSED_OUT = OwedArtifact(
    key="resolved",
    owes="its own ticket's `Status:` line updated to say the work is done "
    "(`resolved`, `done`, or the like)",
    within=TICKET,
    matching=CLOSED_OUT_STATUS,
)


OWED: dict[NodeType, tuple[OwedArtifact, ...]] = {
    NodeType.GRILL_WITH_DOCS: (_SPEC, _TICKETS),
    NodeType.WAYFINDER: (_MAP, _TICKETS),
    NodeType.RESEARCH: (_ANSWER, _RESOLVED),
    NodeType.PROTOTYPE: (_ANSWER, _RESOLVED),
    NodeType.IMPLEMENT: (_CLOSED_OUT,),
}
"""What each node type leaves behind. Known to the harness, never to a session.

`research` and `prototype` are the standing running cost ADR-0002 accepted:
their skills have no tracker awareness of their own, so they arrive at their
first stale point owing everything and are taught the convention every run.
"""


def baseline(node_type: NodeType, repo: Path) -> Baseline:
    """What could already satisfy this node's artifacts, taken at dispatch.

    Taken *before* the session is launched, so nothing the session writes can
    race its way into it.
    """
    return {
        path: stamp
        for artifact in OWED[node_type]
        if not artifact.needs_a_ticket
        for path in repo.glob(artifact.within)
        if (stamp := _stamp(path)) is not None
    }


def _stamp(path: Path) -> tuple[int, int] | None:
    """A cheap fingerprint of a file's current state, or None when it is gone."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def missing(
    node_type: NodeType,
    repo: Path,
    *,
    ticket: str | None = None,
    since: Baseline = EMPTY_BASELINE,
) -> tuple[OwedArtifact, ...]:
    """What this node still owes, in table order.

    `since` is the node's dispatch-time baseline; whatever it holds cannot be
    what the node delivered. An artifact that names a ticket the node does not
    have is dropped rather than reported missing: an obligation the harness
    cannot check is not an obligation it holds.
    """
    return tuple(
        artifact
        for artifact in OWED[node_type]
        if not (artifact.needs_a_ticket and ticket is None)
        and not artifact.delivered(repo, ticket, since)
    )


def keys(artifacts: Sequence[OwedArtifact]) -> list[str]:
    """The names of these artifacts — what the nudge budget compares."""
    return [artifact.key for artifact in artifacts]


def shrank(previous: Collection[str], current: Collection[str]) -> bool:
    """Whether the owed set actually got smaller, and so counts as progress.

    Strictly smaller, and strictly *from* what was owed before: a set that
    swaps one absent artifact for another has moved without progressing, and
    the nudge budget is there for exactly that case.
    """
    return set(current) < set(previous)


def spelt_out(artifacts: Sequence[OwedArtifact]) -> str:
    """The owed artifacts as one readable clause, for a refusal."""
    return "; ".join(artifact.owes for artifact in artifacts)


def read_tracker_doc(repo: Path) -> str:
    """The target repo's tracker doc, verbatim.

    It goes into the orchestrator agent's prompt whole, beside the table above:
    the table says *that* a tracker file is owed, and the repo's doc is what
    says how one is written here, so a nudge quotes the repo rather than
    inventing a convention. Preflight refuses a repo without the doc, so this
    is only ever empty if something removed it mid-run.
    """
    try:
        return (repo / TRACKER_DOC_PATH).read_text(encoding="utf-8")
    except OSError:
        return ""


def render_table() -> str:
    """The whole table, as the orchestrator agent's prompt carries it."""
    return "\n\n".join(
        "\n".join(
            [f"**`{node_type.skill_invocation}`** leaves behind:"]
            + [f"- {artifact.owes}" for artifact in artifacts]
        )
        for node_type, artifacts in OWED.items()
    )
