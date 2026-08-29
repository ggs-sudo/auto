# A subtree satisfies a dependency only when complete, not merely settled

#14 says a node satisfies a dependency only when it is complete and every
graph beneath it is **terminal**. Read literally, "terminal" admits a subtree
that is merely settled: a subgraph whose one node failed has nothing left to
run, so a validation ticket blocked on the spawning node would dispatch — over
work that was never done. That is the exact accident the subtree rule exists
to prevent, arriving through its own wording.

So the readiness check requires **complete**: every node in every graph
beneath the blocker is done, all the way down. A failed node anywhere in the
subtree keeps the dependency unsatisfied for good, exactly as a failed blocker
does in its own graph — the two failure modes get one semantics, and "a
failure blocks only its dependents" holds across graph boundaries the same way
it holds within one. A spawned graph the run does not hold blocks the same
way: a subtree that cannot be vouched for is not one that can be depended
past.

What is given up: a run whose subgraph fails ends with the dependent still
`pending` rather than in any state that says "abandoned because the subtree
failed". The run's own `failed` status carries that verdict; the node itself
just never became ready.
