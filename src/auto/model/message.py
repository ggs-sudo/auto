"""User messages: the user writing straight into a node's chat.

A gate is the run asking the user something. A user message is the other
direction — the user, from the monitoring website, speaking unasked into one
node's session or to the orchestrator about that node. It crosses the process
boundary the way a gate response does (ADR-0007, ADR-0014): the website writes
`messages/<message-id>.json`, and the orchestrator's loop picks it up, delivers
it, and writes `messages/<message-id>.delivery.json` beside it. The split is
absolute in both directions: the website never writes a delivery, and the
orchestrator never writes a message.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from auto.model.common import SCHEMA_VERSION


class MessageTarget(StrEnum):
    """Which of a node's two chats the message was written into."""

    SESSION = "session"
    """The driven session itself: the words are delivered verbatim, on its
    stdin, at its next turn boundary."""

    ORCHESTRATOR = "orchestrator"
    """The orchestrator, about this node: a fresh agent is invoked with the
    words and the node's trace, and acts on them with its tools."""


class UserMessage(BaseModel):
    """`messages/<message-id>.json`. Written only by the website."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=SCHEMA_VERSION)
    message_id: str = Field(
        description="`<timestamp>-<random>`, so the directory sorts as history."
    )
    node: str = Field(description="The run-wide id of the node addressed.")
    session_id: str = Field(
        description="The driven session the user was looking at. A message is "
        "never delivered into a later session of the same node: what the user "
        "wrote was about this one."
    )
    target: MessageTarget
    text: str = Field(description="The user's own words, delivered verbatim.")
    sent_at: datetime

    @field_validator("text")
    @classmethod
    def _says_something(cls, text: str) -> str:
        if not text.strip():
            raise ValueError("a message must say something")
        return text


class MessageDelivery(BaseModel):
    """`messages/<message-id>.delivery.json`. Written only by the orchestrator.

    Its presence is what "taken up" means: a message without one is still
    waiting for the loop. A refusal is a delivery too — the message is closed,
    and the reason is what the website shows in its place.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=SCHEMA_VERSION)
    message_id: str
    delivered_at: datetime
    refused: str | None = Field(
        default=None,
        description="Why the message was not delivered, or None when it was.",
    )
    intervention_id: str | None = Field(
        default=None,
        description="For an orchestrator message: the intervention it invoked.",
    )
