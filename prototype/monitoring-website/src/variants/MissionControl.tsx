// PROTOTYPE variant A — "Mission control": three panes, the execution graph at the centre.
// Left: runs rail. Centre: the DAG, laid out in dependency layers with drawn edges.
// Right: the selected node's session — at-a-glance summary on top, conversation below.
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useLiveRun } from "../mock/live";
import { runSummaries, runs } from "../mock/data";
import {
  GATE_LABEL,
  PROGRESS,
  blockedKeys,
  clock,
  elapsed,
  gateFor,
  isReady,
  money,
  nodeKey,
  openGates,
  sessionFor,
  stamp,
} from "../mock/format";
import type { Gate, GraphNode } from "../mock/schema";
import "./mission-control.css";

export function MissionControl() {
  const run = useLiveRun();
  const [selected, setSelected] = useState("onboarding-revamp/0005-implement-signup");
  const [selectedRun, setSelectedRun] = useState(run.manifest.run_id);

  const blocked = blockedKeys(run);
  const p = PROGRESS(run);
  const gates = openGates(run);

  const [graphId, nodeId] = selected.split("/");
  const graph = run.graphs.find((g) => g.graph_id === graphId)!;
  const node = graph.nodes.find((n) => n.id === nodeId)!;
  const session = sessionFor(run, node);
  const nodeGate = gateFor(run, graphId, nodeId);

  return (
    <div className="mc">
      <aside className="mc-rail">
        <div className="mc-rail__brand">
          auto<span>/monitor</span>
        </div>
        {runs.map((r) => {
          const s = runSummaries[r.run_id];
          const on = r.run_id === selectedRun;
          return (
            <button
              key={r.run_id}
              className={`mc-runcard ${on ? "is-on" : ""}`}
              onClick={() => setSelectedRun(r.run_id)}
            >
              <div className="mc-runcard__top">
                <span className={`mc-dot mc-dot--${r.status}`} />
                <span className="mc-runcard__id">{r.run_id.slice(9)}</span>
                {s.openGates > 0 && <span className="mc-badge">{s.openGates}</span>}
              </div>
              <div className="mc-runcard__prompt">{r.prompt}</div>
              <div className="mc-bar">
                <i style={{ width: `${(s.done / s.nodes) * 100}%` }} />
              </div>
              <div className="mc-runcard__meta">
                <span>{r.route}</span>
                <span>
                  {s.done}/{s.nodes}
                </span>
                <span>{money(s.cost)}</span>
              </div>
            </button>
          );
        })}
      </aside>

      <main className="mc-main">
        <header className="mc-head">
          <div>
            <h1>{run.manifest.run_id}</h1>
            <p className="mc-head__prompt">{run.manifest.prompt}</p>
          </div>
          <dl className="mc-stats">
            <div>
              <dt>status</dt>
              <dd className={`mc-status mc-status--${run.manifest.status}`}>{run.manifest.status}</dd>
            </div>
            <div>
              <dt>phase</dt>
              <dd>{run.manifest.phase}</dd>
            </div>
            <div>
              <dt>elapsed</dt>
              <dd>{elapsed(run.manifest.created_at, run.now)}</dd>
            </div>
            <div>
              <dt>nodes</dt>
              <dd>
                {p.done}/{p.total} <span className="mc-sub">· {p.running} live</span>
              </dd>
            </div>
            <div>
              <dt>spend</dt>
              <dd>{money(p.cost)}</dd>
            </div>
          </dl>
        </header>

        {gates.length > 0 && (
          <div className="mc-gatestrip">
            <span className="mc-gatestrip__label">needs you</span>
            {gates.map((g) => (
              <button
                key={g.seq}
                className={`mc-gatechip mc-gatechip--${g.kind}`}
                onClick={() => g.on_node && setSelected(nodeKey(g.graph_id!, g.on_node))}
              >
                #{g.seq} {GATE_LABEL[g.kind]}
                <span>· {elapsed(g.opened_at, run.now)} waiting</span>
              </button>
            ))}
          </div>
        )}

        <div className="mc-graphs">
          {run.graphs.map((g) => (
            <GraphBoard
              key={g.graph_id}
              graph={g}
              selected={selected}
              blocked={blocked}
              onSelect={setSelected}
              gateOn={(nid) => gateFor(run, g.graph_id, nid)}
              ready={(n) => isReady(g, n)}
            />
          ))}
        </div>
      </main>

      <aside className="mc-inspect">
        {nodeGate && <GateAction gate={nodeGate} onRespond={run.respond} />}
        <div className="mc-inspect__head">
          <span className={`mc-type mc-type--${node.type}`}>{node.type}</span>
          <h2>{node.title}</h2>
          <div className="mc-inspect__sub">
            {graph.graph_id}/{node.id}
          </div>
        </div>
        {session ? (
          <>
            <div className="mc-glance">
              <div className="mc-glance__row">
                <span className={`mc-status mc-status--${session.status}`}>{session.status}</span>
                <span>{session.num_turns} turns</span>
                <span>{money(session.cost_usd)}</span>
                <span>{elapsed(session.started_at, session.ended_at ?? run.now)}</span>
                {session.monitored && <span className="mc-tag">monitored</span>}
              </div>
              <p className="mc-glance__summary">{session.summary}</p>
              <ul className="mc-glance__list">
                {session.highlights.map((h) => (
                  <li key={h}>{h}</li>
                ))}
              </ul>
            </div>
            <Conversation events={run.transcripts[session.session_id] ?? []} />
          </>
        ) : (
          <div className="mc-empty">
            <p>No session yet.</p>
            <p className="mc-sub">
              Waiting on {node.deps.length ? node.deps.join(", ") : "dispatch"}.
            </p>
          </div>
        )}
      </aside>
    </div>
  );
}

/* ---------- graph board: dependency layers + drawn edges ---------- */

function layersOf(nodes: GraphNode[]): GraphNode[][] {
  const depth = new Map<string, number>();
  const at = (n: GraphNode): number => {
    if (depth.has(n.id)) return depth.get(n.id)!;
    depth.set(n.id, 0);
    const d = n.deps.length
      ? 1 + Math.max(...n.deps.map((id) => {
          const dep = nodes.find((x) => x.id === id);
          return dep ? at(dep) : -1;
        }))
      : 0;
    depth.set(n.id, d);
    return d;
  };
  nodes.forEach(at);
  const max = Math.max(...nodes.map((n) => depth.get(n.id)!));
  return Array.from({ length: max + 1 }, (_, i) => nodes.filter((n) => depth.get(n.id) === i));
}

function GraphBoard({
  graph,
  selected,
  blocked,
  onSelect,
  gateOn,
  ready,
}: {
  graph: { graph_id: string; spawned_by_node: string | null; nodes: GraphNode[] };
  selected: string;
  blocked: Set<string>;
  onSelect: (key: string) => void;
  gateOn: (nodeId: string) => Gate | undefined;
  ready: (n: GraphNode) => boolean;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const cells = useRef<Record<string, HTMLElement | null>>({});
  const [edges, setEdges] = useState<{ d: string; done: boolean }[]>([]);

  useLayoutEffect(() => {
    const measure = () => {
      const box = wrap.current?.getBoundingClientRect();
      if (!box) return;
      const next: { d: string; done: boolean }[] = [];
      for (const n of graph.nodes) {
        for (const dep of n.deps) {
          const a = cells.current[dep]?.getBoundingClientRect();
          const b = cells.current[n.id]?.getBoundingClientRect();
          if (!a || !b) continue;
          const x1 = a.right - box.left;
          const y1 = a.top + a.height / 2 - box.top;
          const x2 = b.left - box.left;
          const y2 = b.top + b.height / 2 - box.top;
          const mid = (x1 + x2) / 2;
          next.push({
            d: `M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`,
            done: graph.nodes.find((x) => x.id === dep)?.status === "done",
          });
        }
      }
      setEdges(next);
    };
    measure();
    const ro = new ResizeObserver(measure);
    if (wrap.current) ro.observe(wrap.current);
    window.addEventListener("resize", measure);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [graph]);

  return (
    <section className="mc-board">
      <h3 className="mc-board__title">
        .scratch/{graph.graph_id}/
        {graph.spawned_by_node && <em>subgraph of {graph.spawned_by_node}</em>}
      </h3>
      <div className="mc-board__canvas" ref={wrap}>
        <svg className="mc-edges">
          {edges.map((e, i) => (
            <path key={i} d={e.d} className={e.done ? "is-done" : ""} />
          ))}
        </svg>
        {layersOf(graph.nodes).map((layer, i) => (
          <div className="mc-layer" key={i}>
            {layer.map((n) => {
              const key = nodeKey(graph.graph_id, n.id);
              const gate = gateOn(n.id);
              return (
                <button
                  key={n.id}
                  ref={(el) => {
                    cells.current[n.id] = el;
                  }}
                  className={`mc-node mc-node--${n.status} ${selected === key ? "is-on" : ""} ${
                    blocked.has(key) ? "is-blocked" : ""
                  }`}
                  onClick={() => onSelect(key)}
                >
                  <span className={`mc-type mc-type--${n.type}`}>{n.type}</span>
                  <span className="mc-node__title">{n.title}</span>
                  <span className="mc-node__foot">
                    <span>{n.id}</span>
                    {n.resolution_mode === "user" && <span className="mc-tag">user</span>}
                    {n.spawned_graph && n.spawned_graph !== graph.graph_id && (
                      <span className="mc-tag">↳ {n.spawned_graph}</span>
                    )}
                    {ready(n) && <span className="mc-tag mc-tag--ready">ready</span>}
                    {blocked.has(key) && <span className="mc-tag mc-tag--blocked">gated</span>}
                  </span>
                  {gate && <span className="mc-node__gate">needs you · #{gate.seq}</span>}
                </button>
              );
            })}
          </div>
        ))}
      </div>
    </section>
  );
}

/* ---------- gate action ---------- */

function GateAction({
  gate,
  onRespond,
}: {
  gate: Gate;
  onRespond: (seq: number, r: { responded_at: string; decision: any; text: string }) => void;
}) {
  const [text, setText] = useState("");
  const send = (decision: string) =>
    onRespond(gate.seq, { responded_at: new Date().toISOString(), decision, text });

  return (
    <div className={`mc-gate mc-gate--${gate.kind}`}>
      <div className="mc-gate__kind">
        gate #{gate.seq} · {GATE_LABEL[gate.kind]}
      </div>
      <p className="mc-gate__q">{gate.question}</p>
      {gate.artifact && (
        <a className="mc-gate__artifact" href={gate.artifact} target="_blank" rel="noreferrer">
          open the prototype ↗ <span>{gate.artifact}</span>
        </a>
      )}
      {gate.blocking.length > 0 && (
        <div className="mc-gate__blocking">holding: {gate.blocking.join(", ")}</div>
      )}
      <textarea
        value={text}
        placeholder={
          gate.kind === "prototype-review"
            ? "What to keep, what to change… (required to request revisions)"
            : "Your answer…"
        }
        onChange={(e) => setText(e.target.value)}
      />
      <div className="mc-gate__actions">
        {gate.kind === "prototype-review" && (
          <>
            <button className="mc-btn mc-btn--go" onClick={() => send("approve")}>
              Approve
            </button>
            <button className="mc-btn" disabled={!text.trim()} onClick={() => send("revise")}>
              Request revision
            </button>
          </>
        )}
        {gate.kind === "task-completion" && (
          <>
            <button className="mc-btn mc-btn--go" disabled={!text.trim()} onClick={() => send("done")}>
              Done
            </button>
            <button className="mc-btn" onClick={() => send("cannot")}>
              Can't do it
            </button>
          </>
        )}
        {(gate.kind === "escalated-question" || gate.kind === "ping") && (
          <button className="mc-btn mc-btn--go" disabled={!text.trim()} onClick={() => send("answered")}>
            Send answer
          </button>
        )}
      </div>
    </div>
  );
}

/* ---------- conversation ---------- */

function Conversation({ events }: { events: { t: string; kind: string; tool?: string; text: string }[] }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    box.current?.scrollTo({ top: box.current.scrollHeight, behavior: "smooth" });
  }, [events.length]);

  return (
    <div className="mc-convo" ref={box}>
      {events.map((e, i) => (
        <div key={i} className={`mc-ev mc-ev--${e.kind}`}>
          <div className="mc-ev__gutter">
            <span className="mc-ev__time">{clock(e.t)}</span>
            <span className="mc-ev__kind">{e.tool ?? e.kind}</span>
          </div>
          <div className="mc-ev__body">{e.text}</div>
        </div>
      ))}
      <div className="mc-ev mc-ev--live">
        <div className="mc-ev__gutter">
          <span className="mc-ev__time">live</span>
        </div>
        <div className="mc-ev__body">
          <span className="mc-caret" /> tailing transcript…
        </div>
      </div>
    </div>
  );
}
