# A `cannot` response is consumed by the loop, never delivered

#8 says a gate response is an ordinary intervention trigger: a fresh agent is
invoked with the response rendered in and delivers it via `send_to_session`.
Read literally, that covers a task-completion `cannot` too — but there is
nothing to deliver. The session behind the gate has been waiting for facts
that will now never exist, no message changes what happens next, and the tool
layer's own rule that an undelivered answer blocks every other move would
force the agent through a delivery that is pure ceremony before it could
fail the node.

So `cannot` is the one response the loop acts on itself: it stamps the gate
answered, fails the node with the user's words as the failure's account, and
never invokes an agent. This stays inside the loop's charter — it decides
arithmetic, not judgment — because the judgment was the user's and the
response file already carries it. Every other decision, `done` included, still
rides the ordinary intervention path, and per-kind decision validation on the
poll (a `cannot` is only readable off a task-completion gate) is what keeps
the shortcut from ever firing anywhere else.

What is given up: no intervention record accounts for the node's end — the
session record's summary and the answered gate are together the whole story —
and a session is brought down without ever hearing that the run gave up on
its ticket, which a harness-agnostic session was never owed anyway.
