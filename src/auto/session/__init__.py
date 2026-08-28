"""The process-launcher seam and the stream-json codec."""

from __future__ import annotations

from auto.session.cli_launcher import ClaudeCliLauncher, claude_argv
from auto.session.events import (
    StreamEvent,
    is_result,
    split_turns,
    telemetry_from_result,
)
from auto.session.protocol import LaunchedSession, Launcher, LaunchSpec
from auto.session.replay import ReplayLauncher

__all__ = [
    "ClaudeCliLauncher",
    "LaunchSpec",
    "LaunchedSession",
    "Launcher",
    "ReplayLauncher",
    "StreamEvent",
    "claude_argv",
    "is_result",
    "split_turns",
    "telemetry_from_result",
]
