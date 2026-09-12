"""HTTP task adapter with defense-in-depth destination validation.

The host/container proxy and network policy remain the authoritative egress
control; local URL validation reduces accidental SSRF exposure.
"""

from collections.abc import Callable, Collection, Iterable, Mapping
from ipaddress import IPv4Address, IPv6Address, ip_address
from pathlib import Path
import re
import socket
from time import monotonic
from typing import Any
from urllib.parse import urlsplit

import requests

from packages.protocol import AssertionRule, ExtractionRule, HttpStep, ResultOutcome, TaskEnvelope

from .base import AdapterResult, deadline_remaining

Resolver = Callable[[str], Iterable[str]]
TEMPLATE_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]{0,127})\}")


def _system_resolver(hostname: str) -> Iterable[str]:
    return {item[4][0] for item in socket.getaddrinfo(hostname, None)}


class HTTPAdapter:
    def __init__(
        self,
        session: requests.Session | None = None,
        resolver: Resolver | None = None,
    ) -> None:
        self._session = session if session is not None else requests.Session()
        self._resolver = resolver if resolver is not None else _system_resolver

    def run(self, task: TaskEnvelope, workspace: Path) -> AdapterResult:
        del workspace
        try:
            deadline_remaining(task)
        except TimeoutError:
            return self._deadline_exceeded()

        context = {key: value for key, value in task.parameters.items() if key != "request"}
        summaries: list[dict[str, Any]] = []
        assertions: list[dict[str, Any]] = []
        extraction_names: list[str] = []
        primary: AdapterResult | None = None

        for step in task.execution_policy.before_steps:
            result, extracted, checked = self._execute(
                task, step.request, step.assertions, step.extractions, context, step.name, "before"
            )
            summaries.append(self._step_summary(step.name, "before", result))
            assertions.extend(checked)
            extraction_names.extend(extracted)
            context.update(extracted)
            if result.outcome is not ResultOutcome.SUCCEEDED and step.required:
                primary = result
                break

        if primary is None:
            request = task.parameters.get("request")
            result, extracted, checked = self._execute(
                task, request, task.execution_policy.assertions,
                task.execution_policy.extractions, context, "request", "main",
            )
            summaries.append(self._step_summary("request", "main", result))
            assertions.extend(checked)
            extraction_names.extend(extracted)
            context.update(extracted)
            primary = result

        for step in task.execution_policy.after_steps:
            result, extracted, checked = self._execute(
                task, step.request, step.assertions, step.extractions, context, step.name, "after"
            )
            summaries.append(self._step_summary(step.name, "after", result))
            assertions.extend(checked)
            extraction_names.extend(extracted)
            context.update(extracted)
            if result.outcome is not ResultOutcome.SUCCEEDED and step.required:
                if primary.outcome is ResultOutcome.SUCCEEDED:
                    primary = result

        details = {
            **dict(primary.details), "steps": summaries, "assertions": assertions,
            "extractions": extraction_names,
        }
        return AdapterResult(
            primary.outcome, primary.message, details, timed_out=primary.timed_out
        )

    def _execute(
        self, task: TaskEnvelope, request_spec: Any,
        assertions: tuple[AssertionRule, ...], extractions: tuple[ExtractionRule, ...],
        context: Mapping[str, Any], name: str, phase: str,
    ) -> tuple[AdapterResult, dict[str, Any], list[dict[str, Any]]]:
        try:
            if not isinstance(request_spec, Mapping):
                raise ValueError("request must be a mapping")
            request = self._substitute(request_spec, context)
            method = str(request.get("method", "GET")).upper()
            url = str(request["url"])
            self._validate_url(url)
            headers = request.get("headers", {})
            if not isinstance(headers, Mapping):
                raise ValueError("headers must be a mapping")
            timeout = float(request.get("timeout", 30.0))
            if timeout <= 0:
                raise ValueError("timeout must be positive")
            timeout = min(timeout, deadline_remaining(task))
            expected_codes = self._expected_codes(request.get("expected_status", 200))
        except TimeoutError:
            return self._deadline_exceeded(), {}, []
        except (KeyError, OSError, TypeError, ValueError):
            return AdapterResult(ResultOutcome.FAILED, f"Invalid HTTP {phase} step"), {}, []

        started = monotonic()
        try:
            response = self._session.request(
                method=method, url=url, headers=dict(headers), params=request.get("params"),
                json=request.get("json"), data=request.get("data"), timeout=timeout,
                allow_redirects=False,
            )
        except requests.Timeout:
            return AdapterResult(
                ResultOutcome.TIMED_OUT, f"HTTP step {name} timed out",
                {"timed_out": True}, timed_out=True,
            ), {}, []
        except requests.RequestException as exc:
            return AdapterResult(
                ResultOutcome.INFRA_ERROR, f"HTTP step {name} failed",
                {"error_type": type(exc).__name__},
            ), {}, []

        elapsed_ms = max(0, int((monotonic() - started) * 1000))
        checked = [{"source": "status_code", "operator": "in", "passed": response.status_code in expected_codes}]
        checked.extend(self._assert(response, elapsed_ms, rule) for rule in assertions)
        passed = all(item["passed"] for item in checked)
        try:
            extracted = {}
            for rule in extractions:
                value = self._extract(response, rule)
                if value is not None:
                    extracted[rule.name] = value
        except (KeyError, TypeError, ValueError, re.error):
            return AdapterResult(
                ResultOutcome.FAILED, f"Required extraction failed in HTTP step {name}",
                {"status_code": response.status_code},
            ), {}, checked
        outcome = ResultOutcome.SUCCEEDED if passed else ResultOutcome.FAILED
        return AdapterResult(
            outcome, "HTTP assertions passed" if passed else "HTTP assertion failed",
            {"status_code": response.status_code, "expected_status": sorted(expected_codes)},
        ), extracted, checked

    def _assert(self, response: requests.Response, elapsed_ms: int, rule: AssertionRule) -> dict[str, Any]:
        try:
            actual = self._source_value(response, elapsed_ms, rule.source, rule.expression)
            passed = self._compare(actual, rule.expected, rule.operator)
        except (KeyError, TypeError, ValueError, re.error):
            passed = False
        return {"source": rule.source, "operator": rule.operator, "passed": passed}

    def _extract(self, response: requests.Response, rule: ExtractionRule) -> Any:
        try:
            value = self._source_value(response, 0, rule.source, rule.expression)
        except (KeyError, TypeError, ValueError, re.error):
            if rule.required:
                raise
            return None
        if value is None and rule.required:
            raise ValueError("required extraction is empty")
        return value

    def _source_value(
        self, response: requests.Response, elapsed_ms: int, source: str, expression: str | None,
    ) -> Any:
        if source == "status_code":
            return response.status_code
        if source == "response_time_ms":
            return elapsed_ms
        if source == "header":
            return response.headers.get(str(expression))
        if source == "cookie":
            return response.cookies.get(str(expression))
        if source == "json_path":
            return self._json_path(response.json(), str(expression))
        body = response.text[:65_536]
        if source == "regex":
            match = re.search(str(expression), body)
            if match is None:
                raise ValueError("regex did not match")
            return match.group(1) if match.lastindex else match.group(0)
        if source == "body":
            return body
        raise ValueError("unsupported value source")

    @staticmethod
    def _compare(actual: Any, expected: Any, operator: str) -> bool:
        if operator == "eq": return actual == expected
        if operator == "ne": return actual != expected
        if operator == "gt": return actual > expected
        if operator == "gte": return actual >= expected
        if operator == "lt": return actual < expected
        if operator == "lte": return actual <= expected
        if operator == "contains": return expected in actual
        if operator == "matches": return re.search(str(expected), str(actual)[:65_536]) is not None
        return False

    @classmethod
    def _substitute(cls, value: Any, context: Mapping[str, Any]) -> Any:
        if isinstance(value, Mapping):
            return {key: cls._substitute(item, context) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._substitute(item, context) for item in value]
        if not isinstance(value, str):
            return value
        match = TEMPLATE_PATTERN.fullmatch(value)
        if match:
            resolved = context[match.group(1)]
            if resolved is None:
                raise ValueError("template value is empty")
            return resolved

        def replace(match: re.Match[str]) -> str:
            resolved = context[match.group(1)]
            if resolved is None:
                raise ValueError("template value is empty")
            return str(resolved)

        return TEMPLATE_PATTERN.sub(replace, value)

    @staticmethod
    def _json_path(value: Any, path: str) -> Any:
        if path == "$":
            return value
        if not path.startswith("$."):
            raise ValueError("only simple JSON paths are supported")
        current = value
        for token in path[2:].split("."):
            match = re.fullmatch(r"([^\[\]]+)(?:\[([0-9]+)\])?", token)
            if match is None or not isinstance(current, Mapping):
                raise ValueError("invalid JSON path")
            current = current[match.group(1)]
            if match.group(2) is not None:
                current = current[int(match.group(2))]
        return current

    @staticmethod
    def _step_summary(name: str, phase: str, result: AdapterResult) -> dict[str, Any]:
        return {"name": name, "phase": phase, "outcome": result.outcome.value}

    def _validate_url(self, url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {"http", "https"}:
            raise ValueError("unsupported URL scheme")
        if parsed.hostname is None:
            raise ValueError("URL must include a hostname")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("URL userinfo is forbidden")
        _ = parsed.port  # Force validation of malformed ports.

        try:
            addresses = (ip_address(parsed.hostname),)
        except ValueError:
            addresses = tuple(ip_address(value) for value in self._resolver(parsed.hostname))
        if not addresses or any(self._is_unsafe(address) for address in addresses):
            raise ValueError("URL resolves to a non-public address")

    @staticmethod
    def _is_unsafe(address: IPv4Address | IPv6Address) -> bool:
        return not address.is_global

    @staticmethod
    def _deadline_exceeded() -> AdapterResult:
        return AdapterResult(
            ResultOutcome.TIMED_OUT,
            "Task deadline has passed",
            {"timed_out": True},
            timed_out=True,
        )

    @staticmethod
    def _expected_codes(value: Any) -> set[int]:
        values = (
            value
            if isinstance(value, Collection) and not isinstance(value, (str, bytes))
            else [value]
        )
        codes = {int(item) for item in values}
        if not codes or any(code < 100 or code > 599 for code in codes):
            raise ValueError("expected_status must contain HTTP status codes")
        return codes


HttpAdapter = HTTPAdapter