"""The harness tools, and the single-node scope one intervention gets them in.

Driven sessions get none of this. These tools exist for the orchestrator agent
alone, and they are the *only* way an intervention changes anything: the
agent's prose is recorded for the reader and has no effect. An agent that
rambles can fail to act, but it cannot corrupt state.

Two rules are enforced here rather than asked for in the prompt, because a
prompt is a request and this is a precondition:

- **Scope.** An intervention's tools act on the node its URL names. No tool
  takes a node argument, so there is no argument for an agent to get wrong.
- **Exclusivity.** `send_to_session`, `complete_node` and `fail_node` are
  mutually exclusive within one intervention: a node is being nudged onward,
  called finished, or given up on, never two of the three.
- **Owed artifacts.** `complete_node` is refused while the node still owes a
  tracker file, and says which. Verification is a precondition, not advice: an
  agent that was going to complete a node anyway cannot talk its way past it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from auto.model.intervention import ToolCall

SERVER_NAME = "harness"
"""The MCP server name the agent sees its tools under."""


def qualified(tool: str) -> str:
    """A tool's name as the agent sees it, once MCP has namespaced it."""
    return f"mcp__{SERVER_NAME}__{tool}"


class SendToSession(BaseModel):
    """Arguments to `send_to_session`. No node: the URL already said which."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(
        min_length=1,
        description="The message to deliver, written as the user would write "
        "it. The session knows nothing about the harness, so this must read as "
        "an ordinary next turn of the conversation.",
    )
    highlights: list[str] = Field(
        default_factory=list,
        description="Short scannable facts to append to the session's record, "
        "so a reviewer can triage the node without reading its transcript.",
    )


class CompleteNode(BaseModel):
    """Arguments to `complete_node`."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(
        min_length=1,
        description="What this node actually produced, in one or two sentences.",
    )
    highlights: list[str] = Field(
        default_factory=list,
        description="Short scannable facts to append to the session's record.",
    )


class FailNode(BaseModel):
    """Arguments to `fail_node`."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(
        min_length=1,
        description="Why this node cannot be finished, in one or two sentences. "
        "It is the only account of the failure anyone will read.",
    )
    highlights: list[str] = Field(
        default_factory=list,
        description="Short scannable facts to append to the session's record.",
    )


SEND_TO_SESSION = "send_to_session"
COMPLETE_NODE = "complete_node"
FAIL_NODE = "fail_node"


@dataclass(frozen=True)
class Tool:
    """One harness tool: what it is called, what it takes, what it does.

    Everything about a tool lives on one row, so adding the next one is a new
    entry rather than an edit in five places.
    """

    name: str
    description: str
    arguments: type[BaseModel]
    perform: Callable[["HarnessTools", BaseModel], Awaitable["ToolResult"]]
    exclusive: bool = False
    """Whether at most one call of this kind may land per intervention."""


async def _send(tools: "HarnessTools", parsed: BaseModel) -> "ToolResult":
    assert isinstance(parsed, SendToSession)
    return await tools.send_to_session(parsed.message, parsed.highlights)


async def _complete(tools: "HarnessTools", parsed: BaseModel) -> "ToolResult":
    assert isinstance(parsed, CompleteNode)
    return await tools.complete_node(parsed.summary, parsed.highlights)


async def _fail(tools: "HarnessTools", parsed: BaseModel) -> "ToolResult":
    assert isinstance(parsed, FailNode)
    return await tools.fail_node(parsed.reason, parsed.highlights)


@dataclass(frozen=True)
class ToolResult:
    """What a tool call reports back to the agent."""

    text: str
    is_error: bool = False


TOOLS: dict[str, Tool] = {
    tool.name: tool
    for tool in (
        Tool(
            name=SEND_TO_SESSION,
            description=(
                "Deliver a message to the node's live session, which carries on "
                "from where it left off with its context intact. Use this to "
                "answer a question it asked, or to move it on when its turn "
                "ended mid-thought."
            ),
            arguments=SendToSession,
            perform=_send,
            exclusive=True,
        ),
        Tool(
            name=COMPLETE_NODE,
            description=(
                "Mark the node finished. Terminal success: nothing further will "
                "be asked of this session and it is brought down."
            ),
            arguments=CompleteNode,
            perform=_complete,
            exclusive=True,
        ),
        Tool(
            name=FAIL_NODE,
            description=(
                "Give up on the node. Terminal failure: the session is brought "
                "down and whatever depended on this node is blocked, while "
                "everything else in the run carries on. Use it when the work "
                "cannot be finished at all, not when it is merely unfinished."
            ),
            arguments=FailNode,
            perform=_fail,
            exclusive=True,
        ),
    )
}


QUALIFIED_TOOL_NAMES = tuple(
    qualified(name) for name in (SEND_TO_SESSION, COMPLETE_NODE, FAIL_NODE)
)


def tool_definitions() -> list[dict[str, Any]]:
    """The roster, in the shape MCP's `tools/list` returns it."""
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "inputSchema": tool.arguments.model_json_schema(),
        }
        for tool in TOOLS.values()
    ]


class HarnessTools(Protocol):
    """What the loop does when a tool is called. One instance per node.

    Implementations run on the orchestrator's loop thread, which is what lets
    them touch run state and a live session directly.
    """

    async def send_to_session(
        self, message: str, highlights: Sequence[str]
    ) -> ToolResult: ...

    async def complete_node(
        self, summary: str, highlights: Sequence[str]
    ) -> ToolResult: ...

    async def fail_node(
        self, reason: str, highlights: Sequence[str]
    ) -> ToolResult: ...


@dataclass
class Intervention:
    """One ephemeral agent's window on one node, and everything it does.

    It is open only while that agent is running: the tool server routes to it
    by URL, so an agent whose intervention has ended is not merely disallowed
    from acting, it is unaddressable.
    """

    node: str
    tools: HarnessTools
    tool_calls: list[ToolCall] = field(default_factory=list)
    _exclusive: str | None = None

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Validate, refuse or perform, and record either way."""
        tool = TOOLS.get(name)
        if tool is None:
            return self._refuse(name, arguments, f"no such harness tool: {name}")

        try:
            parsed = tool.arguments.model_validate(arguments)
        except ValidationError as exc:
            return self._refuse(name, arguments, _readable(exc))

        if tool.exclusive and self._exclusive is not None:
            return self._refuse(
                name,
                arguments,
                f"this intervention already called {self._exclusive}: one "
                f"intervention lands at most one of {SEND_TO_SESSION}, "
                f"{COMPLETE_NODE} or {FAIL_NODE}",
            )

        result = await tool.perform(self.tools, parsed)
        if result.is_error:
            # A tool that could not do its job has not used the intervention up.
            return self._refuse(name, arguments, result.text)
        if tool.exclusive:
            self._exclusive = name
        self.tool_calls.append(ToolCall(tool=name, arguments=arguments))
        return result

    def _refuse(
        self, name: str, arguments: dict[str, Any], refusal: str
    ) -> ToolResult:
        self.tool_calls.append(
            ToolCall(tool=name, arguments=arguments, refused=refusal)
        )
        return ToolResult(refusal, is_error=True)


def _readable(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc']) or 'arguments'}: {error['msg']}"
        for error in exc.errors()
    )
