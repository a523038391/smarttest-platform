"""Public, non-secret execution parameters supplied to subprocess scripts."""
import json
import os
from pathlib import Path
from typing import Any

MAX_PARAMETERS_BYTES = 1_048_576


def load_parameters(path: Path | str | None = None) -> dict[str, Any]:
    """Load the immutable data-row parameter snapshot for the current task."""
    configured = path or os.getenv("SMARTTEST_PARAMETERS_FILE")
    if not configured:
        raise RuntimeError("test parameters are unavailable")
    parameter_path = Path(configured)
    with parameter_path.open("rb") as stream:
        raw = stream.read(MAX_PARAMETERS_BYTES + 1)
    if len(raw) > MAX_PARAMETERS_BYTES:
        raise ValueError("test parameters exceed the supported size")
    try:
        result = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("test parameters are invalid") from None
    if not isinstance(result, dict):
        raise ValueError("test parameters must be a JSON object")
    return result


__all__ = ["load_parameters"]