"""Preflight fails fast, warns where the harness must keep its hands off, and
never creates anything."""

from __future__ import annotations

from pathlib import Path

import pytest

from auto.errors import PreflightError
from auto.preflight import EFFORT_ROOT_DIR_NAME, TRACKER_DOC_PATH, preflight
from tests.conftest import git, make_target_repo


def test_a_well_formed_repo_passes_without_warnings(target_repo: Path) -> None:
    result = preflight(target_repo)
    assert result.warnings == []
    assert result.repo == target_repo.resolve()


def test_it_records_the_repo_state_it_was_pointed_at(target_repo: Path) -> None:
    result = preflight(target_repo)
    assert result.repo == target_repo.resolve()
    assert result.branch == "main"
    assert result.head == git(target_repo, "rev-parse", "HEAD").strip()
    assert result.dirty is False


def test_a_missing_directory_fails(tmp_path: Path) -> None:
    with pytest.raises(PreflightError, match="does not exist"):
        preflight(tmp_path / "nowhere")


def test_a_directory_that_is_not_a_git_repository_fails(tmp_path: Path) -> None:
    plain = make_target_repo(tmp_path / "plain", git_init=False)
    with pytest.raises(PreflightError, match="not a git repository"):
        preflight(plain)


def test_a_repo_without_a_tracker_doc_fails(tmp_path: Path) -> None:
    repo = make_target_repo(tmp_path / "no-tracker", tracker_doc=None)
    with pytest.raises(PreflightError, match=TRACKER_DOC_PATH):
        preflight(repo)


def test_a_repo_that_does_not_gitignore_its_effort_dirs_fails(
    tmp_path: Path,
) -> None:
    repo = make_target_repo(tmp_path / "unignored", gitignore="node_modules/\n")
    with pytest.raises(PreflightError, match=EFFORT_ROOT_DIR_NAME):
        preflight(repo)


def test_preflight_creates_nothing(tmp_path: Path) -> None:
    repo = make_target_repo(tmp_path / "no-tracker", tracker_doc=None)
    before = sorted(p.relative_to(repo) for p in repo.rglob("*"))
    with pytest.raises(PreflightError):
        preflight(repo)
    assert sorted(p.relative_to(repo) for p in repo.rglob("*")) == before


def test_a_dirty_working_tree_warns_and_proceeds(target_repo: Path) -> None:
    (target_repo / "untracked.txt").write_text("wip\n")
    result = preflight(target_repo)
    assert result.dirty is True
    assert any("dirty" in warning for warning in result.warnings)


def test_an_unrecognised_tracker_flavour_warns_and_proceeds(
    tmp_path: Path,
) -> None:
    repo = make_target_repo(
        tmp_path / "github-tracker",
        tracker_doc="# Issue tracker: GitHub\n\nUse the `gh` CLI.\n",
    )
    result = preflight(repo)
    assert any("tracker" in warning for warning in result.warnings)


def test_a_subdirectory_of_a_repo_resolves_to_the_worktree_root(
    target_repo: Path,
) -> None:
    nested = target_repo / "src" / "deep"
    nested.mkdir(parents=True)
    assert preflight(nested).repo == target_repo.resolve()


def test_a_detached_head_records_no_branch(target_repo: Path) -> None:
    head = git(target_repo, "rev-parse", "HEAD").strip()
    git(target_repo, "checkout", "--quiet", "--detach", head)
    assert preflight(target_repo).branch is None
