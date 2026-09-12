from collections import defaultdict, deque
from collections.abc import Iterator
from contextlib import contextmanager
from threading import BoundedSemaphore, RLock
from time import monotonic


class LoginRateLimited(RuntimeError):
    pass


class LoginThrottle:
    def __init__(
        self, max_failures: int = 5, window_seconds: float = 300,
        max_concurrent: int = 4,
    ) -> None:
        self._max_failures = max_failures
        self._window_seconds = window_seconds
        self._attempts: dict[str, deque[float]] = defaultdict(deque)
        self._lock = RLock()
        self._slots = BoundedSemaphore(max_concurrent)

    @contextmanager
    def attempt(self, key: str) -> Iterator[None]:
        if not self._slots.acquire(blocking=False):
            raise LoginRateLimited("login is temporarily rate limited")
        try:
            with self._lock:
                attempts = self._attempts[key]
                cutoff = monotonic() - self._window_seconds
                while attempts and attempts[0] <= cutoff:
                    attempts.popleft()
                if len(attempts) >= self._max_failures:
                    raise LoginRateLimited("login is temporarily rate limited")
            yield
        finally:
            self._slots.release()

    def record_failure(self, key: str) -> None:
        with self._lock:
            self._attempts[key].append(monotonic())

    def record_success(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)