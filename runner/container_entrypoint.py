"""JSON-lines process boundary used inside the one-shot Runner image."""
import json
from pathlib import Path
import sys
from threading import Lock

from packages.protocol import EventEnvelope, TaskEnvelope

from .agent import RunnerAgent

MAX_TASK_BYTES = 1_048_576


def _write(kind: str, data: str, lock: Lock) -> None:
    with lock:
        sys.stdout.write('{"kind":' + json.dumps(kind) + ',"data":' + data + "}\n")
        sys.stdout.flush()


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_TASK_BYTES + 1)
    if not raw or len(raw) > MAX_TASK_BYTES:
        sys.stderr.write("task envelope is empty or too large\n")
        return 2
    try:
        task = TaskEnvelope.model_validate_json(raw)
    except Exception:
        sys.stderr.write("task envelope is invalid\n")
        return 2
    lock = Lock()

    def emit(event: EventEnvelope) -> None:
        _write("event", event.model_dump_json(), lock)

    result = RunnerAgent(
        task, Path("/workspace"), emit, artifact_root=Path("/artifacts")
    ).run()
    _write("result", result.model_dump_json(), lock)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())