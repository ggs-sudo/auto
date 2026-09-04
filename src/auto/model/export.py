"""Generate the checked-in JSON Schemas from the Pydantic models.

The models are the source of truth; `schemas/` is a build product that happens
to live in git so the website (and anything else downstream) can consume it
without running Python. `tests/test_model.py` fails when the two drift.

    uv run python -m auto.model.export          # rewrite schemas/
    uv run python -m auto.model.export --check  # fail if stale
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import BaseModel

from auto.model.gate import Gate, GateResponse
from auto.model.graph import Graph
from auto.model.intervention import InterventionRecord
from auto.model.liveness import Liveness
from auto.model.manifest import Manifest
from auto.model.reconciliation import ReconciliationRecord
from auto.model.session import SessionRecord

SCHEMAS: dict[str, type[BaseModel]] = {
    "gate-response.schema.json": GateResponse,
    "gate.schema.json": Gate,
    "graph.schema.json": Graph,
    "intervention.schema.json": InterventionRecord,
    "liveness.schema.json": Liveness,
    "manifest.schema.json": Manifest,
    "reconciliation.schema.json": ReconciliationRecord,
    "session-record.schema.json": SessionRecord,
}

_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"


def render(model: type[BaseModel]) -> str:
    """Render one model's JSON Schema exactly as it is checked in."""
    schema: dict[str, object] = {"$schema": _SCHEMA_DIALECT}
    schema.update(model.model_json_schema())
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def rendered() -> dict[str, str]:
    """Every schema file the models imply, keyed by filename."""
    return {name: render(model) for name, model in SCHEMAS.items()}


def default_schema_dir() -> Path:
    """`schemas/` beside the `pyproject.toml` this package was checked out with."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file():
            return parent / "schemas"
    raise RuntimeError("no pyproject.toml above auto.model.export; pass a directory")


def stale(directory: Path) -> list[str]:
    """Filenames whose checked-in content does not match the models."""
    out = []
    for name, content in rendered().items():
        path = directory / name
        if not path.is_file() or path.read_text() != content:
            out.append(name)
    return sorted(out)


def write(directory: Path) -> list[Path]:
    """Write every schema, returning the paths written."""
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, content in rendered().items():
        path = directory / name
        path.write_text(content)
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    check = "--check" in args
    if check:
        args.remove("--check")
    directory = Path(args[0]) if args else default_schema_dir()

    if check:
        outdated = stale(directory)
        if outdated:
            print(
                "schemas are stale: " + ", ".join(outdated),
                "\nrun: uv run python -m auto.model.export",
                file=sys.stderr,
            )
            return 1
        return 0

    for path in write(directory):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
