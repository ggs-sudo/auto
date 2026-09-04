# Context

Glossary for the `auto` harness. Use these terms exactly; don't drift to synonyms.

## Harness

The whole system built by this repo: the `auto` CLI, the orchestrator, the meta files, and the monitoring website. Drives Matt Pocock's skills through `claude -p` to implement features in a target repo. Never calls the Anthropic API directly.

## Target repo

A repo the harness implements features in. Always pre-configured with Matt Pocock's skills and the local-markdown tracker (`docs/agents/issue-tracker.md`, tickets under `.scratch/`). The harness assumes this and never sets it up.

## Guinea-pig repo

The target repo used to validate the harness itself: `~/workspace/ai-new`. Harness test runs there always execute in a fresh git worktree so runs can be compared side by side.

## Run

One end-to-end execution of the harness for one feature: from pasted prompt to an exhausted graph. The monitoring website uses the same word. Run state lives centrally under `~/.auto/runs/<run-id>/` — the manifest, session records, transcripts and gates — with one exception: the graphs live in the target repo under `.scratch/`, beside the tickets they are derived from, and are gitignored. A run is **finished** when every node in its graph and in every subgraph beneath it is complete. Committing is the implementing session's job, not the harness's: the harness never creates branches or worktrees, and the manifest's `worktree`/`branch` fields record the repo state it was pointed at, never anything it did.

## Effort

One `.scratch/<effort>/` directory in the target repo: the tickets, artifacts and graph for one body of work. One graph per effort. Not a synonym for run: one run spans several efforts (its root graph and every subgraph beneath it), and one effort can be touched by several runs over its lifetime.

## Route

Which skill a run enters through, chosen explicitly by a CLI argument: the **Wayfinder route** (`/wayfinder`) or the **grill route** (`/grill-with-docs`). Not inferred by the harness.

## Orchestrator

The harness's project manager, and one of the two agent roles the harness defines (**Takeover** is the other): a deterministic Python control loop plus the agent it consults for judgment. The agent is **ephemeral**: a fresh one is invoked for every intervention on every node, never one long-lived agent per run. Two reasons, both load-bearing — a single agent's trail would accumulate every node's context until it was reading things irrelevant to the node in front of it, and one agent cannot monitor parallel nodes. The orchestrator agent reads the entire trace of a monitored session — never just the questions — and stands in for the user per the answer policy, with the run's seed prompt framed as a message the orchestrator itself wrote.

## Takeover

The harness's second agent role: a project manager for an effort whose run stopped without finishing, invoked manually as `auto takeover <effort-dir>` with the same flags and defaults as `auto run` — nothing inherited from the old run's config. It locates the effort's run by scanning run manifests and continues it rather than minting a new one; a run held by a live orchestrator is refused (running-but-dead is a crashed run, the prime candidate, and there is no force flag). Takeover reconciles before it resumes: the harness gathers the evidence deterministically, an ephemeral agent consulted through effort-scoped harness tools (ADR-0009) judges the recorded state against the repo — the repo is ground truth, always — and the whole consultation lands as a numbered **reconciliation record** in the run directory. Graph drift is the takeover agent's to repair: a node recorded done whose work the repo does not show, or a failed node whose work is doable after all, is reset to pending through an effort-scoped tool, and the resumed run re-executes it — every correction lands in the reconciliation record with its prior status, new status and evidence, under a **corrected** verdict (**clean** means nothing needed correcting). The manifest is then revived (status back to running, ended timestamp cleared, gate numbering preserved) and the pending nodes go to the stock orchestrator loop. Graph files are harness state, so corrections there are free. A lying ticket `Status:` line is corrected in the ticket itself — rewritten to an open status with a reconciliation note appended — because a from-scratch derivation would otherwise re-import the closed-out ticket as done; that one line is the sole amendment (ADR-0010) to the rule that tracker files are what sessions wrote, and nothing else in a ticket is ever writable by the harness.

## Answer policy

How the orchestrator stands in for the user during grilling: accept the interviewer's recommended answer unless it contradicts the user's pasted prompt or the target repo's docs, which always outrank it. Escalating to the user is allowed but rare: only critical calls (vendor lock-in risk, credentials/API keys, anything the harness cannot know or decide).

## Meta files

Structured files under a run's state directory that both prompt and record every session in the run, enabling the orchestrator and the website to track progress.

## Run manifest

The top-level meta file of a run: its identity, inputs (prompt, route, target repo), and overall status. One per run.

## Session record

The meta file recording one session: its role, status, outcome summary, and transcript pointers. Written only by the orchestrator, transcribed from the session's structured output.

## Execution graph

The dependency graph of tickets derived from the target repo's `.scratch` tickets — their `Blocked by:`, `Type:` and `Status:` lines. Drives which sessions the orchestrator dispatches and what can run in parallel. One graph per `.scratch/<effort>/` directory, stored beside its tickets, and re-derived from them on every tick so that tickets written mid-run join the graph; harness-only fields (session id, in-flight status, gate pointer) are merged in by ticket id. Readiness is derived across every graph in the run at once, so a node in a subgraph and a node in the root graph dispatch side by side. Deciding what is blocked is the graph's own job, and it looks through the whole subtree: a node satisfies a dependency only when it is complete **and** every graph beneath it is terminal, so whatever depended on a node waits for the subgraph that node spawned. Terminal means complete all the way down: a failure anywhere in the subtree keeps dependents blocked for good, exactly as a failed blocker does in its own graph (ADR-0006). Dependencies are never expressed across graphs — the only inter-graph relation is a parent node and the subgraph it spawned.

## Gate

A pause point where the run waits on a human, surfaced through the website. Prototype review, task completion, an escalated question and a user ping are kinds of gate, not separate mechanisms — though a user ping is run-level: it belongs to no node, blocks nothing, and is simply dismissed. A gate blocks only the execution-graph nodes that depend on it; unrelated work keeps running. The gated session stays alive and idle rather than exiting. The website records the user's response, with the verbs the gate's kind accepts; the orchestrator delivers it into the waiting session, which persists it like any other tracker file. The one exception is a task-completion `cannot`, which the loop consumes itself: the node fails with the user's words as its account, and nothing is delivered (ADR-0008). Gates are numbered run-wide and never reopened: a revision raises a fresh gate on the next stale point, so the gate sequence is the review history.

## Harness tools

The tools the harness serves over MCP to the **orchestrator agent only**. Driven sessions get none and are completely harness-agnostic: they run stock skills, know nothing about the harness, and are never asked to call into it. The orchestrator agent uses tools to land its judgments as validated state — emitting a graph when a session that spawns one finishes, marking a prototype node reviewable with its artifact URL, handing a human-only task to the user, escalating a question only the user can answer, pinging the user with something worth knowing, messaging a session that must be nudged, and giving up on a node whose work cannot be done at all.

## Session

One `claude -p` conversation. Conversation boundaries between sessions follow the suite's own chaining rules (`ask-matt`): a grilling session whose outcome requires code continues in the same unbroken conversation through spec and tickets (grill → spec → tickets), then ends. Wayfinder is the exception, per `ask-matt`: its decision tickets produce decisions only, and when the map closes one collapse session (`/to-spec <map>` then `/to-tickets`, same conversation) produces the implementation tickets. One fresh session per implementation ticket; one wayfinder ticket resolved per session.

## Monitored session

A session whose full trace the orchestrator agent reads: grilling and wayfinder sessions, because their outcome spawns a graph. The orchestrator agent answers their questions and judges when they are complete.

## Autonomous session

Every other session (implementation, task, prototype build). The orchestrator agent reads only the **tail** of its trace at each stale point — enough to verify its tracker files were written and to judge whether it finished — rather than the whole stream. A completed prototype session additionally raises a gate for user review.

## Skill roster

The list of available skills and their purposes, always in the orchestrator's context. Sourced from the official skills repo clone at `~/workspace/skills`, engineering/dev skills only.

## Node

One session in a run's graph: exactly one ticket, resolved through exactly one entry skill. The **root node** is the sole exception — the run's first session has no ticket yet, so it carries the pasted prompt instead and lives with the manifest rather than in a graph. The node's type *is* its entry skill, one of `grill-with-docs`, `wayfinder`, `research`, `prototype`, `implement`. Research and prototype tickets are nodes in their own right, dispatched and executed independently rather than folded into a wayfinder session. Node status is the session's status; a node completing says nothing about the subgraph it may have spawned.

## Dispatch

Starting a node's session: the entry skill's invocation and the ticket it points at, and nothing else. The harness never adds obligations, reminders or vocabulary of its own to a session's opening message, and never alters a skill or the agent behind it. A skill behaves in a harness-driven run exactly as it behaves for a human who typed the same thing. Everything the harness needs a session to do that the session did not do on its own is handled afterwards, by the orchestrator, as an **intervention**.

## Subgraph

A graph spawned by a node whose resolution produces further tickets — a grilling node that ends in a spec and tickets, for example. The spawning node is marked complete when its session is; the subgraph is tracked separately and linked to it. A run is not finished while any subgraph beneath it is unfinished.

## Stale

The moment a session's turn ends, read off its stream as the `result` event. Not a heuristic and not a quiet-period timer: the CLI computes it, and the event carries the stop reason and telemetry. Every orchestrator judgment happens at a stale point.

## Intervention

One ephemeral orchestrator-agent invocation and everything it does. Triggered by a session going stale or by a gate response arriving. Interventions for one node never overlap; interventions on different nodes run side by side.

## Tracker file

A file a session writes to carry state and knowledge forward to later sessions in the run: ticket updates, grilling answers, a research report, a prototype decision. Distinct from code files, which the harness has no opinion about. Sessions write tracker files; the orchestrator verifies at each stale point that the ones a node owes were written, and instructs the session to write any that are missing. Verification is always after the fact: the harness never asks for them up front (see **Dispatch**), so an obligation that cannot be checked once a turn has ended is not an obligation the harness holds. The orchestrator never writes them itself; the one amendment to that rule is **takeover**'s, which may correct a lying ticket `Status:` line and append a reconciliation note, nothing else (ADR-0010).

## Owed artifacts

What a node's type is expected to leave behind — its **tracker files** and the edits inside them. Known to the harness, never to the session. Checked at every stale point, and a node cannot be completed while any are missing: the check is a precondition in the tool layer, not advice to the agent. A session that leaves the same ones missing across three consecutive **nudge points** fails; any shrinking of the set is progress and starts the count again.

## Nudge point

A stale point at which the harness refused to complete a node because it still owed an artifact — the only moment where the harness and a session are known to disagree about whether the work is done. What the nudge budget counts, so that a session still being interviewed is never mistaken for one that is stalling (ADR-0004).

## Task resolution mode

Which of three ways a wayfinder `task` ticket gets resolved, decided by the orchestrator agent when it derives the graph, never by the loop and never at dispatch time. **Agent**: work a session can do alone. **User**: work only a human can do — dispatched like agent work all the same, so that when the session hits the human-only wall the orchestrator can raise a task-completion gate and the user's facts have a live session to land in and be persisted by. **Undefined**: not really work at all but a milestone the map surfaced before it could be specified — resolved by re-typing the node as a grilling node over the same ticket (no new ticket is written; ADR-0005), which spawns a subgraph.
