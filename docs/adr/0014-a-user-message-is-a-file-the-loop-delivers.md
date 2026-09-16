# A user message is a file the loop delivers, at a turn boundary

Gates let the run ask the user something; nothing let the user speak first.
A run that went quiet with a prototype built and no review raised had no way
forward from the website: the user could see the session waiting and could
neither tell it "approved" nor tell the orchestrator to raise the review. So
every node's two chats — the driven session's transcript and the
orchestrator's record on the node — take a message from the website.

The channel is ADR-0007's, for the same reasons. The website writes
`messages/<message-id>.json` and nothing else; the orchestrator's loop picks a
node's messages up while it reads that node's stream, delivers them, and
writes `messages/<message-id>.delivery.json` beside each. A refusal is a
delivery with a reason, so every message is closed exactly once and the
website shows which way it went.

The two targets deliver differently, because the two chats are different
things:

- **Into the session**, the words go verbatim onto its stdin — but only at a
  turn boundary. Sent mid-turn, the CLI would queue them behind a `result`
  the loop would take for a stale point, and an agent would judge a session
  that is about to answer the user. A turn end that receives a user message is
  therefore not a stale point: the session is about to speak again. The CLI
  does not echo stdin, so the loop writes the message into the captured
  transcript itself, marked as the user's, which is what lets both the website
  and every later trace show what the session is answering.
- **To the orchestrator**, the message invokes a fresh agent on the node under
  the `user-message` trigger, with the user's words and the node's trace. It
  is not a resumed conversation with an earlier agent: interventions stay
  ephemeral for the reasons that made them so. It may land at any moment the
  loop is reading — including over a turn end held for background work,
  which is exactly the stall that motivated this — and the held boundary and
  its grace survive the intervention. A turn end the message got in ahead of
  is still judged, the moment that intervention is over, unless it moved the
  session on. Silence from this agent costs the node
  nothing: a user asking a question is not a stale point left unjudged.

A message is addressed to the node's session *as the user saw it*: one written
against a session the node has since replaced is refused, never delivered into
its successor. A node parked at a gate refuses both kinds — its answer belongs
in the gate, which is what closes the gate's books — and a node that finishes
with messages still undelivered refuses them on the way down.
