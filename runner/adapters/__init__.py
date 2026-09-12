"""Runner execution adapters."""

from .base import Adapter, AdapterResult
from .http import HTTPAdapter, HttpAdapter
from .subprocess import PlaywrightAdapter, PytestAdapter, SubprocessAdapter

__all__ = [
    "Adapter",
    "AdapterResult",
    "HTTPAdapter",
    "HttpAdapter",
    "PlaywrightAdapter",
    "PytestAdapter",
    "SubprocessAdapter",
]