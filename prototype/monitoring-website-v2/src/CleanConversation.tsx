// PROTOTYPE — throwaway. The two history views for a node, iteration on
// variant A:
//  - CleanConversation: the SKILL session transcript rendered the way
//    Claude Code renders a session — assistant prose with a ⏺ bullet, user
//    (orchestrator-sent) messages quoted, tool calls as dim one-liners,
//    tool results hidden unless they errored, turn ends as hairlines.
//  - OrchestratorView: the ORCHESTRATOR's own record for the node — its
//    intervention timeline (prose judgment + what it did), as a chat.

import { useLayoutEffect, useRef, useState } from "react";
import { clock, money } from "../derive";
import type { InterventionRecord, StreamEvent } from "../types";
import type { ReconciliationRecord } from "./shared";
import "./clean-convo.css";

type CleanItem =
  | { kind: "user"; text: string }
  | { kind: "assistant"; text: string }
  | { kind: "tool"; name: string; summary: string }
  | { kind: "error"; text: string }
  | { kind: "turn"; text: string };

const truncate = (text: string, max: number) =>
  text.length > max ? `${text.slice(0, max)}…` : text;

/** The primary argument is the summary — Read(file), Bash(command) — the
 * rest is noise at reading distance. */
function toolSummary(input: Record<string, unknown> | undefined): string {
  if (input == null) return "";
  const preferred = ["file_path", "command", "pattern", "url", "path", "query", "skill"];
  for (const key of preferred) {
    if (typeof input[key] === "string") return input[key];
  }
  const first = Object.values(input).find((v) => typeof v === "string");
  return typeof first === "string" ? first : "";
}

function flatten(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content))
    return content
      .map((c) => (typeof c === "object" && c !== null && "text" in c ? String(c.text) : ""))
      .join("\n");
  return "";
}

function cleanItems(events: StreamEvent[]): CleanItem[] {
  const items: CleanItem[] = [];
  for (const event of events) {
    if (event.type === "result") {
      const cost =
        typeof event.total_cost_usd === "number" ? money(event.total_cost_usd) : "";
      items.push({ kind: "turn", text: `turn ended${cost ? ` · ${cost}` : ""}` });
      continue;
    }
    if (event.type !== "user" && event.type !== "assistant") continue;
    for (const block of event.message?.content ?? []) {
      if (block.type === "text" && block.text) {
        // A Skill invocation injects the skill's whole instruction text as a
        // user event; the ⏺ Skill(name) one-liner already says it happened.
        if (event.type === "user" && block.text.startsWith("Base directory for this skill:"))
          continue;
        items.push({
          kind: event.type === "user" ? "user" : "assistant",
          text: block.text,
        });
      } else if (block.type === "tool_use" && event.type === "assistant") {
        items.push({
          kind: "tool",
          name: block.name ?? "tool",
          summary: truncate(toolSummary(block.input), 110),
        });
      } else if (block.type === "tool_result") {
        // Results are noise unless they broke something.
        if ((block as { is_error?: unknown }).is_error === true) {
          items.push({ kind: "error", text: truncate(flatten(block.content), 300) });
        }
      }
    }
  }
  return items;
}

/** Tail window — a long run accumulates thousands of items. */
const WINDOW = 300;

export function CleanConversation({
  events,
  running,
}: {
  events: StreamEvent[];
  running: boolean;
}) {
  const box = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const [shown, setShown] = useState(WINDOW);
  const items = cleanItems(events);
  const hidden = Math.max(0, items.length - shown);
  const visible = hidden > 0 ? items.slice(hidden) : items;

  const onScroll = () => {
    const el = box.current;
    if (el != null)
      pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
  };
  useLayoutEffect(() => {
    const el = box.current;
    if (el != null && pinned.current) el.scrollTo({ top: el.scrollHeight });
  }, [items.length, visible.length]);

  return (
    <div className="cc" ref={box} onScroll={onScroll}>
      {hidden > 0 && (
        <button className="mct-earlier" onClick={() => setShown((n) => n + WINDOW)}>
          ↑ show {Math.min(WINDOW, hidden)} earlier · {hidden} above
        </button>
      )}
      {visible.map((item, i) => (
        <CleanRow key={hidden + i} item={item} />
      ))}
      {running && (
        <div className="cc-row cc-live">
          <span className="mct-caret" /> tailing transcript…
        </div>
      )}
      {!running && items.length === 0 && (
        <div className="cc-row cc-live">no conversation captured</div>
      )}
    </div>
  );
}

function CleanRow({ item }: { item: CleanItem }) {
  switch (item.kind) {
    case "assistant":
      return (
        <div className="cc-row cc-assistant">
          <span className="cc-bullet">⏺</span>
          <div className="cc-text">{item.text}</div>
        </div>
      );
    case "user":
      return (
        <div className="cc-row cc-user">
          <span className="cc-bullet">❯</span>
          <div className="cc-text">{item.text}</div>
        </div>
      );
    case "tool":
      return (
        <div className="cc-row cc-tool">
          <span className="cc-bullet">⏺</span>
          <div className="cc-toolline">
            <span className="cc-toolname">{item.name}</span>
            {item.summary && <span className="cc-toolarg">({item.summary})</span>}
          </div>
        </div>
      );
    case "error":
      return (
        <div className="cc-row cc-error">
          <span className="cc-bullet">⎿</span>
          <div className="cc-text">⚠ {item.text}</div>
        </div>
      );
    case "turn":
      return (
        <div className="cc-turn">
          <span>{item.text}</span>
        </div>
      );
  }
}

// ---------------------------------------------------------------------------

/** The takeover trail: one entry per reconciliation consultation — what was
 * examined, the verdict, the corrections, and the consulting agent's prose. */
export function TakeoverView({
  reconciliations,
}: {
  reconciliations: ReconciliationRecord[];
}) {
  return (
    <div className="cc cc--orch">
      {reconciliations.map((record) => (
        <div key={record.reconciliation_id} className="cc-orch cc-tk">
          <div className="cc-orch__head">
            <span className="cc-tk__who">⏺ takeover · {record.reconciliation_id}</span>
            <span className="cc-orch__meta">
              {clock(record.started_at)} · {record.model}
            </span>
          </div>
          <div className="cc-tk__facts">
            <span className={`cc-tk__verdict cc-tk__verdict--${record.verdict}`}>
              {record.verdict}
            </span>
            <span>{record.examined.length} facts examined</span>
            {record.corrections.length > 0 && (
              <span>{record.corrections.length} corrections</span>
            )}
          </div>
          {record.prose != null && (
            <div className="cc-text cc-tk__prose">{record.prose}</div>
          )}
          {record.corrections.length > 0 && (
            <ul className="cc-tk__list">
              {record.corrections.map((correction) => (
                <li key={correction.node}>
                  <span className="cc-tk__corrnode">{correction.node}</span>{" "}
                  {correction.prior_status} → {correction.new_status}
                  <span className="cc-tk__evidence"> — {correction.evidence}</span>
                </li>
              ))}
            </ul>
          )}
          {record.tool_calls.map((call, i) =>
            typeof call.arguments.message === "string" ? (
              <div key={i} className="cc-orch__send cc-tk__send">
                <span className="cc-orch__sendlabel">→ {call.tool}</span>
                <div className="cc-text">{call.arguments.message}</div>
              </div>
            ) : (
              <div key={i} className="cc-row cc-tool">
                <span className="cc-bullet">⏺</span>
                <div className="cc-toolline">
                  <span className="cc-toolname">{call.tool}</span>
                  {call.refused != null && (
                    <span className="cc-orch__refusal"> — refused: {call.refused}</span>
                  )}
                </div>
              </div>
            ),
          )}
        </div>
      ))}
    </div>
  );
}

export function OrchestratorView({
  interventions,
}: {
  interventions: InterventionRecord[];
}) {
  if (interventions.length === 0) {
    return (
      <div className="cc">
        <div className="cc-row cc-live">
          no orchestrator interventions for this node yet
        </div>
      </div>
    );
  }
  return (
    <div className="cc cc--orch">
      {interventions.map((record) => (
        <div key={record.intervention_id} className="cc-orch">
          <div className="cc-orch__head">
            <span className="cc-orch__who">⏺ orchestrator</span>
            <span className="cc-orch__meta">
              {record.trigger} · {clock(record.started_at)} · {record.model}
            </span>
          </div>
          {record.prose != null && <div className="cc-text cc-orch__prose">{record.prose}</div>}
          {record.tool_calls.length === 0 ? (
            <div className="cc-orch__noop">watched — the node is still working; no action</div>
          ) : (
            record.tool_calls.map((call, i) =>
              typeof call.arguments.message === "string" ? (
                <div
                  key={i}
                  className={`cc-orch__send ${call.refused != null ? "is-refused" : ""}`}
                >
                  <span className="cc-orch__sendlabel">
                    {call.refused != null ? `✗ ${call.tool} refused` : `→ ${call.tool}`}
                  </span>
                  <div className="cc-text">{call.arguments.message}</div>
                  {call.refused != null && (
                    <div className="cc-orch__refusal">{call.refused}</div>
                  )}
                </div>
              ) : (
                <div key={i} className="cc-row cc-tool">
                  <span className="cc-bullet">⏺</span>
                  <div className="cc-toolline">
                    <span className="cc-toolname">{call.tool}</span>
                    {call.refused != null && (
                      <span className="cc-orch__refusal"> — refused: {call.refused}</span>
                    )}
                  </div>
                </div>
              ),
            )
          )}
        </div>
      ))}
    </div>
  );
}
