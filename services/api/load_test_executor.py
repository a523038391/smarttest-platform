"""Bounded load execution with pinned, public-only HTTP destinations."""
from collections import Counter
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass, field
from ipaddress import ip_address, ip_network
import json
from random import randrange
import re
import socket
from threading import Event, Lock
import time
from typing import Any
from urllib.parse import urlencode, urljoin, urlsplit

import requests
import urllib3
from urllib3 import HTTPConnectionPool, HTTPSConnectionPool

from .load_test_auth import LoadTestAuthConfiguration, LoadTestAuthMode
from .data_factory_executor import WorkflowExecutionError, render_templates
from .load_test_domain import (
    LoadTestMode, LoadTestRecord, LoadTestTrafficMode, empty_metrics,
)


RequestCallable = Callable[..., Any]
Resolver = Callable[[str], Iterable[str]]
SnapshotCallable = Callable[[dict[str, Any]], None]
_SAMPLE_LIMIT = 10_000
_RESPONSE_LIMIT = 1_048_576
_HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_EXPLICITLY_BLOCKED_NETWORKS = (
    ip_network("0.0.0.0/8"),
    ip_network("100.64.0.0/10"),
    ip_network("192.0.0.0/24"),
)


class LoadTestExecutionError(ValueError):
    pass


@dataclass
class _PinnedResponse:
    status: int
    response: Any
    pool: Any

    def close(self) -> None:
        try:
            self.response.close()
        finally:
            self.pool.close()


def _system_resolver(hostname: str) -> Iterable[str]:
    return {entry[4][0] for entry in socket.getaddrinfo(hostname, None)}


@dataclass
class _MetricValues:
    latencies: list[float] = field(default_factory=list)
    total: int = 0
    latency_sum: float = 0.0
    minimum: float | None = None
    maximum: float | None = None
    successful: int = 0
    failed: int = 0
    statuses: Counter[int] = field(default_factory=Counter)
    errors: Counter[str] = field(default_factory=Counter)

    def add(self, latency: float, success: bool, status: int | None, error: str | None) -> None:
        self.total += 1
        self.latency_sum += latency
        self.minimum = latency if self.minimum is None else min(self.minimum, latency)
        self.maximum = latency if self.maximum is None else max(self.maximum, latency)
        if len(self.latencies) < _SAMPLE_LIMIT:
            self.latencies.append(latency)
        else:
            sample_index = randrange(self.total)
            if sample_index < _SAMPLE_LIMIT:
                self.latencies[sample_index] = latency
        self.successful += int(success)
        self.failed += int(not success)
        if status is not None:
            self.statuses[status] += 1
        if error is not None:
            self.errors[error] += 1


class _Metrics:
    def __init__(self, target_names: list[str], started: float) -> None:
        self._lock = Lock()
        self._started = started
        self._all = _MetricValues()
        self._targets = {name: _MetricValues() for name in target_names}
        self._scenarios = _MetricValues()
        self._last_snapshot = started

    def add(self, name: str, latency: float, success: bool,
            status: int | None, error: str | None) -> None:
        with self._lock:
            self._all.add(latency, success, status, error)
            self._targets[name].add(latency, success, status, error)

    def add_scenario(self, latency: float, success: bool) -> None:
        with self._lock:
            self._scenarios.add(latency, success, None, None)

    def periodic_snapshot(self) -> dict[str, Any] | None:
        with self._lock:
            now = time.monotonic()
            if now - self._last_snapshot < 1:
                return None
            self._last_snapshot = now
            return self._snapshot(now)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot(time.monotonic())

    def _snapshot(self, now: float) -> dict[str, Any]:
        elapsed = max(now - self._started, 0.000001)
        result = self._summarize(self._all, elapsed)
        result["target_metrics"] = {
            name: {**self._summarize(values, elapsed), "target_metrics": {}}
            for name, values in self._targets.items()
        }
        result["scenario_metrics"] = self._summarize_scenarios(
            self._scenarios, elapsed,
        )
        return result

    @staticmethod
    def _summarize(values: _MetricValues, elapsed: float) -> dict[str, Any]:
        if not values.total:
            return {key: value for key, value in empty_metrics().items()
                    if key not in {"target_metrics", "scenario_metrics"}}
        ordered = sorted(values.latencies)

        def percentile(ratio: float) -> float:
            index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * ratio)))
            return round(ordered[index], 3)

        return {
            "total_requests": values.total,
            "successful_requests": values.successful,
            "failed_requests": values.failed,
            "requests_per_second": round(values.total / elapsed, 3),
            "average_ms": round(values.latency_sum / values.total, 3),
            "min_ms": round(values.minimum or 0, 3),
            "max_ms": round(values.maximum or 0, 3),
            "p50_ms": percentile(.50), "p95_ms": percentile(.95),
            "p99_ms": percentile(.99),
            "status_counts": {str(key): value for key, value in sorted(values.statuses.items())},
            "error_counts": dict(sorted(values.errors.items())),
        }

    @staticmethod
    def _summarize_scenarios(values: _MetricValues, elapsed: float) -> dict[str, Any]:
        if not values.total:
            return empty_metrics()["scenario_metrics"]
        ordered = sorted(values.latencies)

        def percentile(ratio: float) -> float:
            index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * ratio)))
            return round(ordered[index], 3)

        return {
            "total_scenarios": values.total,
            "successful_scenarios": values.successful,
            "failed_scenarios": values.failed,
            "scenarios_per_second": round(values.total / elapsed, 3),
            "average_ms": round(values.latency_sum / values.total, 3),
            "min_ms": round(values.minimum or 0, 3),
            "max_ms": round(values.maximum or 0, 3),
            "p50_ms": percentile(.50), "p95_ms": percentile(.95),
            "p99_ms": percentile(.99),
        }


class LoadTestExecutor:
    def __init__(self, request_callable: RequestCallable | None = None,
                 resolver: Resolver | None = None, max_workers: int = 100) -> None:
        if not 1 <= max_workers <= 100:
            raise ValueError("max_workers must be between 1 and 100")
        self._request = request_callable
        self._resolver = resolver or _system_resolver
        self._max_workers = max_workers

    def execute(self, definition: LoadTestRecord, *, cancel_event: Event | None = None,
                snapshot_callback: SnapshotCallable | None = None,
                auth_config: LoadTestAuthConfiguration | None = None) -> dict[str, Any]:
        cancel = cancel_event or Event()
        if definition.traffic_mode == LoadTestTrafficMode.SCENARIO:
            return self._execute_scenarios(
                definition, cancel, snapshot_callback, auth_config,
            )
        prepared_targets = [self._prepare_target(target, auth_config)
                            for target in definition.targets]
        destinations = [self._validate_public_url(target["url"])
                        for target in prepared_targets]
        token = self._authentication_token(auth_config, definition.timeout_seconds)
        if token is not None and auth_config is not None:
            prepared_targets = [self._inject_auth(target, auth_config, token)
                                for target in prepared_targets]
        started = time.monotonic()
        deadline = started + definition.duration_seconds
        metrics = _Metrics([target["name"] for target in definition.targets], started)
        sequence, sequence_lock = 0, Lock()

        def worker() -> None:
            nonlocal sequence
            while not cancel.is_set():
                with sequence_lock:
                    if definition.mode == LoadTestMode.COUNT and sequence >= definition.request_count:
                        return
                    if definition.mode == LoadTestMode.DURATION and time.monotonic() >= deadline:
                        return
                    index, sequence = sequence, sequence + 1
                target_index = index % len(definition.targets)
                target = prepared_targets[target_index]
                before = time.monotonic()
                status, success, error = self._request_target(
                    target, destinations[target_index], definition.timeout_seconds
                )
                metrics.add(target["name"], (time.monotonic() - before) * 1000,
                            success, status, error)
                snapshot = metrics.periodic_snapshot()
                if snapshot is not None and snapshot_callback is not None:
                    snapshot_callback(snapshot)
                if definition.mode == LoadTestMode.COUNT:
                    with sequence_lock:
                        if sequence >= definition.request_count:
                            return
                delay = definition.interval_ms / 1000
                if definition.mode == LoadTestMode.DURATION:
                    delay = min(delay, max(0, deadline - time.monotonic()))
                if delay and cancel.wait(delay):
                    return

        workers = min(definition.concurrency, self._max_workers)
        if definition.mode == LoadTestMode.COUNT:
            workers = min(workers, definition.request_count)
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="load-request") as pool:
            futures = [pool.submit(worker) for _ in range(workers)]
            for future in futures:
                future.result()
        return metrics.snapshot()

    def _execute_scenarios(
        self, definition: LoadTestRecord, cancel: Event,
        snapshot_callback: SnapshotCallable | None,
        auth_config: LoadTestAuthConfiguration | None,
    ) -> dict[str, Any]:
        token = self._authentication_token(auth_config, definition.timeout_seconds)
        started = time.monotonic()
        deadline = started + definition.duration_seconds
        metrics = _Metrics([target["name"] for target in definition.targets], started)
        sequence, sequence_lock = 0, Lock()

        def worker() -> None:
            nonlocal sequence
            while not cancel.is_set():
                with sequence_lock:
                    if definition.mode == LoadTestMode.COUNT and sequence >= definition.request_count:
                        return
                    if definition.mode == LoadTestMode.DURATION and time.monotonic() >= deadline:
                        return
                    sequence += 1
                scenario_started = time.monotonic()
                variables = deepcopy(definition.initial_variables)
                scenario_success = True
                scenario_completed = True
                for target_index, raw_target in enumerate(definition.targets):
                    if cancel.is_set():
                        scenario_completed = False
                        break
                    if (definition.mode == LoadTestMode.DURATION
                            and time.monotonic() >= deadline):
                        scenario_completed = False
                        break
                    before = time.monotonic()
                    status: int | None = None
                    try:
                        target = self._render_scenario_target(raw_target, variables)
                        target = self._prepare_target(target, auth_config)
                        if token is not None and auth_config is not None:
                            target = self._inject_auth(target, auth_config, token)
                        destination = self._validate_public_url(target["url"])
                        status, success, error, extracted = self._request_scenario_target(
                            target, destination, definition.timeout_seconds,
                        )
                        if success:
                            variables.update(extracted)
                    except WorkflowExecutionError:
                        success, error = False, "template_error"
                    except (KeyError, TypeError, ValueError):
                        success, error = False, "request_error"
                    metrics.add(
                        raw_target["name"], (time.monotonic() - before) * 1000,
                        success, status, error,
                    )
                    if not success:
                        scenario_success = False
                    snapshot = metrics.periodic_snapshot()
                    if snapshot is not None and snapshot_callback is not None:
                        snapshot_callback(snapshot)
                    if not success and definition.stop_on_failure:
                        break
                    think_time = raw_target["think_time_ms"] / 1000
                    if definition.mode == LoadTestMode.DURATION:
                        think_time = min(think_time, max(0, deadline - time.monotonic()))
                    if think_time and cancel.wait(think_time):
                        scenario_completed = False
                        break
                    if (definition.mode == LoadTestMode.DURATION
                            and time.monotonic() >= deadline
                            and target_index < len(definition.targets) - 1):
                        scenario_completed = False
                        break
                if scenario_completed:
                    metrics.add_scenario(
                        (time.monotonic() - scenario_started) * 1000, scenario_success,
                    )
                snapshot = metrics.periodic_snapshot()
                if snapshot is not None and snapshot_callback is not None:
                    snapshot_callback(snapshot)
                if cancel.is_set():
                    return
                if definition.mode == LoadTestMode.COUNT:
                    with sequence_lock:
                        if sequence >= definition.request_count:
                            return
                delay = definition.interval_ms / 1000
                if definition.mode == LoadTestMode.DURATION:
                    delay = min(delay, max(0, deadline - time.monotonic()))
                if delay and cancel.wait(delay):
                    return

        workers = min(definition.concurrency, self._max_workers)
        if definition.mode == LoadTestMode.COUNT:
            workers = min(workers, definition.request_count)
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="load-scenario") as pool:
            futures = [pool.submit(worker) for _ in range(workers)]
            for future in futures:
                future.result()
        return metrics.snapshot()

    @staticmethod
    def _render_scenario_target(target: dict[str, Any],
                                variables: dict[str, Any]) -> dict[str, Any]:
        rendered = dict(target)
        for field_name in ("url", "headers", "query", "body"):
            rendered[field_name] = render_templates(target[field_name], variables)
        return rendered

    def _prepare_target(self, target: dict[str, Any],
                        config: LoadTestAuthConfiguration | None) -> dict[str, Any]:
        prepared = dict(target)
        prepared["headers"] = self._validated_headers(target["headers"])
        prepared["query"] = dict(target["query"])
        prepared["url"] = self._resolve_url(target["url"], config.base_url if config else None)
        return prepared

    @staticmethod
    def _resolve_url(url: str, base_url: str | None) -> str:
        parsed = urlsplit(url)
        if parsed.scheme:
            return url
        if parsed.netloc or not base_url:
            raise LoadTestExecutionError("HTTP destination is not allowed")
        return urljoin(base_url.rstrip("/") + "/", url)

    def _authentication_token(self, config: LoadTestAuthConfiguration | None,
                              timeout: float) -> str | None:
        if config is None or config.mode is None:
            return None
        if config.mode is LoadTestAuthMode.TOKEN:
            if not config.token:
                raise LoadTestExecutionError("Load test authentication failed")
            return config.token
        return self._login(config, timeout)

    def _login(self, config: LoadTestAuthConfiguration, timeout: float) -> str:
        response = None
        try:
            if not config.login_url or config.account is None or config.password is None:
                raise ValueError
            url = self._resolve_url(config.login_url, config.base_url)
            destination = self._validate_public_url(url)
            target = {
                "method": "POST", "url": url, "headers": {}, "query": {},
                "body": {config.account_field: config.account,
                         config.password_field: config.password},
            }
            if self._request is None:
                response = self._request_pinned(*destination, target, timeout)
            else:
                response = self._request(
                    method="POST", url=url, headers={}, params={}, json=target["body"],
                    timeout=timeout, allow_redirects=False, stream=True,
                )
            raw_status = getattr(response, "status_code", None)
            status = int(raw_status if raw_status is not None else response.status)
            if status < 200 or status >= 300:
                raise ValueError
            payload = json.loads(self._read_limited_body(response).decode("utf-8"))
            token = self._extract_token(payload, config.token_path)
            if not isinstance(token, str) or not token:
                raise ValueError
            return token
        except Exception:
            raise LoadTestExecutionError("Load test authentication failed") from None
        finally:
            if response is not None and callable(getattr(response, "close", None)):
                response.close()

    @staticmethod
    def _read_limited_body(response: Any) -> bytes:
        source = getattr(response, "response", response)
        if callable(getattr(source, "read", None)):
            body = source.read(_RESPONSE_LIMIT + 1)
        elif callable(getattr(source, "iter_content", None)):
            chunks, size = [], 0
            for chunk in source.iter_content(chunk_size=65_536):
                size += len(chunk)
                if size > _RESPONSE_LIMIT:
                    raise ValueError
                chunks.append(chunk)
            body = b"".join(chunks)
        elif callable(getattr(source, "json", None)):
            body = json.dumps(source.json(), separators=(",", ":")).encode("utf-8")
        else:
            body = source.content
        if not isinstance(body, bytes) or len(body) > _RESPONSE_LIMIT:
            raise ValueError
        return body

    @staticmethod
    def _extract_token(payload: Any, path: str) -> Any:
        current = payload
        normalized_path = re.sub(r"\[([0-9]+)\]", r".\1", path)
        parts = normalized_path.split(".")
        if any(not part for part in parts):
            raise ValueError
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
                current = current[int(part)]
            else:
                raise ValueError
        return current

    @staticmethod
    def _inject_auth(target: dict[str, Any], config: LoadTestAuthConfiguration,
                     token: str) -> dict[str, Any]:
        prepared = dict(target)
        header_name = config.auth_header
        headers = {key: value for key, value in target["headers"].items()
                   if key.lower() != header_name.lower()}
        prefix = config.auth_prefix.strip()
        value = f"{prefix} {token}" if prefix else token
        headers.update(LoadTestExecutor._validated_headers({header_name: value}))
        prepared["headers"] = headers
        return prepared

    @staticmethod
    def _validated_headers(raw_headers: dict[str, Any]) -> dict[str, str]:
        headers: dict[str, str] = {}
        for raw_name, raw_value in raw_headers.items():
            name, value = str(raw_name), str(raw_value)
            if not _HEADER_NAME.fullmatch(name) or "\r" in value or "\n" in value:
                raise LoadTestExecutionError("HTTP request header is not allowed")
            headers[name] = value
        return headers

    def _request_target(self, target: dict[str, Any], destination: tuple[Any, tuple[Any, ...]],
                        timeout: float) -> tuple[int | None, bool, str | None]:
        response = None
        try:
            parsed, addresses = destination
            if self._request is None:
                response = self._request_pinned(parsed, addresses, target, timeout)
            else:
                response = self._request(
                    method=target["method"], url=target["url"],
                    headers=dict(target["headers"]), params=dict(target["query"]),
                    json=target["body"], timeout=timeout,
                    allow_redirects=False, stream=True,
                )
            raw_status = getattr(response, "status_code", None)
            status = int(raw_status if raw_status is not None else response.status)
            success = status in set(target["expected_statuses"])
            return status, success, None if success else "unexpected_status"
        except (requests.RequestException, urllib3.exceptions.HTTPError,
                OSError, TypeError, ValueError):
            return None, False, "request_error"
        finally:
            if response is not None and callable(getattr(response, "close", None)):
                response.close()

    def _request_scenario_target(
        self, target: dict[str, Any], destination: tuple[Any, tuple[Any, ...]],
        timeout: float,
    ) -> tuple[int | None, bool, str | None, dict[str, Any]]:
        response = None
        try:
            parsed, addresses = destination
            if self._request is None:
                response = self._request_pinned(parsed, addresses, target, timeout)
            else:
                response = self._request(
                    method=target["method"], url=target["url"],
                    headers=dict(target["headers"]), params=dict(target["query"]),
                    json=target["body"], timeout=timeout,
                    allow_redirects=False, stream=True,
                )
            raw_status = getattr(response, "status_code", None)
            status = int(raw_status if raw_status is not None else response.status)
            if status not in set(target["expected_statuses"]):
                return status, False, "unexpected_status", {}
            if not target["extractors"]:
                return status, True, None, {}
            try:
                payload = json.loads(self._read_limited_body(response))
                extracted = {
                    name: deepcopy(self._extract_token(payload, path))
                    for name, path in target["extractors"].items()
                }
            except (TypeError, ValueError, UnicodeError):
                return status, False, "extractor_error", {}
            return status, True, None, extracted
        except (requests.RequestException, urllib3.exceptions.HTTPError,
                OSError, TypeError, ValueError):
            return None, False, "request_error", {}
        finally:
            if response is not None and callable(getattr(response, "close", None)):
                response.close()

    def _request_pinned(self, parsed: Any, addresses: tuple[Any, ...],
                        target: dict[str, Any], timeout: float) -> Any:
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        query = urlencode(target["query"], doseq=True)
        existing = parsed.query
        path = parsed.path or "/"
        if existing or query:
            path += "?" + "&".join(item for item in (existing, query) if item)
        headers = {str(key): str(value) for key, value in target["headers"].items()}
        if not any(key.lower() == "host" for key in headers):
            headers["Host"] = parsed.netloc
        body = None
        if target["body"] is not None:
            body = json.dumps(target["body"], ensure_ascii=False, separators=(",", ":")).encode()
            if not any(key.lower() == "content-type" for key in headers):
                headers["Content-Type"] = "application/json"
        last_error: Exception | None = None
        for address in addresses:
            pool_type = HTTPSConnectionPool if parsed.scheme.lower() == "https" else HTTPConnectionPool
            options: dict[str, Any] = {"timeout": timeout, "maxsize": 1, "block": True}
            if pool_type is HTTPSConnectionPool:
                options.update({"cert_reqs": "CERT_REQUIRED", "assert_hostname": parsed.hostname,
                                "server_hostname": parsed.hostname})
            pool = pool_type(str(address), port=port, **options)
            response = None
            try:
                response = pool.urlopen(
                    target["method"], path, body=body, headers=headers, redirect=False,
                    retries=False, preload_content=False,
                )
                return _PinnedResponse(int(response.status), response, pool)
            except Exception as exc:
                try:
                    if response is not None:
                        response.close()
                finally:
                    pool.close()
                if isinstance(exc, (urllib3.exceptions.HTTPError, OSError)):
                    last_error = exc
                    continue
                raise
        raise urllib3.exceptions.HTTPError("validated destination unavailable") from last_error

    @staticmethod
    def _is_public_address(address: Any) -> bool:
        mapped = getattr(address, "ipv4_mapped", None)
        candidate = mapped or address
        if any(candidate in network for network in _EXPLICITLY_BLOCKED_NETWORKS
               if candidate.version == network.version):
            return False
        return bool(
            candidate.is_global
            and not candidate.is_private
            and not candidate.is_loopback
            and not candidate.is_link_local
            and not candidate.is_multicast
            and not candidate.is_reserved
            and not candidate.is_unspecified
        )

    def _validate_public_url(self, url: str) -> tuple[Any, tuple[Any, ...]]:
        try:
            parsed = urlsplit(url)
            if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
                raise ValueError
            if parsed.username is not None or parsed.password is not None:
                raise ValueError
            _ = parsed.port
            try:
                addresses = (ip_address(parsed.hostname),)
            except ValueError:
                addresses = tuple(ip_address(item) for item in self._resolver(parsed.hostname))
            if not addresses or any(not self._is_public_address(address) for address in addresses):
                raise ValueError
            return parsed, addresses
        except Exception:
            raise LoadTestExecutionError("HTTP destination is not allowed") from None