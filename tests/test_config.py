"""Defaults in code, overridable in the user config file, overridable again
per invocation — and whatever resolved is what the manifest records."""

from __future__ import annotations

from pathlib import Path

import pytest

from auto.config import ConfigOverrides, resolve_config, state_dir_from_env
from auto.errors import ConfigError
from auto.model.config import (
    DEFAULT_CONCURRENCY,
    DEFAULT_ORCHESTRATOR_MODEL,
    DEFAULT_RUN_BUDGET_USD,
    DEFAULT_SESSION_BUDGET_USD,
)


def test_an_empty_state_dir_resolves_to_the_defaults(tmp_path: Path) -> None:
    config = resolve_config(tmp_path)
    assert config.concurrency == DEFAULT_CONCURRENCY
    assert config.session_budget_usd == DEFAULT_SESSION_BUDGET_USD
    assert config.run_budget_usd == DEFAULT_RUN_BUDGET_USD
    assert config.orchestrator_model == DEFAULT_ORCHESTRATOR_MODEL


def test_the_user_config_file_overrides_the_defaults(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text(
        'concurrency = 2\norchestrator_model = "claude-sonnet-5"\n'
    )
    config = resolve_config(tmp_path)
    assert config.concurrency == 2
    assert config.orchestrator_model == "claude-sonnet-5"
    assert config.run_budget_usd == DEFAULT_RUN_BUDGET_USD


def test_per_invocation_overrides_beat_the_config_file(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text("concurrency = 2\nrun_budget_usd = 5.0\n")
    config = resolve_config(tmp_path, ConfigOverrides(concurrency=8))
    assert config.concurrency == 8
    assert config.run_budget_usd == 5.0


def test_an_unset_override_does_not_shadow_the_config_file(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text("concurrency = 2\n")
    assert resolve_config(tmp_path, ConfigOverrides()).concurrency == 2


def test_an_unreadable_config_file_is_an_error_not_a_silent_default(
    tmp_path: Path,
) -> None:
    (tmp_path / "config.toml").write_text("concurrency = ")
    with pytest.raises(ConfigError, match="config.toml"):
        resolve_config(tmp_path)


def test_an_unknown_config_key_is_an_error_so_a_typo_is_not_silent(
    tmp_path: Path,
) -> None:
    (tmp_path / "config.toml").write_text("concurency = 2\n")
    with pytest.raises(ConfigError, match="concurency"):
        resolve_config(tmp_path)


def test_an_out_of_range_config_value_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text("concurrency = 0\n")
    with pytest.raises(ConfigError):
        resolve_config(tmp_path)


def test_the_state_dir_defaults_to_dot_auto_under_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("AUTO_STATE_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert state_dir_from_env() == tmp_path / ".auto"


def test_the_state_dir_can_be_moved_by_env_var(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AUTO_STATE_DIR", str(tmp_path / "elsewhere"))
    assert state_dir_from_env() == tmp_path / "elsewhere"
