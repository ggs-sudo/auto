"""Resolving configuration: defaults in code, then the user config file, then
per-invocation overrides. Whatever comes out is copied into the manifest."""

from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from auto.errors import ConfigError
from auto.model.config import ResolvedConfig

CONFIG_FILENAME = "config.toml"
STATE_DIR_ENV_VAR = "AUTO_STATE_DIR"
DEFAULT_STATE_DIR_NAME = ".auto"


@dataclass(frozen=True)
class ConfigOverrides:
    """Per-invocation overrides. `None` means "not given", not "use the default"."""

    concurrency: int | None = None
    session_budget_usd: float | None = None
    run_budget_usd: float | None = None
    orchestrator_model: str | None = None


def state_dir_from_env() -> Path:
    """Where run state lives: `$AUTO_STATE_DIR`, else `~/.auto`."""
    override = os.environ.get(STATE_DIR_ENV_VAR)
    if override:
        return Path(override).expanduser()
    return Path.home() / DEFAULT_STATE_DIR_NAME


def read_config_file(state_dir: Path) -> dict[str, Any]:
    """The user config file's contents, or an empty mapping when absent."""
    path = state_dir / CONFIG_FILENAME
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc


def resolve_config(
    state_dir: Path, overrides: ConfigOverrides | None = None
) -> ResolvedConfig:
    """Layer the config file and then the overrides over the coded defaults."""
    values = read_config_file(state_dir)
    given = {
        key: value
        for key, value in asdict(overrides or ConfigOverrides()).items()
        if value is not None
    }
    values.update(given)
    try:
        return ResolvedConfig.model_validate(values)
    except ValidationError as exc:
        raise ConfigError(
            f"invalid configuration in {state_dir / CONFIG_FILENAME} "
            f"or on the command line: {exc}"
        ) from exc
