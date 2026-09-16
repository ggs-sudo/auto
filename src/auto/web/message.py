"""The website's second write: a user message into one of a node's chats.

The same process boundary as a gate response (ADR-0007), for the other
direction of conversation (ADR-0014). The server writes exactly
`messages/<message-id>.json` and nothing else; the orchestrator's loop does the
delivering and writes the delivery beside it. Nothing here talks to a run.

Validation refuses, up front, what the loop could only refuse later: a message
with no live orchestrator to carry it, one addressed to a node that has no live
session, one written against a session the node has since replaced, and one
into a node parked at a gate — whose answer belongs in the gate, where it
closes the gate's books. The loop re-checks all of it on pickup, because the
node can move between this check and that one.
"""

from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from auto.errors import AutoError, UsageError
from auto.liveness import LIVE_CLAIMS, orchestrator_alive
from auto.model import (
    ROOT_NODE_ID,
    MessageTarget,
    NodeStatus,
    UserMessage,
)
from auto.run import load_run
from auto.web.reads import load_run_graphs


class MessageRejected(AutoError):
    """The body itself is unusable. Nothing was written."""


class MessageUndeliverable(AutoError):
    """The message is well-formed, but nothing could deliver it now."""


class _Submission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: MessageTarget
    node: str
    session_id: str
    text: str


def send_message(state_dir: Path, run_id: str, payload: Any) -> UserMessage:
    """Validate a message and write it for the loop to deliver.

    Raises `UsageError` for a run or node that does not exist,
    `MessageRejected` for a body that cannot be a message, and
    `MessageUndeliverable` for a message nothing could deliver now.
    """
    run = load_run(state_dir, run_id)
    try:
        submission = _Submission.model_validate(payload)
        now = datetime.now(UTC)
        message = UserMessage(
            message_id=f"{now:%Y%m%dT%H%M%S%f}-{secrets.token_hex(3)}",
            node=submission.node,
            session_id=submission.session_id,
            target=submission.target,
            text=submission.text,
            sent_at=now,
        )
    except ValidationError as exc:
        raise MessageRejected(str(exc)) from exc

    manifest = run.read_manifest()
    if not (
        manifest.status in LIVE_CLAIMS and orchestrator_alive(run.read_liveness())
    ):
        raise MessageUndeliverable(
            f"run {run_id} has no live orchestrator, so nothing would deliver "
            "the message"
        )
    node = _find_node(manifest, message.node, load_run_graphs(manifest))
    if node is None:
        raise UsageError(f"run {run_id} has no node {message.node}")
    if node.gate is not None:
        raise MessageUndeliverable(
            f"node {message.node} is waiting at gate {node.gate}: answer the "
            "gate instead"
        )
    if node.status is not NodeStatus.IN_PROGRESS:
        raise MessageUndeliverable(
            f"node {message.node} is {node.status.value} and has no live session "
            "to message"
        )
    if node.session_id != message.session_id:
        raise MessageUndeliverable(
            f"session {message.session_id} is no longer node {message.node}'s "
            "session"
        )

    run.messages_dir.mkdir(parents=True, exist_ok=True)
    _write_exclusively(
        run.message_path(message.message_id),
        message.model_dump_json(indent=2) + "\n",
    )
    return message


def _find_node(manifest: Any, node_id: str, graphs: Any) -> Any:
    if node_id == ROOT_NODE_ID:
        return manifest.root_node
    graph_id, _, stem = node_id.rpartition("/")
    for graph in graphs:
        if graph.graph_id == graph_id:
            return graph.node(stem)
    return None


def _write_exclusively(path: Path, content: str) -> None:
    """Link rather than rename: a message id is never written twice."""
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(content, encoding="utf-8")
    try:
        os.link(temp, path)
    finally:
        temp.unlink(missing_ok=True)
