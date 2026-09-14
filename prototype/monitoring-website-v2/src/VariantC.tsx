// PROTOTYPE — throwaway. Variant C, "tabs": the graph owns the top of a
// fixed viewport, and the chat below it wears a SESSION TAB STRIP — one tab
// per session (plus root), so switching what you read never means touching
// the graph. The runs rail is gone from the frame entirely: a ☰ button in
// the top bar opens it as an overlay, dismissed on selection.

import { useState } from "react";
import { GateCard } from "../GateCard";
import { RunBoards } from "../GraphBoard";
import { Conversation } from "../Inspector";
import { RunsRail } from "../RunsRail";
import { ROOT_KEY, gateById, nodeKey, openGates, titleOf } from "../derive";
import type { RunDetail } from "../types";
import {
  RunStats,
  SessionFacts,
  inspectNode,
  withoutInitialPrompt,
  type VariantProps,
} from "./shared";
import "./variant-c.css";

interface Tab {
  key: string;
  label: string;
  status: string;
  gated: boolean;
}

function sessionTabs(run: RunDetail, selected: string): Tab[] {
  const root = run.manifest.root_node;
  const tabs: Tab[] = [
    { key: ROOT_KEY, label: "root", status: root.status, gated: root.gate != null },
  ];
  for (const graph of run.graphs) {
    for (const node of graph.nodes) {
      const key = nodeKey(graph.graph_id, node.node_id);
      if (node.session_id != null || key === selected) {
        tabs.push({
          key,
          label: titleOf(node.node_id),
          status: node.status,
          gated: node.gate != null,
        });
      }
    }
  }
  return tabs;
}

export function VariantC(p: VariantProps) {
  const [railOpen, setRailOpen] = useState(false);
  return (
    <div className="mct vc">
      <header className="vc-top">
        <button className="vc-burger" onClick={() => setRailOpen(true)}>
          ☰ runs
        </button>
        <h1>{p.detail?.manifest.run_id ?? p.runId ?? "auto ▸ monitor"}</h1>
        {p.everLive && !p.live && <span className="vc-offline">⚠ reconnecting…</span>}
        {p.detail != null && <RunStats run={p.detail} now={p.now} />}
      </header>
      {p.detail != null ? (
        <>
          <div className="vc-graph">
            <Gates {...p} />
            <RunBoards run={p.detail} selected={p.selectedNode} onSelect={p.onSelectNode} />
          </div>
          <ChatPane {...p} />
        </>
      ) : (
        <div className="mct-empty">
          <p>{p.detailError ?? p.runsError ?? "Loading…"}</p>
        </div>
      )}
      {railOpen && (
        <div className="vc-scrim" onClick={() => setRailOpen(false)}>
          <div className="vc-overlay" onClick={(event) => event.stopPropagation()}>
            <RunsRail
              runs={p.runs ?? []}
              selected={p.runId}
              now={p.now}
              live={p.live}
              stale={p.runsError != null && p.runs != null}
              onSelect={(id) => {
                p.onSelectRun(id);
                setRailOpen(false);
              }}
            />
          </div>
        </div>
      )}
    </div>
  );
}

function Gates(p: VariantProps) {
  const run = p.detail!;
  const gates = openGates(run);
  if (gates.length === 0) return null;
  return (
    <section className="mct-gatestrip">
      <span className="mct-gatestrip__label">needs you</span>
      <div className="mct-gatestrip__cards">
        {gates.map((gate) => (
          <GateCard
            key={gate.gate_id}
            run={run}
            gate={gate}
            now={p.now}
            onAnswered={p.onAnswered}
            onSelectNode={p.onSelectNode}
          />
        ))}
      </div>
    </section>
  );
}

function ChatPane(p: VariantProps) {
  const run = p.detail!;
  const tabs = sessionTabs(run, p.selectedNode);
  const node = inspectNode(run, p.selectedNode);
  const session = p.session;
  const gate = node != null ? gateById(run, node.gate) : undefined;
  return (
    <div className="vc-chat">
      <div className="vc-tabs" role="tablist">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            role="tab"
            aria-selected={tab.key === p.selectedNode}
            className={`vc-tab ${tab.key === p.selectedNode ? "is-on" : ""}`}
            onClick={() => p.onSelectNode(tab.key)}
          >
            <span className={`mct-dot mct-dot--${tab.status === "in-progress" ? "running" : tab.status}`} />
            {tab.label}
            {tab.gated && <span className="vc-tab__gate">◆</span>}
          </button>
        ))}
        {session != null && (
          <span className="vc-tabs__facts">
            <SessionFacts session={session} now={p.now} />
          </span>
        )}
      </div>
      <div className="vc-pane">
        {gate != null && (
          <GateCard run={run} gate={gate} now={p.now} onAnswered={p.onAnswered} />
        )}
        {p.transcriptError != null && (
          <div className="mct-transcript-err">⚠ transcript can’t be loaded — {p.transcriptError}</div>
        )}
        {session != null ? (
          <Conversation
            key={session.session_id}
            events={withoutInitialPrompt(p.events)}
            interventions={run.interventions.filter((r) => r.node === p.selectedNode)}
            running={session.status === "running"}
          />
        ) : (
          <div className="mct-empty">
            <p>No session yet.</p>
          </div>
        )}
      </div>
    </div>
  );
}
