"""The `auto serve` API: a long-lived local server over the runs directory.

Independent of any run process on purpose — a finished run stays viewable, a
crashed run does not take the window with it, and one server lists every run
rather than one run per port. The server only ever *reads* run state (gate
responses, the one thing the website writes, arrive with the gate-answering
ticket); it watches the tree, pushes change notifications over SSE, tails
transcripts incrementally, and serves the prebuilt site from inside the
package so the CLI needs no node toolchain at runtime.
"""

from __future__ import annotations

from auto.web.app import create_app

__all__ = ["create_app"]
