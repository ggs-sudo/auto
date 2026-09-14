// The history panel, below the graph: what one node did and what was done
// to it. Every node has two histories — the skill session's own transcript,
// and the orchestrator's interventions on it — so the panel carries a tab
// pair. A takeover key swaps the panel for that consultation's record: what
// was examined, the verdict, the corrections, and the agent's prose.
//
// The transcript renders the way Claude Code renders a session: assistant
// prose with a bullet, messages sent into the session quoted, tool calls as
// one-liners, tool results only when they errored, turn ends as hairlines.

import { useLayoutEffect, useState } from "react";
import { useRef } from "react";
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
  ReconciliationRecord,
  RunDetail,
  SessionRecord,
  StreamEvent,
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
          <SessionTab
            session={session}
            events={events}
            transcriptError={transcriptError}
            blockedBy={node.blockedBy}
          />
        ) : (
          <OrchestratorView interventions={interventions} />
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

// The orchestrator's record for the node: its intervention timeline as a
// chat — the prose judgment, then what it actually did, refusals included.
function OrchestratorView({
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
    <div className="cc cc--records">
      {interventions.map((record) => (
        <div key={record.intervention_id} className="cc-orch">
          <div className="cc-orch__head">
            <span className="cc-orch__who">⏺ orchestrator</span>
            <span className="cc-orch__meta">
              {record.trigger} · {clock(record.started_at)} · {record.model}
            </span>
          </div>
          {record.prose != null && (
            <div className="cc-text cc-orch__prose">{record.prose}</div>
          )}
          {record.tool_calls.length === 0 ? (
            <div className="cc-orch__noop">
              watched — the node is still working; no action
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
      ))}
    </div>
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
