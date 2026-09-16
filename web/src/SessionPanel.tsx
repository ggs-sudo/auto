// The history panel, below the graph: what one node did and what was done
// to it. Every node has two histories — the skill session's own transcript,
// and the orchestrator's interventions on it — so the panel carries a tab
// pair. A takeover key swaps the panel for that consultation's record: what
// was examined, the verdict, the corrections, and the agent's prose.
//
// The transcript renders the way Claude Code renders a session: assistant
// prose with a bullet, messages sent into the session quoted, tool calls as
// one-liners, tool results only when they errored, turn ends as hairlines.
//
// Both histories are chats the user can write into (ADR-0014): the session
// takes their words verbatim at its next turn end, and the orchestrator is
// invoked fresh on the node with them. Each chat names the Claude session id
// behind it, so a conversation can be found — or resumed — outside the site.

import { useLayoutEffect, useState } from "react";
import { useRef } from "react";
import { sendMessage } from "./api";
import { GateCard } from "./GateCard";
import {
  ROOT_KEY,
  cleanConversation,
  clock,
  elapsed,
  gateById,
  graphNodeAt,
  isTakeoverKey,
  money,
  reconciliationAt,
  titleOf,
} from "./derive";
import type { HistoryItem } from "./derive";
import type {
  InterventionRecord,
  MessageTarget,
  NodeStatus,
  ReconciliationRecord,
  RunDetail,
  SessionRecord,
  StreamEvent,
  UserMessage,
} from "./types";

export function SessionPanel({
  run,
  nodeKey,
  session,
  events,
  transcriptError,
  now,
  onAnswered,
}: {
  run: RunDetail | null;
  nodeKey: string;
  session: SessionRecord | undefined;
  events: StreamEvent[];
  transcriptError?: string | null;
  now: number;
  onAnswered: () => void;
}) {
  if (run == null) return null;
  if (isTakeoverKey(nodeKey)) {
    return <TakeoverPanel run={run} nodeKey={nodeKey} />;
  }
  const node = findNode(run, nodeKey);
  if (node == null) {
    return (
      <section className="mct-panel">
        <h3 className="mct-board__title">session</h3>
        <div className="mct-empty">
          <p>Select a node.</p>
        </div>
      </section>
    );
  }
  return (
    <NodePanel
      run={run}
      nodeKey={nodeKey}
      node={node}
      session={session}
      events={events}
      transcriptError={transcriptError}
      now={now}
      onAnswered={onAnswered}
    />
  );
}

type HistoryTab = "session" | "orchestrator";

function NodePanel({
  run,
  nodeKey,
  node,
  session,
  events,
  transcriptError,
  now,
  onAnswered,
}: {
  run: RunDetail;
  nodeKey: string;
  node: PanelNode;
  session: SessionRecord | undefined;
  events: StreamEvent[];
  transcriptError?: string | null;
  now: number;
  onAnswered: () => void;
}) {
  const [tab, setTab] = useState<HistoryTab>("session");
  const gate = gateById(run, node.gate);
  const interventions = run.interventions.filter((record) => record.node === nodeKey);
  const messages = (run.messages ?? []).filter((message) => message.node === nodeKey);
  const composer = (target: MessageTarget) => (
    <Composer
      run={run}
      nodeKey={nodeKey}
      node={node}
      session={session}
      target={target}
      onSent={onAnswered}
    />
  );
  return (
    <section className="mct-panel">
      <h3 className="mct-board__title">session</h3>
      <div className="mct-panel__box">
        <div className="mct-panel__head">
          <span className={`mct-type mct-type--${node.type}`}>{node.type}</span>
          <h2>{node.title}</h2>
          <span className="mct-panel__sub">{node.sub}</span>
          {session != null && (
            <span className="mct-panel__facts">
              <span className={`mct-status mct-status--${session.status}`}>
                {session.status}
              </span>
              <span>{session.telemetry.num_turns ?? 0} turns</span>
              <span>{money(session.telemetry.cost_usd ?? 0)}</span>
              <span>{elapsed(session.started_at, session.ended_at, now)}</span>
              <SessionId id={session.session_id} />
            </span>
          )}
          <div className="mct-tabs">
            <button
              className={`mct-tab ${tab === "session" ? "is-on" : ""}`}
              onClick={() => setTab("session")}
            >
              session
            </button>
            <button
              className={`mct-tab mct-tab--orch ${tab === "orchestrator" ? "is-on" : ""}`}
              onClick={() => setTab("orchestrator")}
            >
              orchestrator{interventions.length > 0 && ` · ${interventions.length}`}
            </button>
          </div>
        </div>
        {gate != null && (
          <GateCard run={run} gate={gate} now={now} onAnswered={onAnswered} />
        )}
        {tab === "session" ? (
          <>
            <SessionTab
              session={session}
              events={events}
              transcriptError={transcriptError}
              blockedBy={node.blockedBy}
            />
            <UndeliveredMessages
              messages={messages.filter(
                (message) =>
                  message.target === "session" &&
                  (message.delivery == null || message.delivery.refused != null),
              )}
            />
            {composer("session")}
          </>
        ) : (
          <>
            <OrchestratorView
              interventions={interventions}
              messages={messages.filter((message) => message.target === "orchestrator")}
            />
            {composer("orchestrator")}
          </>
        )}
      </div>
    </section>
  );
}

function SessionTab({
  session,
  events,
  transcriptError,
  blockedBy,
}: {
  session: SessionRecord | undefined;
  events: StreamEvent[];
  transcriptError?: string | null;
  blockedBy: string[];
}) {
  // The summary block can run very long; it starts folded so the transcript
  // is what the reader lands on.
  const [glanceOpen, setGlanceOpen] = useState(false);
  if (session == null) {
    return (
      <div className="mct-empty">
        <p>No session yet.</p>
        <p className="mct-sub">
          {blockedBy.length > 0
            ? `Waiting on ${blockedBy.join(", ")}.`
            : "Waiting on dispatch."}
        </p>
      </div>
    );
  }
  const hasGlance = session.summary != null || session.highlights.length > 0;
  return (
    <>
      {hasGlance && (
        <button
          className={`mct-glance-toggle ${glanceOpen ? "is-open" : ""}`}
          onClick={() => setGlanceOpen((open) => !open)}
        >
          {glanceOpen ? "▾" : "▸"} summary
        </button>
      )}
      {glanceOpen && session.summary != null && (
        <p className="mct-panel__summary">{session.summary}</p>
      )}
      {glanceOpen && session.highlights.length > 0 && (
        <ul className="mct-glance__list">
          {session.highlights.map((highlight) => (
            <li key={highlight}>{highlight}</li>
          ))}
        </ul>
      )}
      {transcriptError != null && (
        <div className="mct-transcript-err">
          ⚠ transcript can’t be loaded — {transcriptError}; retrying as the run
          moves
        </div>
      )}
      <Conversation
        key={session.session_id}
        events={events}
        running={session.status === "running"}
      />
    </>
  );
}

interface PanelNode {
  type: string;
  title: string;
  sub: string;
  gate: string | null;
  blockedBy: string[];
  status: NodeStatus;
  sessionId: string | null;
}

function findNode(run: RunDetail, key: string): PanelNode | null {
  if (key === ROOT_KEY) {
    const root = run.manifest.root_node;
    return {
      type: root.type ?? "reconciliation",
      title: `root — ${root.type != null ? "the pasted prompt" : "the effort path"}`,
      sub: "lives on the manifest; every graph descends from it",
      gate: root.gate,
      blockedBy: [],
      status: root.status,
      sessionId: root.session_id,
    };
  }
  const at = graphNodeAt(run, key);
  if (at == null) return null;
  return {
    type: at.node.ticket_type,
    title: titleOf(at.node.node_id),
    sub: at.node.ticket,
    gate: at.node.gate,
    blockedBy: at.node.blocked_by,
    status: at.node.status,
    sessionId: at.node.session_id,
  };
}

/** How many history items render at once. A long unattended run accumulates
 * thousands of events; the DOM holds a tail of this size and grows only when
 * the reader asks for more. */
const WINDOW = 250;

function Conversation({
  events,
  running,
}: {
  events: StreamEvent[];
  running: boolean;
}) {
  const box = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const grewFrom = useRef<number | null>(null);
  const [shown, setShown] = useState(WINDOW);
  const items = cleanConversation(events);
  const hidden = Math.max(0, items.length - shown);
  const visible = hidden > 0 ? items.slice(hidden) : items;

  const onScroll = () => {
    const el = box.current;
    if (el != null) {
      pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
    }
  };

  // Follow the tail only while the reader is at it. Revealing earlier rows
  // instead keeps the reader's place: the scroll offset moves by exactly
  // what was prepended.
  useLayoutEffect(() => {
    const el = box.current;
    if (el == null) return;
    if (grewFrom.current != null) {
      el.scrollTop += el.scrollHeight - grewFrom.current;
      grewFrom.current = null;
    } else if (pinned.current) {
      el.scrollTo({ top: el.scrollHeight });
    }
  }, [items.length, visible.length]);

  const showEarlier = () => {
    grewFrom.current = box.current?.scrollHeight ?? null;
    setShown((count) => count + WINDOW);
  };

  return (
    <div className="cc" ref={box} onScroll={onScroll}>
      {hidden > 0 && (
        <button className="mct-earlier" onClick={showEarlier}>
          ↑ show {Math.min(WINDOW, hidden)} earlier · {hidden} above
        </button>
      )}
      {visible.map((item, i) => (
        <HistoryRow key={hidden + i} item={item} />
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

function HistoryRow({ item }: { item: HistoryItem }) {
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
        <div className={`cc-row cc-user ${item.direct ? "cc-user--direct" : ""}`}>
          <span className="cc-bullet">❯</span>
          <div className="cc-text">
            {item.direct && <span className="cc-you">you</span>}
            {item.text}
          </div>
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

// The orchestrator's record for the node: its intervention timeline as a
// chat — the prose judgment, then what it actually did, refusals included —
// with the user's own messages to it interleaved where they were written.
function OrchestratorView({
  interventions,
  messages = [],
}: {
  interventions: InterventionRecord[];
  messages?: UserMessage[];
}) {
  const entries = [
    ...interventions.map((record) => ({
      at: record.started_at,
      key: record.intervention_id,
      node: <InterventionEntry record={record} />,
    })),
    ...messages.map((message) => ({
      at: message.sent_at,
      key: message.message_id,
      node: <MessageEntry message={message} />,
    })),
  ].sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
  if (entries.length === 0) {
    return (
      <div className="cc">
        <div className="cc-row cc-live">
          no orchestrator interventions for this node yet
        </div>
      </div>
    );
  }
  return (
    <div className="cc cc--records">
      {entries.map((entry) => (
        <div key={entry.key} className="cc-orch-entry">
          {entry.node}
        </div>
      ))}
    </div>
  );
}

function InterventionEntry({ record }: { record: InterventionRecord }) {
  return (
    <div className="cc-orch">
      <div className="cc-orch__head">
        <span className="cc-orch__who">⏺ orchestrator</span>
        <span className="cc-orch__meta">
          {record.trigger} · {clock(record.started_at)} · {record.model} ·{" "}
          <SessionId id={record.session_id} />
        </span>
      </div>
      {record.prose != null && (
        <div className="cc-text cc-orch__prose">{record.prose}</div>
      )}
      {record.tool_calls.length === 0 ? (
        <div className="cc-orch__noop">
          {record.trigger === "user-message"
            ? "replied — no action taken on the node"
            : record.ended_at == null
              ? "judging…"
              : "watched — the node is still working; no action"}
        </div>
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
  );
}

/** Where a user message stands: waiting for the loop, taken up, or refused. */
function deliveryNote(message: UserMessage): string {
  if (message.delivery == null) {
    return message.target === "session"
      ? "queued — goes in at the session’s next turn end"
      : "queued — the orchestrator takes it up on its next look";
  }
  if (message.delivery.refused != null) return `not delivered: ${message.delivery.refused}`;
  return message.target === "session" ? "delivered" : "taken up";
}

function MessageEntry({ message }: { message: UserMessage }) {
  const refused = message.delivery?.refused != null;
  return (
    <div className={`cc-msg ${refused ? "is-refused" : ""}`}>
      <div className="cc-orch__head">
        <span className="cc-msg__who">❯ you</span>
        <span className="cc-orch__meta">
          {clock(message.sent_at)} · {deliveryNote(message)}
        </span>
      </div>
      <div className="cc-text">{message.text}</div>
    </div>
  );
}

/** Session messages the transcript cannot show yet — still queued — or
 * never will, because they were refused. Delivered ones are in the
 * transcript itself. */
function UndeliveredMessages({ messages }: { messages: UserMessage[] }) {
  if (messages.length === 0) return null;
  return (
    <div className="cc-msgs">
      {messages.map((message) => (
        <MessageEntry key={message.message_id} message={message} />
      ))}
    </div>
  );
}

/** Why this chat cannot take a message right now, or null when it can. The
 * server checks all of this again; this only spares the user a refusal. */
export function composerBlock(
  run: RunDetail,
  node: { gate: string | null; status: NodeStatus; sessionId: string | null },
  session: SessionRecord | undefined,
): string | null {
  const status = run.manifest.status;
  if (status !== "running" && status !== "gated") {
    return `the run is ${status} — nothing would deliver a message`;
  }
  if (node.gate != null) return "the node is waiting at a gate — answer it above";
  if (session == null || session.status !== "running" || node.status !== "in-progress") {
    return "the node has no live session to message";
  }
  if (node.sessionId !== session.session_id) {
    return "this is not the node’s current session";
  }
  return null;
}

const COMPOSER_PLACEHOLDER: Record<MessageTarget, string> = {
  session: "Write to the session — delivered verbatim at its next turn end.",
  orchestrator:
    "Write to the orchestrator about this node — a fresh agent reads the trace and acts on your words.",
};

function Composer({
  run,
  nodeKey,
  node,
  session,
  target,
  onSent,
}: {
  run: RunDetail;
  nodeKey: string;
  node: PanelNode;
  session: SessionRecord | undefined;
  target: MessageTarget;
  onSent: () => void;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const blocked = composerBlock(run, node, session);

  if (blocked != null) {
    return <div className="mct-composer mct-composer--off">{blocked}</div>;
  }

  const submit = async () => {
    if (session == null || text.trim() === "") return;
    setBusy(true);
    setError(null);
    try {
      await sendMessage(run.manifest.run_id, {
        target,
        node: nodeKey,
        session_id: session.session_id,
        text,
      });
      setText("");
      onSent();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mct-composer">
      <textarea
        className="mct-composer__text"
        aria-label={`message the ${target}`}
        placeholder={COMPOSER_PLACEHOLDER[target]}
        value={text}
        disabled={busy}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
            event.preventDefault();
            void submit();
          }
        }}
      />
      <div className="mct-composer__row">
        <span className="mct-composer__hint">⌘/Ctrl + Enter</span>
        <button
          className={`mct-verb mct-verb--go mct-composer__send--${target}`}
          disabled={busy || text.trim() === ""}
          onClick={() => void submit()}
        >
          send to {target}
        </button>
      </div>
      {error != null && <div className="mct-gate__err">⚠ {error}</div>}
    </div>
  );
}

/** A Claude session id, selectable whole and copyable in one click. */
function SessionId({ id }: { id: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    void navigator.clipboard
      ?.writeText(id)
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1200);
      })
      .catch(() => {});
  };
  return (
    <span className="mct-sid">
      session <code className="mct-sid__id">{id}</code>
      <button className="mct-sid__copy" title="copy the session id" onClick={copy}>
        {copied ? "copied" : "copy"}
      </button>
    </span>
  );
}

function TakeoverPanel({ run, nodeKey }: { run: RunDetail; nodeKey: string }) {
  const record = reconciliationAt(run, nodeKey);
  return (
    <section className="mct-panel">
      <h3 className="mct-board__title">takeover</h3>
      <div className="mct-panel__box">
        {record == null ? (
          <div className="mct-empty">
            <p>This run has no such consultation — its record may not be written yet.</p>
          </div>
        ) : (
          <>
            <div className="mct-panel__head">
              <span className="mct-type mct-type--takeover">takeover</span>
              <h2>{record.reconciliation_id}</h2>
              <span className="mct-panel__facts">
                <Verdict record={record} />
                <span>{record.examined.length} facts examined</span>
                <span>{clock(record.started_at)}</span>
                <span>{record.model}</span>
                <SessionId id={record.session_id} />
              </span>
            </div>
            <TakeoverConsultation record={record} />
          </>
        )}
      </div>
    </section>
  );
}

function Verdict({ record }: { record: ReconciliationRecord }) {
  if (record.verdict != null) {
    return (
      <span className={`cc-tk__verdict cc-tk__verdict--${record.verdict}`}>
        {record.verdict}
      </span>
    );
  }
  return (
    <span className="cc-tk__verdict">
      {record.ended_at == null ? "consulting…" : "no verdict — takeover stopped"}
    </span>
  );
}

function TakeoverConsultation({ record }: { record: ReconciliationRecord }) {
  return (
    <div className="cc cc--records">
      <div className="cc-orch cc-tk">
        {record.prose != null && <div className="cc-text cc-tk__prose">{record.prose}</div>}
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
        {record.ticket_corrections.length > 0 && (
          <ul className="cc-tk__list">
            {record.ticket_corrections.map((correction) => (
              <li key={correction.ticket}>
                <span className="cc-tk__corrnode">{correction.ticket}</span>{" "}
                {correction.prior_status} → {correction.new_status}
                <span className="cc-tk__evidence"> — {correction.evidence}</span>
              </li>
            ))}
          </ul>
        )}
        {(record.blocker_corrections ?? []).length > 0 && (
          <ul className="cc-tk__list">
            {record.blocker_corrections.map((correction) => (
              <li key={correction.ticket}>
                <span className="cc-tk__corrnode">{correction.ticket}</span>{" "}
                blocked by: {correction.prior_blockers} →{" "}
                {correction.new_blockers.length > 0
                  ? correction.new_blockers.join(", ")
                  : "none"}
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
    </div>
  );
}
