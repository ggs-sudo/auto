// PROTOTYPE — in-memory fixture standing in for `~/.auto/runs/`. No persistence, by design.
import type {
  Gate,
  Graph,
  RunDetail,
  RunManifest,
  SessionRecord,
  TranscriptEvent,
} from "./schema";

const T = (hhmm: string, day = 28) => `2026-08-${day}T${hhmm}:00Z`;

export const activeRunId = "20260828-0912-ai-new-onboarding";

const manifest: RunManifest = {
  schema_version: 1,
  run_id: activeRunId,
  route: "wayfinder",
  prompt:
    "Rebuild onboarding for ai-new: a workspace owner should be able to sign up, verify their email, " +
    "invite teammates onto seats, and land on a checklist that shows what's left to do. " +
    "The current signup dumps people straight into an empty dashboard with no idea what to do next.",
  target_repo: "~/workspace/ai-new",
  worktree: "~/workspace/ai-new-wt/onboarding",
  branch: "main",
  created_at: T("09:12"),
  ended_at: null,
  status: "running",
  phase: "executing",
};

const graphs: Graph[] = [
  {
    graph_id: "onboarding-revamp",
    spawned_by_node: null,
    nodes: [
      {
        id: "0001-map-onboarding",
        title: "Map: onboarding revamp",
        type: "wayfinder",
        deps: [],
        status: "done",
        session_id: "s-01-wayfinder",
        spawned_graph: "onboarding-revamp",
      },
      {
        id: "0002-research-auth-limits",
        title: "Research: WorkOS invite + seat limits",
        type: "research",
        deps: ["0001-map-onboarding"],
        status: "done",
        session_id: "s-02-research",
      },
      {
        id: "0003-prototype-wizard",
        title: "Prototype: onboarding wizard shape",
        type: "prototype",
        deps: ["0001-map-onboarding"],
        status: "done",
        session_id: "s-03-prototype",
      },
      {
        id: "0004-grill-invite-seats",
        title: "Invite + seat model",
        type: "grill-with-docs",
        deps: ["0002-research-auth-limits"],
        status: "done",
        session_id: "s-04-grill",
        spawned_graph: "invite-seats",
      },
      {
        id: "0005-implement-signup",
        title: "Signup form + workspace creation",
        type: "implement",
        deps: ["0001-map-onboarding"],
        status: "in-progress",
        session_id: "s-05-signup",
      },
      {
        id: "0006-implement-verify",
        title: "Email verification flow",
        type: "implement",
        deps: ["0005-implement-signup"],
        status: "in-progress",
        session_id: "s-06-verify",
      },
      {
        id: "0007-task-postmark-dns",
        title: "Configure Postmark sending domain",
        type: "implement",
        deps: ["0002-research-auth-limits"],
        status: "in-progress",
        session_id: "s-07-postmark",
        resolution_mode: "user",
      },
      {
        id: "0008-implement-checklist",
        title: "Onboarding checklist surface",
        type: "implement",
        deps: ["0003-prototype-wizard", "0005-implement-signup"],
        status: "pending",
        session_id: null,
      },
    ],
  },
  {
    graph_id: "invite-seats",
    spawned_by_node: "0004-grill-invite-seats",
    nodes: [
      {
        id: "0001-seat-migration",
        title: "Seat model migration",
        type: "implement",
        deps: [],
        status: "done",
        session_id: "s-08-seats",
      },
      {
        id: "0002-invite-api",
        title: "Invite API + redemption",
        type: "implement",
        deps: ["0001-seat-migration"],
        status: "in-progress",
        session_id: "s-09-invite-api",
      },
      {
        id: "0003-invite-ui",
        title: "Invite teammates UI",
        type: "implement",
        deps: ["0002-invite-api"],
        status: "pending",
        session_id: null,
      },
    ],
  },
];

const sessions: SessionRecord[] = [
  {
    session_id: "s-01-wayfinder",
    role: "wayfinder",
    ticket: "0001-map-onboarding",
    graph_id: "onboarding-revamp",
    status: "succeeded",
    started_at: T("09:12"),
    ended_at: T("09:41"),
    summary:
      "Mapped the revamp into eight tickets. Fog cleared around signup and verification; " +
      "seats stayed foggy and became a grilling ticket rather than a guess.",
    highlights: [
      "Destination: owner signs up → verifies → invites → sees a checklist",
      "Seat model deferred to its own grilling (#0004) — pricing implications unknown",
      "Postmark DNS flagged as user-only work at map time",
    ],
    transcript: "transcripts/s-01-wayfinder.jsonl",
    native_transcript: "~/.claude/projects/-Users-ggs-workspace-ai-new-wt-onboarding/s-01.jsonl",
    cost_usd: 1.84,
    num_turns: 34,
    monitored: true,
  },
  {
    session_id: "s-02-research",
    role: "research",
    ticket: "0002-research-auth-limits",
    graph_id: "onboarding-revamp",
    status: "succeeded",
    started_at: T("09:44"),
    ended_at: T("10:02"),
    summary:
      "WorkOS org invites cap at 100 pending per org and expire after 7 days; seats are billed " +
      "on acceptance, not on invite. Report written to .scratch/onboarding-revamp/research/workos-invites.md.",
    highlights: [
      "100 pending invites per org, 7-day expiry (source: WorkOS docs, verified against API)",
      "Seat billing fires on acceptance — invites are free",
      "No bulk-invite endpoint; loop client-side with 429 backoff",
    ],
    transcript: "transcripts/s-02-research.jsonl",
    native_transcript: "~/.claude/projects/-Users-ggs-workspace-ai-new-wt-onboarding/s-02.jsonl",
    cost_usd: 0.91,
    num_turns: 18,
    monitored: false,
  },
  {
    session_id: "s-03-prototype",
    role: "prototype",
    ticket: "0003-prototype-wizard",
    graph_id: "onboarding-revamp",
    status: "succeeded",
    started_at: T("09:44"),
    ended_at: T("10:26"),
    summary:
      "Three wizard shapes on /onboarding?variant= — stepper, single-scroll, and checklist-first. " +
      "Waiting on review before the checklist ticket can start.",
    highlights: [
      "A: four-step stepper — most familiar, most clicks",
      "B: single scroll with sticky progress — fastest, weak on 'what's left'",
      "C: checklist-first, tasks open inline — closest to the map's destination",
    ],
    transcript: "transcripts/s-03-prototype.jsonl",
    native_transcript: "~/.claude/projects/-Users-ggs-workspace-ai-new-wt-onboarding/s-03.jsonl",
    cost_usd: 2.37,
    num_turns: 41,
    monitored: false,
  },
  {
    session_id: "s-04-grill",
    role: "grill-with-docs",
    ticket: "0004-grill-invite-seats",
    graph_id: "onboarding-revamp",
    status: "succeeded",
    started_at: T("10:05"),
    ended_at: T("10:52"),
    summary:
      "Settled the seat model (seats are workspace-scoped, billed on acceptance, downgrade blocked " +
      "while over quota), then continued in-conversation through /to-spec and /to-tickets. " +
      "Emitted the invite-seats subgraph: three tickets.",
    highlights: [
      "Seats are workspace-scoped, not org-scoped",
      "Over-quota downgrade blocked rather than auto-revoking seats",
      "Spawned subgraph invite-seats (3 tickets)",
    ],
    transcript: "transcripts/s-04-grill.jsonl",
    native_transcript: "~/.claude/projects/-Users-ggs-workspace-ai-new-wt-onboarding/s-04.jsonl",
    cost_usd: 3.12,
    num_turns: 58,
    monitored: true,
  },
  {
    session_id: "s-05-signup",
    role: "implement",
    ticket: "0005-implement-signup",
    graph_id: "onboarding-revamp",
    status: "running",
    started_at: T("10:31"),
    ended_at: null,
    summary:
      "Signup form + workspace creation. Server action and zod schema landed; wiring the workspace " +
      "slug uniqueness check now.",
    highlights: [
      "createWorkspace server action written, 4 tests green",
      "Slug collision handling in progress",
    ],
    transcript: "transcripts/s-05-signup.jsonl",
    native_transcript: "~/.claude/projects/-Users-ggs-workspace-ai-new-wt-onboarding/s-05.jsonl",
    cost_usd: 1.22,
    num_turns: 21,
    monitored: false,
  },
  {
    session_id: "s-06-verify",
    role: "implement",
    ticket: "0006-implement-verify",
    graph_id: "onboarding-revamp",
    status: "running",
    started_at: T("10:58"),
    ended_at: null,
    summary:
      "Email verification flow. Token table + send path done; blocked in practice on the Postmark " +
      "domain, so it is stubbing the transport behind a flag.",
    highlights: [
      "verification_tokens table + 24h expiry",
      "Transport stubbed behind MAIL_TRANSPORT=console until DNS lands",
    ],
    transcript: "transcripts/s-06-verify.jsonl",
    native_transcript: "~/.claude/projects/-Users-ggs-workspace-ai-new-wt-onboarding/s-06.jsonl",
    cost_usd: 0.74,
    num_turns: 13,
    monitored: false,
  },
  {
    session_id: "s-07-postmark",
    role: "implement",
    ticket: "0007-task-postmark-dns",
    graph_id: "onboarding-revamp",
    status: "running",
    started_at: T("10:12"),
    ended_at: null,
    summary:
      "User-resolved task. Session is idle inside the task-completion gate, waiting for the DNS " +
      "records to be reported back so it can write them into the ticket.",
    highlights: ["Idle at gate #3 since 10:14", "Needs: DKIM + Return-Path records as published"],
    transcript: "transcripts/s-07-postmark.jsonl",
    native_transcript: "~/.claude/projects/-Users-ggs-workspace-ai-new-wt-onboarding/s-07.jsonl",
    cost_usd: 0.18,
    num_turns: 4,
    monitored: false,
  },
  {
    session_id: "s-08-seats",
    role: "implement",
    ticket: "0001-seat-migration",
    graph_id: "invite-seats",
    status: "succeeded",
    started_at: T("10:55"),
    ended_at: T("11:19"),
    summary: "Seat model migration + backfill for existing workspaces. 11 tests green.",
    highlights: ["seats table, workspace-scoped", "Backfill assigns every existing member a seat"],
    transcript: "transcripts/s-08-seats.jsonl",
    native_transcript: "~/.claude/projects/-Users-ggs-workspace-ai-new-wt-onboarding/s-08.jsonl",
    cost_usd: 1.41,
    num_turns: 26,
    monitored: false,
  },
  {
    session_id: "s-09-invite-api",
    role: "implement",
    ticket: "0002-invite-api",
    graph_id: "invite-seats",
    status: "running",
    started_at: T("11:22"),
    ended_at: null,
    summary:
      "Invite API + redemption. Hit a question the answer policy could not settle — escalated to the " +
      "user at gate #2 and is idle waiting.",
    highlights: [
      "POST /invites + redemption route drafted",
      "Escalated: should an expired invite auto-reissue or hard-fail?",
    ],
    transcript: "transcripts/s-09-invite-api.jsonl",
    native_transcript: "~/.claude/projects/-Users-ggs-workspace-ai-new-wt-onboarding/s-09.jsonl",
    cost_usd: 0.63,
    num_turns: 11,
    monitored: false,
  },
];

const gates: Gate[] = [
  {
    seq: 1,
    kind: "prototype-review",
    opened_at: T("10:26"),
    opened_by_session: "s-03-prototype",
    on_node: "0003-prototype-wizard",
    graph_id: "onboarding-revamp",
    question:
      "Three onboarding wizard shapes are up. Pick one (or say what to graft from which) before the " +
      "checklist ticket starts.",
    artifact: "http://localhost:5173/onboarding?variant=C",
    blocking: ["0008-implement-checklist"],
  },
  {
    seq: 2,
    kind: "escalated-question",
    opened_at: T("11:31"),
    opened_by_session: "s-09-invite-api",
    on_node: "0002-invite-api",
    graph_id: "invite-seats",
    question:
      "An invite that has expired (7 days, per the research report): auto-reissue on click, or hard-fail " +
      "and make the owner re-invite? The prompt and repo docs are silent, and it changes the billing story.",
    blocking: ["0003-invite-ui"],
  },
  {
    seq: 3,
    kind: "task-completion",
    opened_at: T("10:14"),
    opened_by_session: "s-07-postmark",
    on_node: "0007-task-postmark-dns",
    graph_id: "onboarding-revamp",
    question:
      "Add the Postmark sending domain for ai-new and publish the DKIM + Return-Path records. " +
      "Report the records back so the verification ticket can use the real transport.",
    blocking: [],
  },
  {
    seq: 4,
    kind: "ping",
    opened_at: T("09:41"),
    opened_by_session: "s-01-wayfinder",
    on_node: null,
    graph_id: null,
    question: "Map closed with eight tickets. Nothing blocking — glance at it when you get a minute.",
    blocking: [],
    response: {
      responded_at: T("09:52"),
      decision: "answered",
      text: "Looks right. Keep going.",
    },
  },
];

const ev = (t: string, kind: TranscriptEvent["kind"], text: string, tool?: string): TranscriptEvent => ({
  t: T(t),
  kind,
  text,
  ...(tool ? { tool } : {}),
});

const transcripts: Record<string, TranscriptEvent[]> = {
  "s-01-wayfinder": [
    ev("09:12", "user", "/wayfinder\n\nRebuild onboarding for ai-new: a workspace owner should be able to sign up, verify their email, invite teammates onto seats, and land on a checklist…"),
    ev("09:13", "assistant", "Before I map this, I need the destination sharp. What does 'done' look like for a brand-new owner — the moment they'd say onboarding worked?"),
    ev("09:14", "orchestrator", "Answering per the answer policy (recommendation accepted; nothing in the prompt or repo docs contradicts it): they've verified, invited at least one teammate, and can see what's left."),
    ev("09:15", "assistant", "Good. Second: does 'seats' mean billing seats, or just membership? That changes whether this map touches pricing."),
    ev("09:16", "orchestrator", "Escalation check: pricing is a vendor/billing call → but the prompt says 'invite teammates onto seats', so membership-with-quota is the reading. Not escalating. Answered."),
    ev("09:22", "tool_use", "Wrote .scratch/onboarding-revamp/map.md", "Write"),
    ev("09:30", "assistant", "The seat question keeps coming back with different answers depending on which doc I read. That's a fog signal — I'd rather not guess it inside the map."),
    ev("09:34", "tool_use", "Wrote .scratch/onboarding-revamp/issues/0004-grill-invite-seats.md", "Write"),
    ev("09:38", "assistant", "Map closed. Eight tickets: one research, one prototype, one grilling, four implementations, one user task."),
    ev("09:41", "result", "stop_reason: end_turn · 34 turns · $1.84"),
    ev("09:41", "orchestrator", "Verified tracker files (map.md, issues/0001–0008). complete_node(0001-map-onboarding) · emit_graph(onboarding-revamp)"),
  ],
  "s-02-research": [
    ev("09:44", "user", "/research WorkOS organization invites: limits, expiry, and when a seat is billed."),
    ev("09:46", "tool_use", "https://workos.com/docs/user-management/invitations", "WebFetch"),
    ev("09:47", "tool_result", "Invitations expire after 7 days by default; expires_in_days configurable 1–30.", "WebFetch"),
    ev("09:52", "tool_use", "curl -s https://api.workos.com/user_management/invitations?limit=1", "Bash"),
    ev("09:53", "tool_result", "200 OK — pagination confirms `limit` max 100.", "Bash"),
    ev("09:58", "tool_use", "Wrote .scratch/onboarding-revamp/research/workos-invites.md", "Write"),
    ev("10:02", "result", "stop_reason: end_turn · 18 turns · $0.91"),
    ev("10:02", "orchestrator", "Report present and sourced. complete_node(0002-research-auth-limits)"),
  ],
  "s-03-prototype": [
    ev("09:44", "user", "/prototype 0003-prototype-wizard — what should the onboarding wizard look like?"),
    ev("09:47", "assistant", "UI branch. Three variants on /onboarding?variant=, switchable from a floating bar."),
    ev("10:04", "tool_use", "Wrote app/onboarding/prototype-variants/StepperVariant.tsx", "Write"),
    ev("10:11", "tool_use", "Wrote app/onboarding/prototype-variants/ScrollVariant.tsx", "Write"),
    ev("10:19", "tool_use", "Wrote app/onboarding/prototype-variants/ChecklistVariant.tsx", "Write"),
    ev("10:24", "tool_use", "pnpm dev — ready on http://localhost:5173", "Bash"),
    ev("10:26", "assistant", "Three shapes up at /onboarding?variant=A|B|C. C is closest to the map's destination but has the weakest first screen."),
    ev("10:26", "result", "stop_reason: end_turn · 41 turns · $2.37"),
    ev("10:26", "orchestrator", "prototype_ready(0003-prototype-wizard, artifact=http://localhost:5173/onboarding?variant=C) — gate #1 opened, user pinged."),
  ],
  "s-04-grill": [
    ev("10:05", "user", "/grill-with-docs 0004-grill-invite-seats"),
    ev("10:06", "assistant", "Is a seat scoped to the workspace or to the org? If a person is in two workspaces of the same org, is that one seat or two?"),
    ev("10:07", "orchestrator", "Answering: workspace-scoped — the prompt frames invites as 'onto seats' inside one workspace. Two workspaces, two seats."),
    ev("10:12", "assistant", "Then: an owner downgrades to a plan with fewer seats than they've filled. Auto-revoke the newest members, or block the downgrade?"),
    ev("10:13", "orchestrator", "Answering: block. Silent revocation destroys access a user paid for; the repo's docs/principles.md says destructive defaults are never acceptable."),
    ev("10:31", "assistant", "That's the model settled. Continuing in-conversation to the spec."),
    ev("10:33", "tool_use", "Wrote .scratch/invite-seats/spec.md", "Write"),
    ev("10:44", "tool_use", "Wrote .scratch/invite-seats/issues/0001-seat-migration.md", "Write"),
    ev("10:45", "tool_use", "Wrote .scratch/invite-seats/issues/0002-invite-api.md", "Write"),
    ev("10:46", "tool_use", "Wrote .scratch/invite-seats/issues/0003-invite-ui.md", "Write"),
    ev("10:52", "result", "stop_reason: end_turn · 58 turns · $3.12"),
    ev("10:52", "orchestrator", "Spec + 3 tickets verified. complete_node(0004-grill-invite-seats) · emit_graph(invite-seats, spawned_by=0004)"),
  ],
  "s-05-signup": [
    ev("10:31", "user", "/implement 0005-implement-signup"),
    ev("10:33", "tool_use", "Read app/(auth)/signup/page.tsx", "Read"),
    ev("10:38", "tool_use", "Wrote app/(auth)/signup/actions.ts", "Write"),
    ev("10:44", "tool_use", "pnpm vitest run app/(auth)", "Bash"),
    ev("10:45", "tool_result", "4 passed (4)", "Bash"),
    ev("10:52", "assistant", "Server action + zod schema in. Now the workspace slug: two owners can pick the same company name."),
    ev("11:03", "tool_use", "Wrote lib/workspace/slug.ts", "Write"),
    ev("11:14", "tool_use", "pnpm vitest run lib/workspace", "Bash"),
    ev("11:15", "tool_result", "1 failed — slugify('Acme, Inc.') → 'acme-inc-' (trailing dash)", "Bash"),
    ev("11:18", "assistant", "Trailing separator. Trimming before the uniqueness probe."),
  ],
  "s-06-verify": [
    ev("10:58", "user", "/implement 0006-implement-verify"),
    ev("11:02", "tool_use", "Wrote drizzle/0007_verification_tokens.sql", "Write"),
    ev("11:09", "tool_use", "Wrote lib/mail/send-verification.ts", "Write"),
    ev("11:16", "assistant", "The real transport needs the Postmark domain, which isn't up yet. Putting it behind MAIL_TRANSPORT so the flow is testable now and the switch is one env var later."),
    ev("11:24", "tool_use", "pnpm vitest run lib/mail", "Bash"),
    ev("11:25", "tool_result", "6 passed (6)", "Bash"),
  ],
  "s-07-postmark": [
    ev("10:12", "user", "/implement 0007-task-postmark-dns"),
    ev("10:13", "assistant", "This one is DNS work in a vendor dashboard — I can't publish records for ai-new.dev. I'll write the ticket up with exactly what's needed and wait."),
    ev("10:14", "orchestrator", "Node classified `user` at emit_graph time. Gate #3 (task-completion) opened; session held alive and idle."),
  ],
  "s-08-seats": [
    ev("10:55", "user", "/implement 0001-seat-migration"),
    ev("11:01", "tool_use", "Wrote drizzle/0006_seats.sql", "Write"),
    ev("11:08", "tool_use", "Wrote lib/seats/backfill.ts", "Write"),
    ev("11:15", "tool_use", "pnpm vitest run lib/seats", "Bash"),
    ev("11:16", "tool_result", "11 passed (11)", "Bash"),
    ev("11:19", "result", "stop_reason: end_turn · 26 turns · $1.41"),
    ev("11:19", "orchestrator", "Ticket status updated to Done. complete_node(invite-seats/0001-seat-migration)"),
  ],
  "s-09-invite-api": [
    ev("11:22", "user", "/implement 0002-invite-api"),
    ev("11:26", "tool_use", "Wrote app/api/invites/route.ts", "Write"),
    ev("11:29", "assistant", "Redemption raises a question I can't answer from the spec: an invite past its 7-day expiry — auto-reissue on click, or hard-fail and make the owner re-invite? Both are defensible and it changes when a seat gets billed."),
    ev("11:31", "orchestrator", "Answer policy: prompt and repo docs are both silent, and this has billing consequences → escalating. Gate #2 opened; session held alive and idle."),
  ],
};

export const activeRun: RunDetail = { manifest, graphs, sessions, gates, transcripts };

/** The runs list — one hydrated run, the rest as manifests only (as the real list view would be). */
export const runs: RunManifest[] = [
  manifest,
  {
    schema_version: 1,
    run_id: "20260827-1744-ai-new-billing-export",
    route: "grill-with-docs",
    prompt: "Let workspace owners export their billing history as CSV from the billing page.",
    target_repo: "~/workspace/ai-new",
    worktree: "~/workspace/ai-new-wt/billing-export",
    branch: "main",
    created_at: T("17:44", 27),
    ended_at: T("19:02", 27),
    status: "done",
    phase: "finished",
  },
  {
    schema_version: 1,
    run_id: "20260827-0931-ai-new-search-rerank",
    route: "wayfinder",
    prompt: "Search results are ordered badly. Rerank them, and make it obvious why a result ranked where it did.",
    target_repo: "~/workspace/ai-new",
    worktree: "~/workspace/ai-new-wt/search-rerank",
    branch: "main",
    created_at: T("09:31", 27),
    ended_at: T("12:18", 27),
    status: "failed",
    phase: "executing",
  },
  {
    schema_version: 1,
    run_id: "20260826-2210-auto-cli-skeleton",
    route: "grill-with-docs",
    prompt: "Stand up the `auto` CLI skeleton: argument parsing, route selection, run-dir creation.",
    target_repo: "~/workspace/auto",
    worktree: "~/workspace/auto",
    branch: "main",
    created_at: T("22:10", 26),
    ended_at: null,
    status: "gated",
    phase: "questions",
  },
  {
    schema_version: 1,
    run_id: "20260826-1502-ai-new-dark-mode",
    route: "wayfinder",
    prompt: "Dark mode across the app, respecting the OS preference by default.",
    target_repo: "~/workspace/ai-new",
    worktree: "~/workspace/ai-new-wt/dark-mode",
    branch: "main",
    created_at: T("15:02", 26),
    ended_at: T("15:39", 26),
    status: "aborted",
    phase: "questions",
  },
];

/** Coarse per-run counts for the list view, as a real list endpoint would precompute. */
export const runSummaries: Record<string, { nodes: number; done: number; openGates: number; cost: number }> = {
  [activeRunId]: { nodes: 11, done: 5, openGates: 3, cost: 12.42 },
  "20260827-1744-ai-new-billing-export": { nodes: 6, done: 6, openGates: 0, cost: 7.88 },
  "20260827-0931-ai-new-search-rerank": { nodes: 9, done: 4, openGates: 0, cost: 14.05 },
  "20260826-2210-auto-cli-skeleton": { nodes: 3, done: 1, openGates: 1, cost: 2.6 },
  "20260826-1502-ai-new-dark-mode": { nodes: 2, done: 0, openGates: 0, cost: 1.14 },
};
