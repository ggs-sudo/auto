// The session inspector: what one node is, at a glance and in full. Headline
// facts sit above the conversation because they accumulate while the node is
// still running — which is exactly when they are most useful. The gate card
// is the same one the header shows, answerable here too: reading the session
// is how you decide what to answer, so the box belongs beside it.

import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { GateCard } from "./GateCard";
import {
  ROOT_KEY,
  clock,
  conversation,
  elapsed,
  gateById,
  graphNodeAt,
  money,
  titleOf,
} from "./derive";
import type { DisplayEvent } from "./derive";
import type {
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
      {gate != null && (
        <GateCard run={run} gate={gate} now={now} onAnswered={onAnswered} />
      )}
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
          {transcriptError != null && (
            <div className="mct-transcript-err">
              ⚠ transcript can’t be loaded — {transcriptError}; retrying as the
              run moves
            </div>
          )}
          <Conversation
            key={session.session_id}
            events={events}
            interventions={interventions}
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

/** How many conversation items render at once. A long unattended run
 * accumulates thousands of events; the DOM holds a tail of this size and
 * grows only when the reader asks for more. */
const WINDOW = 250;

function Conversation({
  events,
  interventions,
  running,
}: {
  events: StreamEvent[];
  interventions: InterventionRecord[];
  running: boolean;
}) {
  const box = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const grewFrom = useRef<number | null>(null);
  const [shown, setShown] = useState(WINDOW);
  const items = useMemo(
    () => conversation(events, interventions),
    [events, interventions],
  );
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
    <div className="mct-convo" ref={box} onScroll={onScroll}>
      {hidden > 0 && (
        <button className="mct-earlier" onClick={showEarlier}>
          ↑ show {Math.min(WINDOW, hidden)} earlier · {hidden} above
        </button>
      )}
      {visible.map((item, i) =>
        item.kind === "event" ? (
          <EventRow key={hidden + i} event={item.event} />
        ) : (
          <InterventionRow key={hidden + i} record={item.intervention} />
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
