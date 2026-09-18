import json
from threading import Event
from uuid import uuid4

import pytest

from services.api.load_test_domain import LoadTestMode, LoadTestTrafficMode
from services.api.load_test_auth import LoadTestAuthConfiguration, LoadTestAuthMode
from services.api.load_test_executor import LoadTestExecutionError, LoadTestExecutor
from services.api.load_test_repository import LoadTestRepository


class Response:
    status_code = 200

    @property
    def content(self):
        raise AssertionError("load tests must not retain response bodies")

    def close(self) -> None:
        pass


class LoginResponse(Response):
    def __init__(self, content: bytes) -> None:
        self._content = content

    @property
    def content(self) -> bytes:
        return self._content


class JsonResponse(Response):
    def __init__(self, payload, status_code=200) -> None:
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload


def definition(*, mode=LoadTestMode.COUNT, count=5, duration=1, concurrency=2,
               traffic_mode=LoadTestTrafficMode.REQUESTS, targets=None,
               initial_variables=None, stop_on_failure=True):
    return LoadTestRepository().create_load_test(
        __import__("uuid").uuid4(), "Load", "", targets or [{
            "name": "health", "method": "get", "url": "https://example.test/health",
            "headers": {}, "query": {}, "body": None, "expected_statuses": [200],
        }], mode, count, duration, concurrency, 0, 5,
        traffic_mode=traffic_mode, initial_variables=initial_variables,
        stop_on_failure=stop_on_failure,
    )


def test_count_execution_is_bounded_and_does_not_read_bodies() -> None:
    calls = []

    def request(**kwargs):
        calls.append(kwargs)
        return Response()

    metrics = LoadTestExecutor(
        request_callable=request, resolver=lambda _: ["93.184.216.34"], max_workers=2,
    ).execute(definition(count=5, concurrency=8))

    assert len(calls) == metrics["total_requests"] == 5
    assert metrics["successful_requests"] == 5
    assert metrics["status_counts"] == {"200": 5}
    assert metrics["target_metrics"]["health"]["total_requests"] == 5
    assert metrics["scenario_metrics"]["total_scenarios"] == 0
    assert all(call["allow_redirects"] is False and call["stream"] is True for call in calls)


def test_duration_execution_observes_cancellation() -> None:
    cancellation = Event()
    calls = 0

    def request(**_):
        nonlocal calls
        calls += 1
        if calls == 3:
            cancellation.set()
        return Response()

    metrics = LoadTestExecutor(
        request_callable=request, resolver=lambda _: ["93.184.216.34"]
    ).execute(
        definition(mode=LoadTestMode.DURATION, duration=30, concurrency=1),
        cancel_event=cancellation,
    )
    assert metrics["total_requests"] == 3


@pytest.mark.parametrize("url", ["http://127.0.0.1", "http://169.254.169.254/latest"])
def test_private_destinations_are_rejected_before_request(url: str) -> None:
    item = definition()
    item.targets[0]["url"] = url
    with pytest.raises(LoadTestExecutionError, match="not allowed"):
        LoadTestExecutor(request_callable=lambda **_: Response()).execute(item)


def test_hostname_is_rejected_if_any_dns_answer_is_private() -> None:
    with pytest.raises(LoadTestExecutionError):
        LoadTestExecutor(
            request_callable=lambda **_: Response(),
            resolver=lambda _: ["93.184.216.34", "10.0.0.1"],
        ).execute(definition())


@pytest.mark.parametrize("address", [
    "0.0.0.1", "100.64.0.1", "192.0.0.1", "::1", "::ffff:127.0.0.1",
])
def test_special_use_addresses_are_rejected(address: str) -> None:
    with pytest.raises(LoadTestExecutionError):
        LoadTestExecutor(
            request_callable=lambda **_: Response(), resolver=lambda _: [address],
        ).execute(definition())


def test_multiple_targets_are_dispatched_and_aggregated() -> None:
    item = definition(count=4, concurrency=1)
    item.targets.append({
        **item.targets[0], "name": "items", "url": "https://example.test/items",
    })
    calls = []

    metrics = LoadTestExecutor(
        request_callable=lambda **kwargs: calls.append(kwargs["url"]) or Response(),
        resolver=lambda _: ["93.184.216.34"],
    ).execute(item)

    assert calls == [
        "https://example.test/health", "https://example.test/items",
        "https://example.test/health", "https://example.test/items",
    ]
    assert metrics["target_metrics"]["health"]["total_requests"] == 2
    assert metrics["target_metrics"]["items"]["total_requests"] == 2


def test_scenario_executes_ordered_rounds_with_typed_templates_and_extraction() -> None:
    targets = [{
        "name": "create", "method": "POST",
        "url": "https://example.test/users/{{seed}}", "headers": {},
        "query": {}, "body": {"enabled": "{{enabled}}"},
        "expected_statuses": [200], "extractors": {"item_id": "data.items[0].id"},
    }, {
        "name": "read", "method": "POST",
        "url": "https://example.test/items/{{item_id}}",
        "headers": {"X-Item": "{{item_id}}"}, "query": {"filter": "{{filter}}"},
        "body": {"id": "{{item_id}}"}, "expected_statuses": [200],
    }]
    calls, resolutions = [], []

    def request(**kwargs):
        calls.append(kwargs)
        if "/users/" in kwargs["url"]:
            return JsonResponse({"data": {"items": [{"id": 42}]}})
        return Response()

    metrics = LoadTestExecutor(
        request_callable=request,
        resolver=lambda hostname: resolutions.append(hostname) or ["93.184.216.34"],
    ).execute(definition(
        count=2, concurrency=1, traffic_mode=LoadTestTrafficMode.SCENARIO,
        targets=targets,
        initial_variables={"seed": "alpha", "enabled": True, "filter": {"active": True}},
    ))

    assert [call["url"] for call in calls] == [
        "https://example.test/users/alpha", "https://example.test/items/42",
        "https://example.test/users/alpha", "https://example.test/items/42",
    ]
    assert calls[0]["json"] == {"enabled": True}
    assert calls[1]["json"] == {"id": 42}
    assert calls[1]["params"] == {"filter": {"active": True}}
    assert calls[1]["headers"] == {"X-Item": "42"}
    assert len(resolutions) == 4
    assert metrics["total_requests"] == 4
    assert metrics["scenario_metrics"]["total_scenarios"] == 2
    assert metrics["scenario_metrics"]["successful_scenarios"] == 2


@pytest.mark.parametrize("stop_on_failure, expected_calls", [(True, 1), (False, 2)])
def test_scenario_failure_policy_controls_remaining_steps(
    stop_on_failure: bool, expected_calls: int,
) -> None:
    targets = [{
        "name": "fail", "method": "GET", "url": "https://example.test/fail",
        "headers": {}, "query": {}, "body": None, "expected_statuses": [200],
    }, {
        "name": "after", "method": "GET", "url": "https://example.test/after",
        "headers": {}, "query": {}, "body": None, "expected_statuses": [200],
    }]
    calls = []

    def request(**kwargs):
        calls.append(kwargs["url"])
        return JsonResponse({}, status_code=500 if kwargs["url"].endswith("/fail") else 200)

    metrics = LoadTestExecutor(
        request_callable=request, resolver=lambda _: ["93.184.216.34"],
    ).execute(definition(
        count=1, concurrency=1, traffic_mode=LoadTestTrafficMode.SCENARIO,
        targets=targets, stop_on_failure=stop_on_failure,
    ))

    assert len(calls) == expected_calls
    assert metrics["scenario_metrics"]["failed_scenarios"] == 1


def test_scenario_extractor_rejects_response_over_one_mib() -> None:
    target = {
        "name": "large", "method": "GET", "url": "https://example.test/large",
        "headers": {}, "query": {}, "body": None, "expected_statuses": [200],
        "extractors": {"value": "value"},
    }
    metrics = LoadTestExecutor(
        request_callable=lambda **_: JsonResponse({"value": "x" * 1_048_576}),
        resolver=lambda _: ["93.184.216.34"],
    ).execute(definition(
        count=1, concurrency=1, traffic_mode=LoadTestTrafficMode.SCENARIO,
        targets=[target],
    ))

    assert metrics["failed_requests"] == 1
    assert metrics["error_counts"] == {"extractor_error": 1}
    assert metrics["scenario_metrics"]["failed_scenarios"] == 1


def test_scenario_missing_template_is_reported_without_a_request() -> None:
    target = {
        "name": "missing", "method": "GET",
        "url": "https://example.test/{{missing}}", "headers": {},
        "query": {}, "body": None, "expected_statuses": [200],
    }
    calls = []
    metrics = LoadTestExecutor(
        request_callable=lambda **kwargs: calls.append(kwargs) or Response(),
        resolver=lambda _: ["93.184.216.34"],
    ).execute(definition(
        count=1, concurrency=1, traffic_mode=LoadTestTrafficMode.SCENARIO,
        targets=[target],
    ))

    assert calls == []
    assert metrics["error_counts"] == {"template_error": 1}
    assert metrics["scenario_metrics"]["failed_scenarios"] == 1


def test_scenario_concurrency_does_not_exceed_count_and_isolates_variables() -> None:
    targets = [{
        "name": "seed", "method": "GET", "url": "https://example.test/seed",
        "headers": {}, "query": {}, "body": None, "expected_statuses": [200],
        "extractors": {"item_id": "id"},
    }, {
        "name": "read", "method": "GET",
        "url": "https://example.test/items/{{item_id}}", "headers": {},
        "query": {}, "body": None, "expected_statuses": [200],
    }]
    lock = __import__("threading").Lock()
    next_id = 0
    calls = []

    def request(**kwargs):
        nonlocal next_id
        with lock:
            calls.append(kwargs["url"])
            if kwargs["url"].endswith("/seed"):
                next_id += 1
                return JsonResponse({"id": next_id})
        return Response()

    metrics = LoadTestExecutor(
        request_callable=request, resolver=lambda _: ["93.184.216.34"],
    ).execute(definition(
        count=7, concurrency=5, traffic_mode=LoadTestTrafficMode.SCENARIO,
        targets=targets,
    ))

    assert metrics["scenario_metrics"]["total_scenarios"] == 7
    assert metrics["total_requests"] == 14
    assert len([url for url in calls if url.endswith("/seed")]) == 7
    assert len([url for url in calls if "/items/" in url]) == 7


def test_request_errors_are_counted() -> None:
    metrics = LoadTestExecutor(
        request_callable=lambda **_: (_ for _ in ()).throw(OSError("offline")),
        resolver=lambda _: ["93.184.216.34"],
    ).execute(definition(count=1, concurrency=1))
    assert metrics["failed_requests"] == 1
    assert metrics["error_counts"] == {"request_error": 1}


def test_relative_url_and_direct_token_are_injected() -> None:
    item, calls, token = definition(count=2, concurrency=1), [], uuid4().hex
    item.targets[0]["url"] = "health"
    config = LoadTestAuthConfiguration(
        base_url="https://example.test/api", mode=LoadTestAuthMode.TOKEN,
        auth_header="X-Token", auth_prefix="Token", token=token,
    )

    LoadTestExecutor(
        request_callable=lambda **kwargs: calls.append(kwargs) or Response(),
        resolver=lambda _: ["93.184.216.34"],
    ).execute(item, auth_config=config)

    assert [call["url"] for call in calls] == [
        "https://example.test/api/health", "https://example.test/api/health",
    ]
    assert all(call["headers"]["X-Token"] == f"Token {token}" for call in calls)
    assert token not in repr(config)


def test_login_runs_once_and_injects_extracted_token() -> None:
    item, calls = definition(count=3, concurrency=2), []
    item.targets[0]["url"] = "/items"
    account, password, token = uuid4().hex, uuid4().hex, uuid4().hex

    def request(**kwargs):
        calls.append(kwargs)
        if kwargs["url"].endswith("/session"):
            assert kwargs["json"] == {"username": account, "passcode": password}
            return LoginResponse(json.dumps({"data": {"access": token}}).encode())
        return Response()

    config = LoadTestAuthConfiguration(
        base_url="https://example.test/api", mode=LoadTestAuthMode.LOGIN,
        login_url="/session", account_field="username", password_field="passcode",
        token_path="data.access", account=account, password=password,
    )
    LoadTestExecutor(
        request_callable=request, resolver=lambda _: ["93.184.216.34"],
    ).execute(item, auth_config=config)

    login_calls = [call for call in calls if call["url"].endswith("/session")]
    target_calls = [call for call in calls if call["url"].endswith("/items")]
    assert len(login_calls) == 1
    assert len(target_calls) == 3
    assert all(call["headers"]["Authorization"] == f"Bearer {token}"
               for call in target_calls)


def test_login_ssrf_failure_does_not_echo_credentials() -> None:
    account, password = uuid4().hex, uuid4().hex
    config = LoadTestAuthConfiguration(
        mode=LoadTestAuthMode.LOGIN, login_url="http://127.0.0.1/session",
        account=account, password=password,
    )
    with pytest.raises(LoadTestExecutionError) as captured:
        LoadTestExecutor(
            request_callable=lambda **_: LoginResponse(b"{}"),
            resolver=lambda _: ["93.184.216.34"],
        ).execute(definition(count=1), auth_config=config)
    assert account not in str(captured.value)
    assert password not in str(captured.value)


@pytest.mark.parametrize("headers", [
    {"X-Test\r\nInjected": "value"},
    {"X-Test": "value\r\nInjected: yes"},
])
def test_request_header_injection_is_rejected(headers: dict[str, str]) -> None:
    item = definition(count=1)
    item.targets[0]["headers"] = headers
    with pytest.raises(LoadTestExecutionError, match="header is not allowed"):
        LoadTestExecutor(
            request_callable=lambda **_: Response(),
            resolver=lambda _: ["93.184.216.34"],
        ).execute(item)


def test_token_header_injection_is_rejected_without_echoing_token() -> None:
    token = f"safe{uuid4().hex}\r\nInjected: yes"
    config = LoadTestAuthConfiguration(
        mode=LoadTestAuthMode.TOKEN, token=token,
    )
    with pytest.raises(LoadTestExecutionError) as captured:
        LoadTestExecutor(
            request_callable=lambda **_: Response(),
            resolver=lambda _: ["93.184.216.34"],
        ).execute(definition(count=1), auth_config=config)
    assert token not in str(captured.value)