"""The ephemeral orchestrator agent: its prompt, its trace, its invocation."""

from __future__ import annotations

from auto.agent.invoke import OrchestratorAgent
from auto.agent.prompt import intervention_message, stable_system_prompt
from auto.agent.trace import render as render_trace

__all__ = [
    "OrchestratorAgent",
    "intervention_message",
    "render_trace",
    "stable_system_prompt",
]
