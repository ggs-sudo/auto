// PROTOTYPE variant D — "Gate inbox": the website as a triage queue, not a dashboard.
// The premise: an unattended harness only needs you when it's stuck, so what needs you is the page,
// and the run itself is browsable behind it. Light, reading-first, one thing in focus at a time.
import { useState } from "react";
import { useLiveRun } from "../mock/live";
import { runSummaries, runs } from "../mock/data";
import {
  GATE_LABEL,
  PROGRESS,
  clock,
  elapsed,
  isReady,
  money,
  openGates,
  sessionFor,
  stamp,
} from "../mock/format";
import type { Gate, SessionRecord, TranscriptEvent } from "../mock/schema";
import "./inbox.css";

type Focus = { kind: "gate"; seq: number } | { kind: "session"; id: string } | { kind: "run" };
type Pane = "needs-you" | "activity" | "runs";

export function Inbox() {
  const run = useLiveRun();
  const [pane, setPane] = useState<Pane>("needs-you");
  const [focus, setFocus] = useState<Focus>({ kind: "gate", seq: 1 });
  const gates = openGates(run);
  const p = PROGRESS(run);

  const sessionsByRecency = [...run.sessions].sort(
    (a, b) => Date.parse(b.ended_at ?? run.now.toISOString()) - Date.parse(a.ended_at ?? run.now.toISOString()),
  );

  return (
    <div className="ib">
      <header className="ib-top">
        <div className="ib-top__left">
          <h1>auto</h1>
          <span className="ib-top__run">{run.manifest.run_id}</span>
        </div>
        <div className="ib-top__stats">
          <span className="ib-stat">
            <b>{p.running}</b> working
          </span>
          <span className="ib-stat">
            <b>
              {p.done}/{p.total}
            </b>{" "}
            done
          </span>
          <span className="ib-stat">
            <b>{money(p.cost)}</b> spent
          </span>
          <span className={`ib-stat ${gates.length ? "is-hot" : ""}`}>
            <b>{gates.length}</b> waiting on you
          </span>
        </div>
      </header>

      <div className="ib-body">
        <nav className="ib-list">
          <div className="ib-list__tabs">
            {(
              [
                ["needs-you", `Needs you ${gates.length ? `(${gates.length})` : ""}`],
                ["activity", "Activity"],
                ["runs", "Runs"],
              ] as [Pane, string][]
            ).map(([k, label]) => (
              <button key={k} className={pane === k ? "is-on" : ""} onClick={() => setPane(k)}>
                {label}
              </button>
            ))}
          </div>

          <div className="ib-list__scroll">
            {pane === "needs-you" && (
              <>
                {gates.map((g) => (
                  <button
                    key={g.seq}
                    className={`ib-item ib-item--${g.kind} ${
                      focus.kind === "gate" && focus.seq === g.seq ? "is-on" : ""
                    }`}
                    onClick={() => setFocus({ kind: "gate", seq: g.seq })}
                  >
                    <div className="ib-item__kind">{GATE_LABEL[g.kind]}</div>
                    <div className="ib-item__q">{g.question}</div>
                    <div className="ib-item__meta">
                      <span>{g.on_node ?? "run-level"}</span>
                      <span>waiting {elapsed(g.opened_at, run.now)}</span>
                      {g.blocking.length > 0 && <span className="ib-hold">holds {g.blocking.length}</span>}
                    </div>
                  </button>
                ))}
                {run.gates
                  .filter((g) => g.response)
                  .map((g) => (
                    <button
                      key={g.seq}
                      className={`ib-item is-done ${focus.kind === "gate" && focus.seq === g.seq ? "is-on" : ""}`}
                      onClick={() => setFocus({ kind: "gate", seq: g.seq })}
                    >
                      <div className="ib-item__kind">✓ {GATE_LABEL[g.kind]}</div>
                      <div className="ib-item__q">{g.question}</div>
                      <div className="ib-item__meta">
                        <span>answered {stamp(g.response!.responded_at)}</span>
                      </div>
                    </button>
                  ))}
              </>
            )}

            {pane === "activity" &&
              sessionsByRecency.map((s) => (
                <button
                  key={s.session_id}
                  className={`ib-item ${focus.kind === "session" && focus.id === s.session_id ? "is-on" : ""} ${
                    s.status === "running" ? "is-live" : ""
                  }`}
                  onClick={() => setFocus({ kind: "session", id: s.session_id })}
                >
                  <div className="ib-item__kind">
                    {s.role} {s.status === "running" && <i className="ib-pulse" />}
                  </div>
                  <div className="ib-item__q">{s.summary}</div>
                  <div className="ib-item__meta">
                    <span>{s.ticket}</span>
                    <span>{elapsed(s.started_at, s.ended_at ?? run.now)}</span>
                    <span>{money(s.cost_usd)}</span>
                  </div>
                </button>
              ))}

            {pane === "runs" &&
              runs.map((r) => {
                const sum = runSummaries[r.run_id];
                return (
                  <button
                    key={r.run_id}
                    className={`ib-item ${r.run_id === run.manifest.run_id && focus.kind === "run" ? "is-on" : ""}`}
                    onClick={() => setFocus({ kind: "run" })}
                  >
                    <div className="ib-item__kind">
                      <span className={`ib-state ib-state--${r.status}`}>{r.status}</span> {r.run_id.slice(9)}
                    </div>
                    <div className="ib-item__q">{r.prompt}</div>
                    <div className="ib-item__meta">
                      <span>
                        {sum.done}/{sum.nodes} nodes
                      </span>
                      <span>{money(sum.cost)}</span>
                      <span>{stamp(r.created_at)}</span>
                    </div>
                  </button>
                );
              })}
          </div>
        </nav>

        <main className="ib-focus">
          {focus.kind === "gate" && <GateView gate={run.gates.find((g) => g.seq === focus.seq)!} run={run} />}
          {focus.kind === "session" && (
            <SessionView
              s={run.sessions.find((x) => x.session_id === focus.id)!}
              events={run.transcripts[focus.id] ?? []}
              now={run.now}
            />
          )}
          {focus.kind === "run" && <RunView run={run} />}
        </main>
      </div>
    </div>
  );
}

function GateView({ gate, run }: { gate: Gate; run: ReturnType<typeof useLiveRun> }) {
  const [text, setText] = useState("");
  const session = run.sessions.find((s) => s.session_id === gate.opened_by_session);
  const tail = (session ? run.transcripts[session.session_id] ?? [] : []).slice(-4);
  const send = (decision: string) =>
    run.respond(gate.seq, { responded_at: run.now.toISOString(), decision: decision as any, text });

  return (
    <article className="ib-article">
      <div className={`ib-eyebrow ib-eyebrow--${gate.kind}`}>
        gate #{gate.seq} · {GATE_LABEL[gate.kind]}
        {!gate.response && <span> · waiting {elapsed(gate.opened_at, run.now)}</span>}
      </div>
      <h2 className="ib-question">{gate.question}</h2>

      {gate.artifact && (
        <a className="ib-artifact" href={gate.artifact} target="_blank" rel="noreferrer">
          <div className="ib-artifact__frame">
            <span>▤</span>
          </div>
          <div>
            <b>Open the prototype</b>
            <span>{gate.artifact}</span>
          </div>
        </a>
      )}

      <section className="ib-context">
        <h3>Why it stopped</h3>
        {session && (
          <p className="ib-context__line">
            <b>{session.ticket}</b> · {session.role} session, {session.num_turns} turns in. It is still alive
            and idle — the answer goes straight back into the conversation.
          </p>
        )}
        <div className="ib-excerpt">
          {tail.map((e, i) => (
            <div key={i} className={`ib-ex ib-ex--${e.kind}`}>
              <span>{clock(e.t)}</span>
              <p>{e.text}</p>
            </div>
          ))}
        </div>
        {gate.blocking.length > 0 && (
          <p className="ib-blocking">
            Holding up <b>{gate.blocking.join(", ")}</b>. Everything else in the run keeps going.
          </p>
        )}
      </section>

      {gate.response ? (
        <section className="ib-answered">
          <h3>Answered {stamp(gate.response.responded_at)}</h3>
          <p className="ib-decision">{gate.response.decision}</p>
          {gate.response.text && <p>{gate.response.text}</p>}
        </section>
      ) : (
        <section className="ib-respond">
          <h3>Your call</h3>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={
              gate.kind === "prototype-review"
                ? "e.g. C, but with B's first screen — the stepper is too many clicks."
                : "Type your answer. It goes back to the waiting session verbatim."
            }
          />
          <div className="ib-actions">
            {gate.kind === "prototype-review" && (
              <>
                <button className="ib-btn ib-btn--primary" onClick={() => send("approve")}>
                  Approve &amp; continue
                </button>
                <button className="ib-btn" disabled={!text.trim()} onClick={() => send("revise")}>
                  Send revisions
                </button>
              </>
            )}
            {gate.kind === "task-completion" && (
              <>
                <button className="ib-btn ib-btn--primary" disabled={!text.trim()} onClick={() => send("done")}>
                  I did it
                </button>
                <button className="ib-btn" onClick={() => send("cannot")}>
                  I can't
                </button>
              </>
            )}
            {(gate.kind === "escalated-question" || gate.kind === "ping") && (
              <button className="ib-btn ib-btn--primary" disabled={!text.trim()} onClick={() => send("answered")}>
                Send answer
              </button>
            )}
          </div>
        </section>
      )}
    </article>
  );
}

function SessionView({ s, events, now }: { s: SessionRecord; events: TranscriptEvent[]; now: Date }) {
  return (
    <article className="ib-article">
      <div className="ib-eyebrow">
        {s.role} · {s.ticket}
      </div>
      <h2 className="ib-question">{s.summary}</h2>
      <div className="ib-sessionbar">
        <span className={s.status === "running" ? "is-live" : ""}>{s.status}</span>
        <span>{elapsed(s.started_at, s.ended_at ?? now)}</span>
        <span>{s.num_turns} turns</span>
        <span>{money(s.cost_usd)}</span>
        <span>{s.monitored ? "monitored" : "autonomous"}</span>
      </div>
      <section className="ib-context">
        <h3>At a glance</h3>
        <ul className="ib-glance">
          {s.highlights.map((h) => (
            <li key={h}>{h}</li>
          ))}
        </ul>
      </section>
      <section className="ib-context">
        <h3>Conversation</h3>
        <div className="ib-thread">
          {events.map((e, i) => (
            <div key={i} className={`ib-turn ib-turn--${e.kind}`}>
              <div className="ib-turn__who">
                {e.kind === "user"
                  ? "harness → session"
                  : e.kind === "orchestrator"
                    ? "orchestrator"
                    : e.tool
                      ? e.tool
                      : e.kind}
                <span>{clock(e.t)}</span>
              </div>
              <p>{e.text}</p>
            </div>
          ))}
          {s.status === "running" && <div className="ib-turn ib-turn--tail">still working…</div>}
        </div>
      </section>
    </article>
  );
}

function RunView({ run }: { run: ReturnType<typeof useLiveRun> }) {
  return (
    <article className="ib-article">
      <div className="ib-eyebrow">{run.manifest.route} route</div>
      <h2 className="ib-question">{run.manifest.prompt}</h2>
      <div className="ib-sessionbar">
        <span>{run.manifest.target_repo}</span>
        <span>{run.manifest.worktree}</span>
        <span>started {stamp(run.manifest.created_at)}</span>
      </div>
      {run.graphs.map((g) => (
        <section className="ib-context" key={g.graph_id}>
          <h3>
            {g.graph_id}
            {g.spawned_by_node ? ` — subgraph of ${g.spawned_by_node}` : ""}
          </h3>
          <ol className="ib-nodes">
            {g.nodes.map((n) => {
              const s = sessionFor(run, n);
              return (
                <li key={n.id} className={`ib-node ib-node--${n.status}`}>
                  <span className="ib-node__mark" />
                  <span className="ib-node__title">{n.title}</span>
                  <span className="ib-node__meta">
                    {n.type}
                    {isReady(g, n) ? " · ready" : ""}
                    {s ? ` · ${s.num_turns} turns` : ""}
                  </span>
                </li>
              );
            })}
          </ol>
        </section>
      ))}
    </article>
  );
}
