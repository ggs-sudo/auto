"""Pydantic models — the source of truth for every persisted meta file.

The JSON Schemas under `schemas/` are generated from these and checked in; a
test fails when they go stale.
"""

from __future__ import annotations

from auto.model.common import (
    ROOT_NODE_ID,
    SCHEMA_VERSION,
    NodeState,
    NodeStatus,
    NodeType,
    Route,
    RunStatus,
    SessionStatus,
)
from auto.model.config import ResolvedConfig
from auto.model.gate import Gate, GateDecision, GateKind, GateResponse
from auto.model.graph import Graph, GraphNode, TaskResolutionMode, TicketType
from auto.model.intervention import (
    InterventionRecord,
    InterventionTrigger,
    ToolCall,
)
from auto.model.manifest import Manifest, RootNode
from auto.model.session import SessionRecord, Telemetry

__all__ = [
    "ROOT_NODE_ID",
    "SCHEMA_VERSION",
    "Gate",
    "GateDecision",
    "GateKind",
    "GateResponse",
    "Graph",
    "GraphNode",
    "InterventionRecord",
    "InterventionTrigger",
    "Manifest",
    "NodeState",
    "NodeStatus",
    "NodeType",
    "ResolvedConfig",
    "RootNode",
    "Route",
    "RunStatus",
    "SessionRecord",
    "SessionStatus",
    "TaskResolutionMode",
    "Telemetry",
    "TicketType",
    "ToolCall",
]
