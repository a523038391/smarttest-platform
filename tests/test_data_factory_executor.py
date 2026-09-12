import json
from threading import Barrier, Lock

import pytest
from pydantic import ValidationError

from services.api.data_factory_executor import DataFactoryExecutor, WorkflowExecutionError
from services.api.data_factory_schemas import WorkflowDefinitionSchema


PUBLIC_RESOLVER = lambda _: ["8.8.8.8"]


class StubResponse:
    def __init__(self, payload, status_code: int = 200) -> None:
        self.status_code = status_code
        self.encoding = "utf-8"
        self.content = json.dumps(payload).encode()
        self.closed = False

    def iter_content(self, chunk_size: int):
        yield from (
            self.content[index:index + chunk_size]
            for index in range(0, len(self.content), chunk_size)
        )

    def close(self) -> None:
        self.closed = True


def node(node_id: str, kind: str, config: dict) -> dict:
    return {"id": node_id, "name": node_id, "type": kind, "x": 0, "y": 0, "config": config}


def edge(edge_id: str, source: str, target: str, outcome: str = "always") -> dict:
    return {"id": edge_id, "source": source, "target": target, "outcome": outcome}


def definition(nodes: list[dict], edges: list[dict] | None = None, variables=None):
    schema = WorkflowDefinitionSchema.model_validate({
        "nodes": nodes, "edges": edges or [], "variables": variables or {},
    })
    return schema.to_domain("workflow-1")


def test_linear_substitution_and_extraction_preserve_json_types() -> None:
    calls = []

    def request(**kwargs):
        calls.append(kwargs)
        if kwargs["url"].endswith("/start"):
            return StubResponse({"data": {"user": {"id": 7}, "user_id": 42}})
        assert kwargs["url"].endswith("/users/42")
        assert kwargs["json"] == {"payload": {"id": 7}}
        return StubResponse({"ok": True})

    workflow = definition([
        node("first", "http", {
            "method": "GET", "url": "https://api.example/start",
            "extractions": {"user": "$.data.user", "user_id": "$.data.user_id"},
        }),
        node("second", "http", {
            "method": "POST", "url": "https://api.example/users/{{user_id}}",
            "body": {"payload": "{{user}}"}, "extractions": {},
        }),
    ], [edge("e1", "first", "second", "success")])

    run = DataFactoryExecutor(request, PUBLIC_RESOLVER).execute(workflow)

    assert run.status == "SUCCEEDED"
    assert run.variables["user"] == {"id": 7}
    assert [item["status"] for item in run.node_results.values()] == ["SUCCEEDED"] * 2
    assert len(calls) == 2


@pytest.mark.parametrize(
    ("flag", "executed", "skipped"), [(True, "yes", "no"), (False, "no", "yes")],
)
def test_condition_activates_only_selected_branch_and_resolves_join(
    flag: bool, executed: str, skipped: str,
) -> None:
    workflow = definition([
        node("condition", "condition", {
            "left": "{{flag}}", "operator": "equals", "right": True,
        }),
        node("yes", "parallel", {"max_concurrency": 2}),
        node("no", "parallel", {"max_concurrency": 2}),
        node("join", "parallel", {"max_concurrency": 2}),
    ], [
        edge("true", "condition", "yes", "true"),
        edge("false", "condition", "no", "false"),
        edge("yes-join", "yes", "join"), edge("no-join", "no", "join"),
    ], {"flag": flag})

    run = DataFactoryExecutor().execute(workflow)

    assert run.status == "SUCCEEDED"
    assert run.node_results[executed]["status"] == "SUCCEEDED"
    assert run.node_results[skipped]["status"] == "SKIPPED"
    assert run.node_results["join"]["status"] == "SUCCEEDED"


def test_parallel_fan_out_is_actually_concurrent() -> None:
    barrier = Barrier(2, timeout=2)
    lock = Lock()
    active = 0
    maximum_active = 0

    def request(**_):
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        barrier.wait()
        with lock:
            active -= 1
        return StubResponse({"ok": True})

    workflow = definition([
        node("fanout", "parallel", {"max_concurrency": 2}),
        node("left", "http", {"method": "GET", "url": "https://left.example"}),
        node("right", "http", {"method": "GET", "url": "https://right.example"}),
    ], [edge("left-edge", "fanout", "left"), edge("right-edge", "fanout", "right")])

    run = DataFactoryExecutor(request, PUBLIC_RESOLVER).execute(workflow)

    assert run.status == "SUCCEEDED"
    assert maximum_active == 2


def test_graph_validation_and_empty_execution_rules() -> None:
    empty = definition([])
    with pytest.raises(WorkflowExecutionError, match="empty"):
        DataFactoryExecutor().execute(empty)

    duplicate = [
        node("same", "parallel", {}), node("same", "parallel", {}),
    ]
    with pytest.raises(ValidationError, match="duplicate node id"):
        definition(duplicate)

    cyclic = [node("a", "parallel", {}), node("b", "parallel", {})]
    with pytest.raises(ValidationError, match="root node|DAG"):
        definition(cyclic, [edge("ab", "a", "b"), edge("ba", "b", "a")])

    with pytest.raises(ValidationError, match="incompatible"):
        definition(cyclic, [edge("bad", "a", "b", "true")])


def test_private_address_is_rejected_without_sending_request() -> None:
    calls = []
    workflow = definition([
        node("private", "http", {
            "method": "GET", "url": "http://127.0.0.1/private-secret",
        }),
    ])

    run = DataFactoryExecutor(lambda **kwargs: calls.append(kwargs)).execute(workflow)

    assert run.status == "FAILED"
    assert calls == []
    assert "private-secret" not in json.dumps(run.as_dict())


def test_default_https_transport_pins_validated_ip_and_preserves_hostname(
    monkeypatch,
) -> None:
    connection = {}

    class PinnedResponse:
        status = 200
        encoding = "utf-8"

        @staticmethod
        def stream(_: int):
            yield b'{"ok":true}'

        @staticmethod
        def close() -> None:
            pass

    class PinnedPool:
        def __init__(self, host, port, **options):
            connection.update(host=host, port=port, options=options)

        def urlopen(self, method, path, **options):
            connection.update(method=method, path=path, request=options)
            return PinnedResponse()

    monkeypatch.setattr(
        "services.api.data_factory_executor.HTTPSConnectionPool", PinnedPool,
    )
    workflow = definition([
        node("public", "http", {
            "method": "GET", "url": "https://api.example/items", "query": {"page": 2},
        }),
    ])

    run = DataFactoryExecutor(resolver=PUBLIC_RESOLVER).execute(workflow)

    assert run.status == "SUCCEEDED"
    assert connection["host"] == "8.8.8.8"
    assert connection["options"]["server_hostname"] == "api.example"
    assert connection["request"]["headers"]["Host"] == "api.example"
    assert connection["path"] == "/items?page=2"


def test_debug_executes_exactly_one_node_and_returns_run_shape() -> None:
    workflow = definition([
        node("selected", "condition", {
            "left": "{{count}}", "operator": "gte", "right": 2,
        }),
        node("other", "parallel", {"max_concurrency": 2}),
    ], variables={"count": 3})

    run = DataFactoryExecutor().debug(workflow, "selected")

    assert run.status == "SUCCEEDED"
    assert set(run.node_results) == {"selected"}
    assert run.node_results["selected"]["outcome"] is True
    assert run.workflow_id == "workflow-1"
    assert run.started_at and run.finished_at