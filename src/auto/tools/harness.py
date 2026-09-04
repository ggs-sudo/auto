"""The harness tools, and the single-node scope one intervention gets them in.

Driven sessions get none of this. These tools exist for the orchestrator agent
alone, and they are the *only* way an intervention changes anything: the
agent's prose is recorded for the reader and has no effect. An agent that
rambles can fail to act, but it cannot corrupt state.

Two rules are enforced here rather than asked for in the prompt, because a
prompt is a request and this is a precondition:

- **Scope.** An intervention's tools act on the node its URL names. No tool
  takes a node argument, so there is no argument for an agent to get wrong.
- **Exclusivity.** `send_to_session`, `complete_node`, `fail_node` and
  the gate-raising tools (`prototype_ready`, `hand_to_user`,
  `escalate_question`) are mutually exclusive within one intervention: a node
  is being nudged onward, called finished, given up on, or parked at a gate,
  never two of those. `emit_graph` is not exclusive — it accompanies the
  completion of the node whose session wrote the tickets. Neither is
  `ping_user`: a ping is run-level and decides nothing about the node.
- **Owed artifacts.** `complete_node` is refused while the node still owes a
  tracker file, and says which. Verification is a precondition, not advice: an
  agent that was going to complete a node anyway cannot talk its way past it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from auto.model.graph import TaskResolutionMode
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


class TaskClassification(BaseModel):
    """One `task` ticket's resolution mode, decided as the graph is emitted."""

    model_config = ConfigDict(extra="forbid")

    ticket: str = Field(
        min_length=1,
        description="The ticket file's name or stem, e.g. `03-wire-up` or "
        "`03-wire-up.md`.",
    )
    mode: TaskResolutionMode = Field(
        description="`agent` for work a session can do alone; `user` for work "
        "only a human can perform; `undefined` for a milestone not yet "
        "specified enough to be either — it will be resolved by a grilling "
        "session in its place.",
    )


class EmitGraph(BaseModel):
    """Arguments to `emit_graph`."""

    model_config = ConfigDict(extra="forbid")

    effort: str = Field(
        min_length=1,
        description="The effort directory name under `.scratch/` holding the "
        "tickets this node's session wrote. Becomes the graph's id.",
    )
    tasks: list[TaskClassification] = Field(
        default_factory=list,
        description="A resolution mode for every ticket whose `Type:` line "
        "says `task`. Mandatory for each of them; meaningless for any other "
        "type.",
    )
    highlights: list[str] = Field(
        default_factory=list,
        description="Short scannable facts to append to the session's record.",
    )


class PrototypeReady(BaseModel):
    """Arguments to `prototype_ready`."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(
        min_length=1,
        description="What the user is being asked to judge about this "
        "prototype, in one or two sentences.",
    )
    artifact: str = Field(
        min_length=1,
        description="Pointer to what they should look at — the path or URL of "
        "the prototype the session built. A review the user cannot see is not "
        "a review, so this is required.",
    )
    highlights: list[str] = Field(
        default_factory=list,
        description="Short scannable facts to append to the session's record.",
    )


class HandToUser(BaseModel):
    """Arguments to `hand_to_user`."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(
        min_length=1,
        description="What the user must go and do, and what facts to report "
        "back when it is done — a credential's location, a URL, a row count. "
        "In one or two sentences.",
    )
    highlights: list[str] = Field(
        default_factory=list,
        description="Short scannable facts to append to the session's record.",
    )


class EscalateQuestion(BaseModel):
    """Arguments to `escalate_question`."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(
        min_length=1,
        description="The policy-critical question, with enough of its context "
        "that the user can decide without reading the transcript.",
    )
    highlights: list[str] = Field(
        default_factory=list,
        description="Short scannable facts to append to the session's record.",
    )


class PingUser(BaseModel):
    """Arguments to `ping_user`."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(
        min_length=1,
        description="What the user should hear. Informational only: nothing "
        "waits on their reaction.",
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


class ReportEffortClean(BaseModel):
    """Arguments to `report_effort_clean`. No effort: the URL already said which."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(
        min_length=1,
        description="Why the effort is clean: what was checked and what agreed, "
        "in a few sentences. It becomes part of the reconciliation record.",
    )


class ResetNode(BaseModel):
    """Arguments to `reset_node`. No effort: the URL already said which."""

    model_config = ConfigDict(extra="forbid")

    node: str = Field(
        min_length=1,
        description="The node to reset: the ticket file's name or stem, e.g. "
        "`03-wire-up` or `03-wire-up.md`.",
    )
    evidence: str = Field(
        min_length=1,
        description="What the repo shows that contradicts the recorded status, "
        "in one or two sentences. It becomes the correction's evidence in the "
        "reconciliation record.",
    )


SEND_TO_SESSION = "send_to_session"
COMPLETE_NODE = "complete_node"
EMIT_GRAPH = "emit_graph"
FAIL_NODE = "fail_node"
PROTOTYPE_READY = "prototype_ready"
HAND_TO_USER = "hand_to_user"
ESCALATE_QUESTION = "escalate_question"
PING_USER = "ping_user"
REPORT_EFFORT_CLEAN = "report_effort_clean"
RESET_NODE = "reset_node"


@dataclass(frozen=True)
class Tool:
    """One harness tool: what it is called, what it takes, what it does.

    Everything about a tool lives on one row, so adding the next one is a new
    entry rather than an edit in five places.
    """

    name: str
    description: str
    arguments: type[BaseModel]
    perform: Callable[[Any, BaseModel], Awaitable["ToolResult"]]
    """Typed loosely because two rosters share this row: node tools perform
    against `HarnessTools`, takeover tools against `TakeoverTools`, and each
    perform function asserts its own parsed-argument type anyway."""

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


async def _emit(tools: "HarnessTools", parsed: BaseModel) -> "ToolResult":
    assert isinstance(parsed, EmitGraph)
    return await tools.emit_graph(
        parsed.effort,
        {entry.ticket: entry.mode for entry in parsed.tasks},
        parsed.highlights,
    )


async def _ready(tools: "HarnessTools", parsed: BaseModel) -> "ToolResult":
    assert isinstance(parsed, PrototypeReady)
    return await tools.prototype_ready(
        parsed.question, parsed.artifact, parsed.highlights
    )


async def _hand(tools: "HarnessTools", parsed: BaseModel) -> "ToolResult":
    assert isinstance(parsed, HandToUser)
    return await tools.hand_to_user(parsed.question, parsed.highlights)


async def _escalate(tools: "HarnessTools", parsed: BaseModel) -> "ToolResult":
    assert isinstance(parsed, EscalateQuestion)
    return await tools.escalate_question(parsed.question, parsed.highlights)


async def _ping(tools: "HarnessTools", parsed: BaseModel) -> "ToolResult":
    assert isinstance(parsed, PingUser)
    return await tools.ping_user(parsed.message, parsed.highlights)


async def _report_clean(tools: "TakeoverTools", parsed: BaseModel) -> "ToolResult":
    assert isinstance(parsed, ReportEffortClean)
    return await tools.report_effort_clean(parsed.summary)


async def _reset(tools: "TakeoverTools", parsed: BaseModel) -> "ToolResult":
    assert isinstance(parsed, ResetNode)
    return await tools.reset_node(parsed.node, parsed.evidence)


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
            name=EMIT_GRAPH,
            description=(
                "Derive this node's execution graph from the tickets its "
                "session wrote under `.scratch/<effort>/issues/` and hand it "
                "to the run, classifying every `task` ticket as agent, user "
                "or undefined. Call it when a grilling or wayfinder session "
                "has genuinely finished its tickets, before completing the "
                "node — the tickets become dispatchable work only through "
                "this."
            ),
            arguments=EmitGraph,
            perform=_emit,
        ),
        Tool(
            name=PROTOTYPE_READY,
            description=(
                "Raise a prototype-review gate: record that this prototype "
                "node's build is ready for the user to judge, with a pointer "
                "to the artifact and the question they should answer. The "
                "node waits — alive and idle — until they respond; whatever "
                "depends on it waits with it, and everything else keeps "
                "running. For prototype nodes only, and only while no gate "
                "is already open on this node."
            ),
            arguments=PrototypeReady,
            perform=_ready,
            exclusive=True,
        ),
        Tool(
            name=HAND_TO_USER,
            description=(
                "Raise a task-completion gate: this node's ticket is work "
                "only a human being can perform, and the session has hit that "
                "wall. Say what the user must do and what facts to report "
                "back. The node waits — alive and idle — until they respond: "
                "a `done` answer carries facts the session must then persist, "
                "and a `cannot` answer fails the node on its own. For task "
                "tickets the run classified `user` only."
            ),
            arguments=HandToUser,
            perform=_hand,
            exclusive=True,
        ),
        Tool(
            name=ESCALATE_QUESTION,
            description=(
                "Raise an escalated-question gate: the session asked "
                "something only the user can decide — vendor lock-in, "
                "credentials, a fact the run cannot know — and the answer "
                "policy's escape hatch applies. The node waits — alive and "
                "idle — until the user answers, and their answer comes back "
                "to be delivered. Rare by design: escalate only what you "
                "genuinely cannot decide."
            ),
            arguments=EscalateQuestion,
            perform=_escalate,
            exclusive=True,
        ),
        Tool(
            name=PING_USER,
            description=(
                "Tell the user something without stopping anything: the ping "
                "is surfaced run-level, blocks no node, and needs no answer. "
                "Use it for things worth knowing before the run ends — a "
                "notable decision, a risk accepted, work skipped. It rides "
                "alongside any other tool call."
            ),
            arguments=PingUser,
            perform=_ping,
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


TAKEOVER_TOOLS: dict[str, Tool] = {
    tool.name: tool
    for tool in (
        Tool(
            name=REPORT_EFFORT_CLEAN,
            description=(
                "Record your verdict that this effort's recorded state matches "
                "what the target repo actually shows: every node recorded done "
                "is backed by its ticket, and every open ticket is recorded as "
                "unfinished work. Execution resumes only after this verdict "
                "lands, so call it only when you have genuinely checked — and "
                "only after every correction the effort needs has been made "
                "with `reset_node`."
            ),
            arguments=ReportEffortClean,
            perform=_report_clean,
            exclusive=True,
        ),
        Tool(
            name=RESET_NODE,
            description=(
                "Reset one graph node to pending, so the resumed run "
                "re-executes it. For a node recorded done whose work the "
                "target repo does not show, or a failed node whose work is "
                "doable after all — resetting it unblocks its dependents. "
                "The correction lands in the reconciliation record with the "
                "evidence you give here. Not exclusive: reset every node "
                "that needs it, then report the effort clean."
            ),
            arguments=ResetNode,
            perform=_reset,
        ),
    )
}
"""The takeover roster: what an agent judging one effort's recorded state can
do. Served on the effort-scoped address, never on a node's."""


QUALIFIED_TOOL_NAMES = tuple(
    qualified(name)
    for name in (
        SEND_TO_SESSION,
        COMPLETE_NODE,
        EMIT_GRAPH,
        PROTOTYPE_READY,
        HAND_TO_USER,
        ESCALATE_QUESTION,
        PING_USER,
        FAIL_NODE,
    )
)

TAKEOVER_TOOL_NAMES = (qualified(REPORT_EFFORT_CLEAN), qualified(RESET_NODE))


def tool_definitions(roster: Mapping[str, Tool] = TOOLS) -> list[dict[str, Any]]:
    """One roster, in the shape MCP's `tools/list` returns it."""
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "inputSchema": tool.arguments.model_json_schema(),
        }
        for tool in roster.values()
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

    async def emit_graph(
        self,
        effort: str,
        task_modes: Mapping[str, TaskResolutionMode],
        highlights: Sequence[str],
    ) -> ToolResult: ...

    async def prototype_ready(
        self, question: str, artifact: str, highlights: Sequence[str]
    ) -> ToolResult: ...

    async def hand_to_user(
        self, question: str, highlights: Sequence[str]
    ) -> ToolResult: ...

    async def escalate_question(
        self, question: str, highlights: Sequence[str]
    ) -> ToolResult: ...

    async def ping_user(
        self, message: str, highlights: Sequence[str]
    ) -> ToolResult: ...


class TakeoverTools(Protocol):
    """What a takeover does when its agent lands a verdict. One per consultation.

    Implementations run on the loop thread, like `HarnessTools`.
    """

    async def report_effort_clean(self, summary: str) -> ToolResult: ...

    async def reset_node(self, node: str, evidence: str) -> ToolResult: ...


@dataclass
class Intervention:
    """One ephemeral agent's window on one scope, and everything it does.

    The scope is one node — or, for a takeover consultation, one effort — and
    the window is open only while that agent is running: the tool server
    routes to it by URL, so an agent whose intervention has ended is not
    merely disallowed from acting, it is unaddressable. The roster is the
    window's: the same endpoint serves node tools on node addresses and
    takeover tools on effort addresses.
    """

    scope: str
    """What the window is about: a node id, or a takeover's effort name."""

    tools: HarnessTools | TakeoverTools
    roster: Mapping[str, Tool] = field(default_factory=lambda: TOOLS)
    tool_calls: list[ToolCall] = field(default_factory=list)
    _exclusive: str | None = None

    def definitions(self) -> list[dict[str, Any]]:
        """What `tools/list` answers on this window's address."""
        return tool_definitions(self.roster)

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Validate, refuse or perform, and record either way."""
        tool = self.roster.get(name)
        if tool is None:
            return self._refuse(name, arguments, f"no such harness tool: {name}")

        try:
            parsed = tool.arguments.model_validate(arguments)
        except ValidationError as exc:
            return self._refuse(name, arguments, _readable(exc))

        if tool.exclusive and self._exclusive is not None:
            exclusive = ", ".join(t.name for t in self.roster.values() if t.exclusive)
            return self._refuse(
                name,
                arguments,
                f"this intervention already called {self._exclusive}: one "
                f"intervention lands at most one of {exclusive}",
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
