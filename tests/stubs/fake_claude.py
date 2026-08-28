#!/usr/bin/env python3
"""A stand-in for the `claude` binary, for testing the real launcher's plumbing.

Speaks the same shape of stream-json: one turn per line of input, ending in a
`result` event, staying alive between turns. Echoes what it was sent so a test
can assert on what actually reached the session.
"""

from __future__ import annotations

import json
import os
import sys


def main() -> int:
    argv = sys.argv[1:]
    session_id = argv[argv.index("--session-id") + 1] if "--session-id" in argv else "?"
    if os.environ.get("FAKE_CLAUDE_FAIL_TO_START"):
        print("fake claude: refusing to start", file=sys.stderr)
        return 2

    emit({"type": "system", "subtype": "init", "session_id": session_id, "argv": argv})
    turn = 0
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        turn += 1
        message = json.loads(line)
        text = message["message"]["content"][0]["text"]
        emit(
            {
                "type": "assistant",
                "session_id": session_id,
                "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
            }
        )
        emit(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": f"received: {text}",
                "session_id": session_id,
                "num_turns": turn,
                "total_cost_usd": 0.01 * turn,
                "stop_reason": "end_turn",
                "terminal_reason": "completed",
            }
        )
    return 0


def emit(event: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
