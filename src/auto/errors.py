"""Errors the CLI turns into a message and an exit code, rather than a traceback."""

from __future__ import annotations


class AutoError(Exception):
    """Base for every error the operator is meant to read rather than debug."""


class ConfigError(AutoError):
    """The user config file or a per-invocation override is unusable."""


class PreflightError(AutoError):
    """The target repo is not in a state a run can start against."""


class UsageError(AutoError):
    """The invocation itself is wrong — a missing prompt, an unknown run."""


class SessionLaunchError(AutoError):
    """A session could not be started, or died without being asked to."""


class TakeoverError(AutoError):
    """An effort's run cannot be taken over — held, unlocatable, or not clean."""
