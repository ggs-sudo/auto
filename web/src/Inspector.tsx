// The session inspector: what one node is, at a glance and in full. Headline
// facts sit above the conversation because they accumulate while the node is
// still running — which is exactly when they are most useful. The gate card
// is read-only here: answering gates is the gate-response ticket's work, and
// until then the card says what is being asked and where to look.

import { useEffect, useRef } from "react";
import {
  GATE_LABEL,
  ROOT_KEY,
  clock,
  conversation,
  elapsed,
  gateById,
  money,
} from "./derive";
import type {
  ConversationItem,
  DisplayEvent,
} from "./derive";
import type {
  Gate,
  InterventionRecord,
  RunDetail,
  SessionRecord,
  StreamEvent,
} from "./types";

export function Inspector({
  run,
  nodeKey,
  session,
  events,
  now,
}: {
  run: RunDetail | null;
  nodeKey: string;
  session: SessionRecord | undefined;
  events: StreamEvent[];
  now: number;
}) {
  if (run == null) {
    return <aside className="mct-inspect" />;
  }
  const node = findNode(run, nodeKey);
  if (node == null) {
    return (
      <aside className="mct-inspect">
        <div className="mct-empty">
          <p>Select a node.</p>
        </div>
      </aside>
    );
  }
  const gate = gateById(run, node.gate);
  const interventions = run.interventions.filter((record) => record.node === nodeKey);
  const running = session?.status === "running";

  return (
    <aside className="mct-inspect">
      {gate != null && <GateCard gate={gate} now={now} />}
      <div className="mct-inspect__head">
        <span className={`mct-type mct-type--${node.type}`}>{node.type}</span>
        <h2>{node.title}</h2>
        <div className="mct-inspect__sub">{node.sub}</div>
      </div>
      {session != null ? (
        <>
          <div className="mct-glance">
            <div className="mct-glance__row">
              <span className={`mct-status mct-status--${session.status}`}>
                {session.status}
              </span>
              <span>{session.telemetry.num_turns ?? 0} turns</span>
              <span>{money(session.telemetry.cost_usd ?? 0)}</span>
              <span>{elapsed(session.started_at, session.ended_at, now)}</span>
            </div>
            {session.summary != null && (
              <p className="mct-glance__summary">{session.summary}</p>
            )}
            {session.highlights.length > 0 && (
              <ul className="mct-glance__list">
                {session.highlights.map((highlight) => (
                  <li key={highlight}>{highlight}</li>
                ))}
              </ul>
            )}
          </div>
          <Conversation
            items={conversation(events, interventions)}
            running={running}
          />
        </>
      ) : (
        <div className="mct-empty">
          <p>No session yet.</p>
          <p className="mct-sub">
            {node.blockedBy.length > 0
              ? `Waiting on ${node.blockedBy.join(", ")}.`
              : "Waiting on dispatch."}
          </p>
        </div>
      )}
    </aside>
  );
}

interface InspectedNode {
  type: string;
  title: string;
  sub: string;
  gate: string | null;
  blockedBy: string[];
}

function findNode(run: RunDetail, key: string): InspectedNode | null {
  if (key === ROOT_KEY) {
    const root = run.manifest.root_node;
    return {
      type: root.type,
      title: "root — the pasted prompt",
      sub: "lives on the manifest; every graph descends from it",
      gate: root.gate,
      blockedBy: [],
    };
  }
  const slash = key.indexOf("/");
  const graph = run.graphs.find((g) => g.graph_id === key.slice(0, slash));
  const node = graph?.nodes.find((n) => n.node_id === key.slice(slash + 1));
  if (graph == null || node == null) return null;
  return {
    type: node.ticket_type,
    title: node.node_id.replace(/^\d+-/, "").replace(/-/g, " "),
    sub: node.ticket,
    gate: node.gate,
    blockedBy: node.blocked_by,
  };
}

function GateCard({ gate, now }: { gate: Gate; now: number }) {
  return (
    <div className={`mct-gate mct-gate--${gate.kind}`}>
      <div className="mct-gate__kind">
        gate #{gate.sequence} · {GATE_LABEL[gate.kind]} · waiting{" "}
        {elapsed(gate.raised_at, null, now)}
      </div>
      <p className="mct-gate__q">{gate.question}</p>
      {gate.artifact != null && (
        <a
          className="mct-gate__artifact"
          href={gate.artifact}
          target="_blank"
          rel="noreferrer"
        >
          open the artifact ↗ <span>{gate.artifact}</span>
        </a>
      )}
      <div className="mct-gate__note">
        Answer by writing the gate's response file — answering from here is on
        its way.
      </div>
    </div>
  );
}

function Conversation({
  items,
  running,
}: {
  items: ConversationItem[];
  running: boolean;
}) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    box.current?.scrollTo({ top: box.current.scrollHeight });
  }, [items.length]);

  return (
    <div className="mct-convo" ref={box}>
      {items.map((item, i) =>
        item.kind === "event" ? (
          <EventRow key={i} event={item.event} />
        ) : (
          <InterventionRow key={i} record={item.intervention} />
        ),
      )}
      {running && (
        <div className="mct-ev mct-ev--live">
          <div className="mct-ev__gutter">
            <span className="mct-ev__time">live</span>
          </div>
          <div className="mct-ev__body">
            <span className="mct-caret" /> tailing transcript…
          </div>
        </div>
      )}
    </div>
  );
}

function EventRow({ event }: { event: DisplayEvent }) {
  return (
    <div className={`mct-ev mct-ev--${event.type}`}>
      <div className="mct-ev__gutter">
        <span className="mct-ev__kind">{event.label}</span>
      </div>
      <div className="mct-ev__body">
        {event.isError ? "⚠ " : ""}
        {event.text}
      </div>
    </div>
  );
}

// The orchestrator's own move, rendered inline where it happened and styled
// apart from session content: reading a run means reading why the
// orchestrator answered as it did, in place.
function InterventionRow({ record }: { record: InterventionRecord }) {
  return (
    <div className="mct-iv">
      <div className="mct-iv__head">
        <span>orchestrator · {record.intervention_id}</span>
        <span className="mct-iv__meta">
          {record.trigger} · {clock(record.started_at)} · {record.model}
        </span>
      </div>
      {record.prose != null && <p className="mct-iv__prose">{record.prose}</p>}
      {record.tool_calls.length === 0 ? (
        <div className="mct-iv__call mct-iv__call--noop">
          no action — the node is still working
        </div>
      ) : (
        record.tool_calls.map((call, i) => (
          <div
            key={i}
            className={`mct-iv__call ${call.refused != null ? "mct-iv__call--refused" : ""}`}
          >
            <span className="mct-iv__tool">{call.tool}</span>
            {typeof call.arguments.message === "string" && (
              <span className="mct-iv__msg">“{call.arguments.message}”</span>
            )}
            {call.refused != null && (
              <span className="mct-iv__refusal">refused: {call.refused}</span>
            )}
          </div>
        ))
      )}
    </div>
  );
}
