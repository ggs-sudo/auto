// The gate card: every kind wants the same shell — the question, what it is
// blocking, a response box — and a different verb. One component, so
// answering one kind teaches you how to answer the rest. Submitting POSTs to
// the server, which writes the response file beside the gate and nothing
// else; the orchestrator's poll does the delivering.

import { useState } from "react";
import { submitGateResponse } from "./api";
import { GATE_LABEL, elapsed, keysHeldBy, titleOf } from "./derive";
import type { Gate, GateDecision, GateKind, RunDetail } from "./types";

interface Verb {
  decision: GateDecision;
  label: string;
  /** Whether an empty response box leaves this verb unclickable — a revision
   * without words, or a `done` without the facts, carries nothing. */
  needsText: boolean;
  tone: "go" | "warn" | "bad";
}

const SHELL: Record<GateKind, { placeholder: string; verbs: Verb[] }> = {
  "prototype-review": {
    placeholder: "Your review — carried to the session verbatim.",
    verbs: [
      { decision: "approve", label: "approve", needsText: false, tone: "go" },
      { decision: "revise", label: "revise", needsText: true, tone: "warn" },
    ],
  },
  "task-completion": {
    placeholder: "The facts a later ticket will read — ids, where things live, what you did.",
    verbs: [
      { decision: "done", label: "done", needsText: true, tone: "go" },
      { decision: "cannot", label: "can’t be done", needsText: true, tone: "bad" },
    ],
  },
  "escalated-question": {
    placeholder: "Your answer — carried to the session verbatim.",
    verbs: [
      { decision: "answer", label: "answer", needsText: true, tone: "go" },
    ],
  },
  "user-ping": {
    placeholder: "Anything to add (optional).",
    verbs: [
      { decision: "dismiss", label: "acknowledge", needsText: false, tone: "go" },
    ],
  },
};

export function GateCard({
  run,
  gate,
  now,
  onAnswered,
  onSelectNode,
}: {
  run: RunDetail;
  gate: Gate;
  now: number;
  onAnswered: () => void;
  onSelectNode?: (key: string) => void;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const shell = SHELL[gate.kind];
  const held = keysHeldBy(run, gate);

  const submit = async (decision: GateDecision) => {
    setBusy(true);
    setError(null);
    try {
      await submitGateResponse(run.manifest.run_id, gate.gate_id, decision, text);
      onAnswered();
      // Stay busy: the refetched run replaces this card with its answered
      // state, and a live form in the meantime would invite a second answer.
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };

  return (
    <div className={`mct-gate mct-gate--${gate.kind}`}>
      <div className="mct-gate__kind">
        gate #{gate.sequence} · {GATE_LABEL[gate.kind]} ·{" "}
        {gate.response != null || gate.answered_at != null
          ? "answered"
          : `waiting ${elapsed(gate.raised_at, null, now)}`}
      </div>
      {gate.node != null && (
        <button
          className="mct-gate__node"
          onClick={() => onSelectNode?.(gate.node!)}
          disabled={onSelectNode == null}
        >
          blocks {titleOf(gate.node.slice(gate.node.indexOf("/") + 1))}
          {held.length > 0 && (
            <span>
              {" "}
              and {held.length} downstream node{held.length > 1 ? "s" : ""}
            </span>
          )}
        </button>
      )}
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
      {gate.response != null ? (
        <div className="mct-gate__answered">
          <span className={`mct-verb mct-verb--${toneOf(gate, gate.response.decision)}`}>
            {gate.response.decision}
          </span>
          {gate.response.text !== "" && <p>“{gate.response.text}”</p>}
          <div className="mct-gate__note">
            {gate.answered_at != null
              ? "taken up by the orchestrator"
              : "written — the run picks it up on its next look"}
          </div>
        </div>
      ) : gate.answered_at != null ? (
        // Answered and already consumed, the response file gone or unread:
        // nothing left to do here.
        <div className="mct-gate__note">taken up by the orchestrator</div>
      ) : (
        <div className="mct-gate__form">
          <textarea
            className="mct-gate__text"
            placeholder={shell.placeholder}
            value={text}
            disabled={busy}
            onChange={(event) => setText(event.target.value)}
          />
          <div className="mct-gate__verbs">
            {shell.verbs.map((verb) => (
              <button
                key={verb.decision}
                className={`mct-verb mct-verb--${verb.tone}`}
                disabled={busy || (verb.needsText && text.trim() === "")}
                title={
                  verb.needsText && text.trim() === ""
                    ? "needs your words above"
                    : undefined
                }
                onClick={() => submit(verb.decision)}
              >
                {verb.label}
              </button>
            ))}
          </div>
          {error != null && <div className="mct-gate__err">⚠ {error}</div>}
        </div>
      )}
    </div>
  );
}

function toneOf(gate: Gate, decision: GateDecision): Verb["tone"] {
  return (
    SHELL[gate.kind].verbs.find((verb) => verb.decision === decision)?.tone ?? "go"
  );
}
