"""Synchronous, bounded Data Factory DAG execution with safe HTTP egress."""
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from ipaddress import ip_address
import json
import re
import socket
from typing import Any
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

import requests
import urllib3
from urllib3 import HTTPConnectionPool, HTTPSConnectionPool

from .data_factory_domain import (
    WorkflowDefinition, WorkflowEdge, WorkflowEdgeOutcome, WorkflowNode,
    WorkflowNodeType, validate_workflow_graph,
)


MAX_RESPONSE_BYTES = 1024 * 1024
MAX_HTTP_TIMEOUT_SECONDS = 120.0
_TEMPLATE = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]{0,127})\}\}")
_WHOLE_TEMPLATE = re.compile(r"^\{\{([A-Za-z_][A-Za-z0-9_]{0,127})\}\}$")
RequestCallable = Callable[..., Any]
Resolver = Callable[[str], Iterable[str]]


class WorkflowExecutionError(ValueError):
    """Raised when a workflow cannot be executed."""


@dataclass(frozen=True, slots=True)
class WorkflowRunRecord:
    id: str
    workflow_id: str
    status: str
    node_results: dict[str, Any]
    variables: dict[str, Any]
    started_at: str
    finished_at: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "workflow_id": self.workflow_id, "status": self.status,
            "node_results": self.node_results, "variables": self.variables,
            "started_at": self.started_at, "finished_at": self.finished_at,
        }


@dataclass(frozen=True, slots=True)
class _NodeExecution:
    status: str
    details: dict[str, Any]
    extracted: dict[str, Any]
    branch: bool | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _node_type_value(node: WorkflowNode) -> str:
    return WorkflowNodeType(node.type).value


def _system_resolver(hostname: str) -> Iterable[str]:
    return {entry[4][0] for entry in socket.getaddrinfo(hostname, None)}


def _stringify_template_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def render_templates(value: Any, variables: Mapping[str, Any]) -> Any:
    """Recursively render {{variable}} tokens, preserving whole-token JSON types."""
    if isinstance(value, str):
        whole = _WHOLE_TEMPLATE.fullmatch(value)
        if whole:
            name = whole.group(1)
            if name not in variables:
                raise WorkflowExecutionError("a required template variable is missing")
            return deepcopy(variables[name])

        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in variables:
                raise WorkflowExecutionError("a required template variable is missing")
            return _stringify_template_value(variables[name])

        return _TEMPLATE.sub(replace, value)
    if isinstance(value, list):
        return [render_templates(item, variables) for item in value]
    if isinstance(value, Mapping):
        return {key: render_templates(item, variables) for key, item in value.items()}
    return deepcopy(value)


def extract_json_path(value: Any, path: str) -> Any:
    """Evaluate the simple $.field[index].child path supported by the frontend."""
    if path == "$":
        return deepcopy(value)
    if not path.startswith("$."):
        raise ValueError("invalid JSON path")
    current = value
    for token in path[2:].split("."):
        match = re.fullmatch(r"([^\[\]]+)(?:\[([0-9]+)\])?", token)
        if match is None or not isinstance(current, Mapping):
            raise ValueError("invalid JSON path")
        current = current[match.group(1)]
        if match.group(2) is not None:
            if not isinstance(current, list):
                raise ValueError("invalid JSON path")
            current = current[int(match.group(2))]
    return deepcopy(current)


class DataFactoryExecutor:
    def __init__(
        self,
        request_callable: RequestCallable | None = None,
        resolver: Resolver | None = None,
        max_concurrency: int = 20,
    ) -> None:
        if not 1 <= max_concurrency <= 100:
            raise ValueError("max_concurrency must be between 1 and 100")
        self._request = request_callable
        self._resolver = resolver or _system_resolver
        self._max_concurrency = max_concurrency

    def execute(
        self,
        workflow: WorkflowDefinition,
        variables: Mapping[str, Any] | None = None,
        *,
        workflow_id: str | None = None,
    ) -> WorkflowRunRecord:
        validate_workflow_graph(workflow)
        if not workflow.nodes:
            raise WorkflowExecutionError("cannot execute an empty workflow")
        context = deepcopy(dict(workflow.variables))
        context.update(deepcopy(dict(variables or {})))
        started_at = _now()
        results, failed = self._run_graph(workflow, context)
        return WorkflowRunRecord(
            str(uuid4()), str(workflow_id or workflow.id),
            "FAILED" if failed else "SUCCEEDED",
            results, context, started_at, _now(),
        )

    def debug(
        self,
        workflow: WorkflowDefinition,
        node_id: str,
        variables: Mapping[str, Any] | None = None,
        *,
        workflow_id: str | None = None,
    ) -> WorkflowRunRecord:
        validate_workflow_graph(workflow)
        node = next((item for item in workflow.nodes if item.id == node_id), None)
        if node is None:
            raise WorkflowExecutionError("debug node does not exist")
        context = deepcopy(dict(workflow.variables))
        context.update(deepcopy(dict(variables or {})))
        started_at = _now()
        execution, result = self._execute_node(node, context)
        context.update(execution.extracted)
        return WorkflowRunRecord(
            str(uuid4()), str(workflow_id or workflow.id),
            "SUCCEEDED" if execution.status == "SUCCEEDED" else "FAILED",
            {node.id: result}, context, started_at, _now(),
        )

    def _run_graph(
        self, workflow: WorkflowDefinition, context: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        nodes = {node.id: node for node in workflow.nodes}
        incoming: dict[str, list[WorkflowEdge]] = {node.id: [] for node in workflow.nodes}
        for edge in workflow.edges:
            incoming[edge.target].append(edge)
        pending = {node.id for node in workflow.nodes}
        executions: dict[str, _NodeExecution] = {}
        raw_results: dict[str, dict[str, Any]] = {}

        while pending:
            changed = True
            while changed:
                changed = False
                for node in workflow.nodes:
                    edges = incoming[node.id]
                    if node.id not in pending or not edges:
                        continue
                    if all(edge.source in executions for edge in edges) and not any(
                        self._edge_active(edge, executions[edge.source]) for edge in edges
                    ):
                        timestamp = _now()
                        executions[node.id] = _NodeExecution("SKIPPED", {}, {})
                        raw_results[node.id] = {
                            "node_id": node.id, "name": node.name,
                            "type": _node_type_value(node),
                            "status": "SKIPPED", "started_at": timestamp,
                            "finished_at": timestamp,
                        }
                        pending.remove(node.id)
                        changed = True

            ready = [
                node for node in workflow.nodes
                if node.id in pending and (
                    not incoming[node.id]
                    or (
                        all(edge.source in executions for edge in incoming[node.id])
                        and any(self._edge_active(edge, executions[edge.source])
                                for edge in incoming[node.id])
                    )
                )
            ]
            if not ready:
                raise WorkflowExecutionError("workflow scheduling could not make progress")
            limit = self._wave_limit(ready, incoming, nodes, executions)
            snapshot = deepcopy(context)
            with ThreadPoolExecutor(max_workers=limit) as pool:
                completed = list(pool.map(lambda node: self._execute_node(node, snapshot), ready))
            for node, (execution, result) in zip(ready, completed, strict=True):
                executions[node.id] = execution
                raw_results[node.id] = result
                pending.remove(node.id)
            # Node declaration order defines collision precedence, independent of finish order.
            for node in workflow.nodes:
                if node in ready:
                    context.update(executions[node.id].extracted)

        ordered_results = {
            node.id: raw_results[node.id] for node in workflow.nodes if node.id in raw_results
        }
        failed = any(item.status == "FAILED" for item in executions.values())
        return ordered_results, failed

    def _wave_limit(
        self, ready: list[WorkflowNode], incoming: Mapping[str, list[WorkflowEdge]],
        nodes: Mapping[str, WorkflowNode], executions: Mapping[str, _NodeExecution],
    ) -> int:
        limits = [self._max_concurrency]
        for node in ready:
            for edge in incoming[node.id]:
                source = nodes[edge.source]
                if (
                    source.type == WorkflowNodeType.PARALLEL
                    and self._edge_active(edge, executions[source.id])
                ):
                    limits.append(int(source.config.get("max_concurrency", 2)))
        return max(1, min(len(ready), *limits))

    @staticmethod
    def _edge_active(edge: WorkflowEdge, source: _NodeExecution) -> bool:
        if source.status == "SKIPPED":
            return False
        if edge.outcome == WorkflowEdgeOutcome.ALWAYS:
            return True
        if edge.outcome == WorkflowEdgeOutcome.SUCCESS:
            return source.status == "SUCCEEDED"
        if edge.outcome == WorkflowEdgeOutcome.FAILURE:
            return source.status == "FAILED"
        if edge.outcome == WorkflowEdgeOutcome.TRUE:
            return source.branch is True
        return source.branch is False

    def _execute_node(
        self, node: WorkflowNode, variables: Mapping[str, Any],
    ) -> tuple[_NodeExecution, dict[str, Any]]:
        started_at = _now()
        try:
            if node.type == WorkflowNodeType.HTTP:
                execution = self._execute_http(node.config, variables)
            elif node.type == WorkflowNodeType.CONDITION:
                execution = self._execute_condition(node.config, variables)
            elif node.type == WorkflowNodeType.PARALLEL:
                execution = _NodeExecution("SUCCEEDED", {"max_concurrency": int(
                    node.config.get("max_concurrency", 2)
                )}, {})
            else:
                raise WorkflowExecutionError("unsupported node type")
        except (KeyError, TypeError, ValueError, WorkflowExecutionError):
            execution = _NodeExecution(
                "FAILED", {"error": "node execution failed"}, {},
            )
        result = {
            "node_id": node.id, "name": node.name, "type": _node_type_value(node),
            "status": execution.status, "started_at": started_at,
            "finished_at": _now(), **execution.details,
        }
        return execution, result

    def _execute_condition(
        self, config: Mapping[str, Any], variables: Mapping[str, Any],
    ) -> _NodeExecution:
        operator = str(config["operator"])
        raw_left = config.get("left")
        if operator == "exists":
            token = _WHOLE_TEMPLATE.fullmatch(raw_left) if isinstance(raw_left, str) else None
            variable_name = token.group(1) if token else raw_left
            outcome = isinstance(variable_name, str) and variable_name in variables
            left = None
        else:
            left = render_templates(raw_left, variables)
            right = render_templates(config.get("right"), variables)
            outcome = self._compare(left, right, operator)
        return _NodeExecution("SUCCEEDED", {"outcome": outcome}, {}, outcome)

    @staticmethod
    def _compare(left: Any, right: Any, operator: str) -> bool:
        try:
            if operator == "equals":
                return left == right
            if operator == "not_equals":
                return left != right
            if operator == "contains":
                return right in left
            if operator == "gt":
                return left > right
            if operator == "gte":
                return left >= right
            if operator == "lt":
                return left < right
            if operator == "lte":
                return left <= right
        except (TypeError, ValueError):
            return False
        raise WorkflowExecutionError("unsupported condition operator")

    def _execute_http(
        self, config: Mapping[str, Any], variables: Mapping[str, Any],
    ) -> _NodeExecution:
        rendered = render_templates(config, variables)
        url = str(rendered["url"])
        parsed, addresses = self._validate_public_url(url)
        timeout = float(rendered.get("timeout_seconds", 30))
        if not 0 < timeout <= MAX_HTTP_TIMEOUT_SECONDS:
            raise WorkflowExecutionError("HTTP timeout is invalid")
        expected = {int(item) for item in rendered.get("expected_statuses", range(200, 300))}
        if not expected or any(item < 100 or item > 599 for item in expected):
            raise WorkflowExecutionError("expected HTTP statuses are invalid")
        response = None
        try:
            if self._request is None:
                response = self._request_pinned(parsed, addresses, rendered, timeout)
            else:
                response = self._request(
                    method=str(rendered.get("method", "GET")).upper(), url=url,
                    headers=dict(rendered.get("headers", {})),
                    params=dict(rendered.get("query", {})), json=rendered.get("body"),
                    timeout=timeout, allow_redirects=False, stream=True,
                )
            body = self._read_bounded_body(response)
            status_value = getattr(response, "status_code", None)
            if status_value is None:
                status_value = response.status
            status_code = int(status_value)
            extracted: dict[str, Any] = {}
            rules = dict(rendered.get("extractions", {}))
            if rules:
                payload = json.loads(body.decode(getattr(response, "encoding", None) or "utf-8"))
                for name, path in rules.items():
                    extracted[name] = extract_json_path(payload, path)
            status = "SUCCEEDED" if status_code in expected else "FAILED"
            return _NodeExecution(status, {
                "status_code": status_code, "response_size_bytes": len(body),
                "extractions": deepcopy(extracted),
            }, extracted)
        except (requests.RequestException, urllib3.exceptions.HTTPError,
                UnicodeError, json.JSONDecodeError,
                KeyError, IndexError, TypeError, ValueError):
            return _NodeExecution("FAILED", {"error": "HTTP request failed"}, {})
        finally:
            if response is not None and callable(getattr(response, "close", None)):
                response.close()

    @staticmethod
    def _read_bounded_body(response: Any) -> bytes:
        chunks: list[bytes] = []
        size = 0
        iterator = getattr(response, "iter_content", None)
        stream = getattr(response, "stream", None)
        if callable(iterator):
            values = iterator(chunk_size=64 * 1024)
        elif callable(stream):
            values = stream(64 * 1024)
        else:
            values = [response.content]
        for chunk in values:
            if not chunk:
                continue
            if not isinstance(chunk, bytes):
                chunk = bytes(chunk)
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise ValueError("HTTP response body is too large")
            chunks.append(chunk)
        return b"".join(chunks)

    def _request_pinned(
        self, parsed: Any, addresses: tuple[Any, ...], config: Mapping[str, Any],
        timeout: float,
    ) -> Any:
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        path = parsed.path or "/"
        query = urlencode(dict(config.get("query", {})), doseq=True)
        if parsed.query and query:
            query = f"{parsed.query}&{query}"
        elif parsed.query:
            query = parsed.query
        if query:
            path = f"{path}?{query}"
        headers = {str(key): str(value) for key, value in dict(
            config.get("headers", {})
        ).items()}
        if not any(key.lower() == "host" for key in headers):
            headers["Host"] = parsed.netloc
        body_value = config.get("body")
        body = None
        if body_value is not None:
            body = json.dumps(body_value, ensure_ascii=False, separators=(",", ":")).encode()
            if not any(key.lower() == "content-type" for key in headers):
                headers["Content-Type"] = "application/json"
        last_error: Exception | None = None
        for address in addresses:
            pool_type = HTTPSConnectionPool if parsed.scheme.lower() == "https" else HTTPConnectionPool
            options: dict[str, Any] = {"timeout": timeout, "maxsize": 1, "block": True}
            if pool_type is HTTPSConnectionPool:
                options.update({
                    "cert_reqs": "CERT_REQUIRED", "assert_hostname": parsed.hostname,
                    "server_hostname": parsed.hostname,
                })
            pool = pool_type(str(address), port=port, **options)
            try:
                return pool.urlopen(
                    str(config.get("method", "GET")).upper(), path, body=body,
                    headers=headers, redirect=False, retries=False, preload_content=False,
                )
            except urllib3.exceptions.HTTPError as exc:
                last_error = exc
                pool.close()
        raise urllib3.exceptions.HTTPError("all validated destination addresses failed") from last_error

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
            if not addresses or any(not address.is_global for address in addresses):
                raise ValueError
            return parsed, addresses
        except Exception:
            raise WorkflowExecutionError("HTTP destination is not allowed") from None


WorkflowExecutor = DataFactoryExecutor
DataFactoryRun = WorkflowRunRecord