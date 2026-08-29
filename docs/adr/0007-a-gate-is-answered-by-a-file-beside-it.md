# A gate is answered by a file beside it, which the orchestrator polls for

A gated run has to receive an answer from outside itself — from the
monitoring website, or from a person before the website exists. The
alternatives were a channel into the run process (an HTTP endpoint on the
orchestrator, or IPC the website would have to reach), or the filesystem. We
chose the filesystem: the website writes `gates/<gate-id>.response.json`
beside the gate file, and the gated node's wait is a poll for that path.

Three reasons. The write split stays absolute and legible — the orchestrator
writes everything in the run directory except responses, the website writes
responses and nothing else, so "what answered what" is never a question a
process has to be alive to answer. A response can be written by anything that
can write a file, which is what makes hand-answering work today and keeps the
website a plain file-writer later. And the run directory stays the whole
interface between the two processes, which the resumability plan (backlog
item 2) already depends on: an answer written while the orchestrator is down
is simply there when it comes back.

What is given up: latency up to the poll interval, and a response file is
"not yet an answer" while it is unreadable — a half-saved hand edit is
indistinguishable from silence until it parses, and the poll just looks
again. A permanently malformed response therefore waits forever rather than
failing the node; the website, as the intended writer, writes valid files
atomically.
