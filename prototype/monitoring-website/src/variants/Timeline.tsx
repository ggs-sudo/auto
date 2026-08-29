// PROTOTYPE variant C — "Timeline": the run as a horizontal time axis. One swimlane per node,
// bars for sessions, gates as vertical markers where the run stopped to wait for a human.
// Conversation lives in a bottom drawer, so the whole run stays visible while you read one session.
import { useEffect, useRef, useState } from "react";
import { useLiveRun } from "../mock/live";
import { runSummaries, runs } from "../mock/data";
import {
  GATE_LABEL,
  PROGRESS,
  clock,
  elapsed,
  gateFor,
  money,
  nodeKey,
  openGates,
  sessionFor,
} from "../mock/format";
import type { Gate, GraphNode, TranscriptEvent } from "../mock/schema";
import "./timeline.css";

export function Timeline() {
  const run = useLiveRun();
  const [sel, setSel] = useState<string | null>("onboarding-revamp/0004-grill-invite-seats");
  const p = PROGRESS(run);
  const gates = openGates(run);

  const t0 = Date.parse(run.manifest.created_at);
  const t1 = run.now.getTime();
  const span = t1 - t0;
  const pct = (iso: string | null) => ((iso ? Date.parse(iso) : t1) - t0) / span;

  const ticks: number[] = [];
  for (let t = t0; t <= t1; t += 30 * 60_000) ticks.push(t);

  const selNode = sel
    ? run.graphs
        .find((g) => g.graph_id === sel.split("/")[0])!
        .nodes.find((n) => n.id === sel.split("/")[1])!
    : null;
  const selGraphId = sel?.split("/")[0] ?? "";
  const selSession = selNode ? sessionFor(run, selNode) : undefined;
  const selGate = selNode ? gateFor(run, selGraphId, selNode.id) : undefined;

  return (
    <div className="tm">
      <header className="tm-top">
        <div className="tm-brand">auto</div>
        <div className="tm-runs">
          {runs.map((r) => (
            <button
              key={r.run_id}
              className={`tm-runtab ${r.run_id === run.manifest.run_id ? "is-on" : ""}`}
              title={r.prompt}
            >
              <i className={`tm-pip tm-pip--${r.status}`} />
              {r.run_id.slice(9)}
              {runSummaries[r.run_id].openGates > 0 && (
                <b>{runSummaries[r.run_id].openGates}</b>
              )}
            </button>
          ))}
        </div>
        <div className="tm-now">
          <span className="tm-live" /> {clock(run.now.toISOString())} · {elapsed(run.manifest.created_at, run.now)} in
        </div>
      </header>

      <section className="tm-banner">
        <p className="tm-prompt">{run.manifest.prompt}</p>
        <div className="tm-facts">
          <span>
            <b>{run.manifest.route}</b> route
          </span>
          <span>
            <b>{run.manifest.target_repo}</b>
          </span>
          <span>
            <b>
              {p.done}/{p.total}
            </b>{" "}
            nodes
          </span>
          <span>
            <b>{p.running}</b> sessions live
          </span>
          <span>
            <b>{money(p.cost)}</b> spend
          </span>
          <span className={gates.length ? "tm-alert" : ""}>
            <b>{gates.length}</b> gates open
          </span>
        </div>
      </section>

      <div className="tm-scroll">
        <div className="tm-axis">
          <div className="tm-axis__label" />
          <div className="tm-axis__track">
            {ticks.map((t) => (
              <span key={t} className="tm-tick" style={{ left: `${((t - t0) / span) * 100}%` }}>
                {clock(new Date(t).toISOString())}
              </span>
            ))}
            {run.gates.map((g) => (
              <span
                key={g.seq}
                className={`tm-gatemark ${g.response ? "is-answered" : ""} tm-gatemark--${g.kind}`}
                style={{ left: `${pct(g.opened_at) * 100}%` }}
                onClick={() => g.on_node && setSel(nodeKey(g.graph_id!, g.on_node))}
                title={g.question}
              >
                ◆ #{g.seq} {GATE_LABEL[g.kind]}
              </span>
            ))}
          </div>
        </div>

        {run.graphs.map((g) => (
          <div className="tm-group" key={g.graph_id}>
            <div className="tm-group__title">
              <span>.scratch/{g.graph_id}/</span>
              {g.spawned_by_node && <em>spawned by {g.spawned_by_node}</em>}
            </div>
            {g.nodes.map((n) => {
              const s = sessionFor(run, n);
              const gate = gateFor(run, g.graph_id, n.id);
              const key = nodeKey(g.graph_id, n.id);
              const left = s ? pct(s.started_at) : 1;
              const right = s ? pct(s.ended_at) : 1;
              return (
                <div
                  className={`tm-lane ${sel === key ? "is-on" : ""}`}
                  key={n.id}
                  onClick={() => setSel(key)}
                >
                  <div className="tm-lane__label">
                    <span className={`tm-chip tm-chip--${n.type}`}>{n.type[0].toUpperCase()}</span>
                    <span className="tm-lane__title">{n.title}</span>
                    {n.spawned_graph && n.spawned_graph !== g.graph_id && <em>↳</em>}
                  </div>
                  <div className="tm-lane__track">
                    {run.graphs
                      .find((x) => x.graph_id === g.graph_id)!
                      .nodes.filter((d) => n.deps.includes(d.id))
                      .map((d) => {
                        const ds = sessionFor(run, d);
                        return ds?.ended_at ? (
                          <span key={d.id} className="tm-dep" style={{ left: `${pct(ds.ended_at) * 100}%` }} />
                        ) : null;
                      })}
                    {s ? (
                      <div
                        className={`tm-bar tm-bar--${n.type} ${s.status === "running" ? "is-live" : ""} ${
                          gate ? "is-gated" : ""
                        }`}
                        style={{ left: `${left * 100}%`, width: `${Math.max(right - left, 0.012) * 100}%` }}
                      >
                        <span className="tm-bar__txt">
                          {elapsed(s.started_at, s.ended_at ?? run.now)} · {s.num_turns}t · {money(s.cost_usd)}
                        </span>
                      </div>
                    ) : (
                      <div className="tm-queued">
                        queued · waiting on {n.deps.length} dep{n.deps.length === 1 ? "" : "s"}
                      </div>
                    )}
                    {gate && (
                      <div
                        className="tm-wait"
                        style={{ left: `${pct(gate.opened_at) * 100}%`, width: `${(1 - pct(gate.opened_at)) * 100}%` }}
                      >
                        waiting on you · {elapsed(gate.opened_at, run.now)}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        ))}
      </div>

      {selNode && (
        <div className="tm-drawer">
          <div className="tm-drawer__head">
            <span className={`tm-chip tm-chip--${selNode.type}`}>{selNode.type}</span>
            <h2>{selNode.title}</h2>
            <span className="tm-drawer__id">
              {selGraphId}/{selNode.id}
            </span>
            <button className="tm-x" onClick={() => setSel(null)}>
              ✕
            </button>
          </div>
          <div className="tm-drawer__body">
            <div className="tm-drawer__left">
              {selSession ? (
                <>
                  <div className="tm-metrics">
                    <div>
                      <b>{selSession.status}</b>
                      <span>status</span>
                    </div>
                    <div>
                      <b>{elapsed(selSession.started_at, selSession.ended_at ?? run.now)}</b>
                      <span>elapsed</span>
                    </div>
                    <div>
                      <b>{selSession.num_turns}</b>
                      <span>turns</span>
                    </div>
                    <div>
                      <b>{money(selSession.cost_usd)}</b>
                      <span>spend</span>
                    </div>
                  </div>
                  <p className="tm-summary">{selSession.summary}</p>
                  <ul className="tm-hl">
                    {selSession.highlights.map((h) => (
                      <li key={h}>{h}</li>
                    ))}
                  </ul>
                </>
              ) : (
                <p className="tm-summary">Not dispatched yet — blocked by {selNode.deps.join(", ") || "nothing"}.</p>
              )}
              {selGate && <GateCard gate={selGate} respond={run.respond} now={run.now} />}
            </div>
            <div className="tm-drawer__right">
              {selSession ? (
                <Chat events={run.transcripts[selSession.session_id] ?? []} live={selSession.status === "running"} />
              ) : (
                <div className="tm-nochat">no conversation yet</div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function GateCard({
  gate,
  respond,
  now,
}: {
  gate: Gate;
  respond: (seq: number, r: any) => void;
  now: Date;
}) {
  const [text, setText] = useState("");
  const send = (decision: string) =>
    respond(gate.seq, { responded_at: now.toISOString(), decision, text });
  return (
    <div className="tm-gate">
      <div className="tm-gate__head">
        ◆ gate #{gate.seq} · {GATE_LABEL[gate.kind]} · waiting {elapsed(gate.opened_at, now)}
      </div>
      <p>{gate.question}</p>
      {gate.artifact && (
        <a href={gate.artifact} target="_blank" rel="noreferrer">
          {gate.artifact} ↗
        </a>
      )}
      <textarea value={text} onChange={(e) => setText(e.target.value)} placeholder="notes / answer…" />
      <div className="tm-gate__acts">
        {gate.kind === "prototype-review" && (
          <>
            <button className="tm-btn tm-btn--go" onClick={() => send("approve")}>
              Approve
            </button>
            <button className="tm-btn" disabled={!text.trim()} onClick={() => send("revise")}>
              Revise
            </button>
          </>
        )}
        {gate.kind === "task-completion" && (
          <>
            <button className="tm-btn tm-btn--go" disabled={!text.trim()} onClick={() => send("done")}>
              Done
            </button>
            <button className="tm-btn" onClick={() => send("cannot")}>
              Can't
            </button>
          </>
        )}
        {(gate.kind === "escalated-question" || gate.kind === "ping") && (
          <button className="tm-btn tm-btn--go" disabled={!text.trim()} onClick={() => send("answered")}>
            Answer
          </button>
        )}
      </div>
    </div>
  );
}

function Chat({ events, live }: { events: TranscriptEvent[]; live: boolean }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    box.current?.scrollTo({ top: box.current.scrollHeight, behavior: "smooth" });
  }, [events.length]);
  return (
    <div className="tm-chat" ref={box}>
      {events.map((e, i) => (
        <div key={i} className={`tm-msg tm-msg--${e.kind}`}>
          <span className="tm-msg__meta">
            {clock(e.t)} {e.tool ?? e.kind}
          </span>
          <div className="tm-msg__text">{e.text}</div>
        </div>
      ))}
      {live && <div className="tm-msg tm-msg--tail">▌ streaming…</div>}
    </div>
  );
}
