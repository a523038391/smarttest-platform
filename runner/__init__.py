"""Container-local execution runner; Docker lifecycle is managed elsewhere."""

from importlib import import_module
from typing import Any

from .context import load_parameters


_LAZY_EXPORTS = {
    "Adapter": ".adapters",
    "AdapterResult": ".adapters",
    "EventSink": ".agent",
    "HTTPAdapter": ".adapters",
    "HttpAdapter": ".adapters",
    "PlaywrightAdapter": ".adapters",
    "PytestAdapter": ".adapters",
    "RunnerAgent": ".agent",
    "secret_path": ".secrets",
    "SubprocessAdapter": ".adapters",
}


def __getattr__(name: str) -> Any:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value

__all__ = [
    "Adapter",
    "AdapterResult",
    "EventSink",
    "HTTPAdapter",
    "HttpAdapter",
    "load_parameters",
    "PlaywrightAdapter",
    "PytestAdapter",
    "RunnerAgent",
    "secret_path",
    "SubprocessAdapter",
]