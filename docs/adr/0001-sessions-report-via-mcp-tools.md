---
status: superseded by ADR-0002
---

# Sessions report via harness-served MCP tools, not `--json-schema` output

Issue #2's research recommended a `--json-schema` final-message contract for detecting session status headlessly. We reversed that: the orchestrator serves every session two MCP tools — `report` (mandatory final structured status; a session that exits without calling it is marked failed) and `ask` (blocking Q&A, answered by the orchestrator per the answer policy, escalating to a gate only when policy demands the user). Tool calls arrive in the orchestrator process as validated typed input with no stdout parsing, and a blocking tool can return answers into a still-live session — keeping the planning session literally unbroken — which a final-message contract cannot do. The cost is an extra moving part (an MCP server config on every `claude -p` invocation), accepted deliberately.
