import json
import re
from threading import Event
from uuid import UUID, uuid4

import pytest

from services.api.load_test_domain import LoadTestMode, LoadTestTrafficMode
from services.api.load_test_auth import LoadTestAuthConfiguration, LoadTestAuthMode
from services.api.load_test_executor import LoadTestExecutionError, LoadTestExecutor
from services.api.load_test_repository import LoadTestRepository


class Response:
    status_code = 200

    def __init__(self, content: bytes = b"{}", status_code: int = 200) -> None:
        self._content = content
        self.status_code = status_code

    @property
    def content(self) -> bytes:
        return self._content

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


def test_count_execution_is_bounded_and_samples_limited_bodies() -> None:
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
    assert len(metrics["response_samples"]["successful"]) == 5
    assert metrics["scenario_metrics"]["total_scenarios"] == 0
    assert all(call["allow_redirects"] is False and call["stream"] is True for call in calls)


def test_request_mode_renders_typed_dynamic_values_per_request() -> None:
    calls = []
    target = {
        "name": "dynamic", "method": "post",
        "url": "https://example.test/items/{{$sequence}}",
        "headers": {"X-Request-ID": "{{$uuid}}"},
        "query": {"page": "{{$randomInt(2,2)}}"},
        "body": {
            "requestId": "{{$uuid}}", "sequence": "{{$sequence}}",
            "timestamp": "{{$timestamp}}", "timestampMs": "{{$timestampMs}}",
            "code": "CODE-{{$randomDigits(6)}}", "name": "{{$randomString(8)}}",
            "phone": "{{$randomPhone}}", "email": "{{$randomEmail}}",
            "enabled": "{{$randomBoolean}}",
            "status": "{{$randomChoice(draft,paid,closed)}}",
        },
        "expected_statuses": [200],
    }

    metrics = LoadTestExecutor(
        request_callable=lambda **kwargs: calls.append(kwargs) or Response(),
        resolver=lambda _: ["93.184.216.34"],
    ).execute(definition(count=3, concurrency=1, targets=[target]))

    assert metrics["successful_requests"] == 3
    assert [call["url"].rsplit("/", 1)[1] for call in calls] == ["1", "2", "3"]
    assert [call["json"]["sequence"] for call in calls] == [1, 2, 3]
    assert all(call["params"]["page"] == 2 for call in calls)
    assert all(call["headers"]["X-Request-ID"] == call["json"]["requestId"] for call in calls)
    assert len({call["json"]["requestId"] for call in calls}) == 3
    assert all(UUID(call["json"]["requestId"]) for call in calls)
    assert all(re.fullmatch(r"CODE-[0-9]{6}", call["json"]["code"]) for call in calls)
    assert all(len(call["json"]["name"]) == 8 for call in calls)
    assert all(re.fullmatch(r"1[3-9][0-9]{9}", call["json"]["phone"]) for call in calls)
    assert all(call["json"]["email"].endswith("@example.com") for call in calls)
    assert all(isinstance(call["json"]["enabled"], bool) for call in calls)
    assert all(call["json"]["status"] in {"draft", "paid", "closed"} for call in calls)


def test_invalid_dynamic_template_is_counted_without_sending_request() -> None:
    calls = []
    target = {
        "name": "invalid", "method": "post", "url": "https://example.test/items",
        "headers": {}, "query": {}, "body": {"value": "{{$randomInt(10,1)}}"},
        "expected_statuses": [200],
    }

    metrics = LoadTestExecutor(
        request_callable=lambda **kwargs: calls.append(kwargs) or Response(),
        resolver=lambda _: ["93.184.216.34"],
    ).execute(definition(count=1, concurrency=1, targets=[target]))

    assert calls == []
    assert metrics["failed_requests"] == 1
    assert metrics["error_counts"] == {"template_error": 1}


def test_response_samples_include_failures_and_redact_sensitive_json() -> None:
    responses = [
        Response(b'{"data":{"id":1},"accessToken":"secret-value"}'),
        Response(b'{"message":"failed","password":"hidden"}', status_code=500),
    ]

    metrics = LoadTestExecutor(
        request_callable=lambda **_: responses.pop(0),
        resolver=lambda _: ["93.184.216.34"],
    ).execute(definition(count=2, concurrency=1))

    successful = metrics["response_samples"]["successful"][0]
    failed = metrics["response_samples"]["failed"][0]
    assert successful["status"] == 200
    assert successful["body"]["data"] == {"id": 1}
    assert successful["body"]["accessToken"] == "[REDACTED]"
    assert failed["status"] == 500 and failed["error"] == "unexpected_status"
    assert failed["body"]["message"] == "failed"
    assert failed["body"]["password"] == "[REDACTED]"


def test_response_samples_are_bounded_and_omit_unsafe_text() -> None:
    metrics = LoadTestExecutor(
        request_callable=lambda **_: Response(b"plain text response"),
        resolver=lambda _: ["93.184.216.34"],
    ).execute(definition(count=20, concurrency=1))

    samples = metrics["response_samples"]["successful"]
    assert len(samples) == 5
    assert all(sample["body"] is None and sample["body_kind"] == "non_json"
               for sample in samples)


def test_response_callback_receives_every_full_json_and_text_body() -> None:
    records = []
    large_value = "x" * 20_000
    responses = [
        Response(json.dumps({"value": large_value, "token": "hidden"}).encode()),
        Response(b"plain response text"),
    ]

    metrics = LoadTestExecutor(
        request_callable=lambda **_: responses.pop(0),
        resolver=lambda _: ["93.184.216.34"],
    ).execute(
        definition(count=2, concurrency=1), response_callback=records.append,
    )

    assert metrics["total_requests"] == len(records) == 2
    assert [item["sequence"] for item in records] == [1, 2]
    assert large_value in records[0]["body"]
    assert "hidden" not in records[0]["body"]
    assert records[0]["body_kind"] == "json" and not records[0]["truncated"]
    assert records[1]["body"] == "plain response text"
    assert records[1]["body_kind"] == "text"


def test_response_callback_marks_body_over_one_mib_as_truncated() -> None:
    records = []
    LoadTestExecutor(
        request_callable=lambda **_: Response(b"x" * (1_048_576 + 10)),
        resolver=lambda _: ["93.184.216.34"],
    ).execute(
        definition(count=1, concurrency=1), response_callback=records.append,
    )

    assert len(records[0]["body"]) == 1_048_576
    assert records[0]["truncated"] is True
    assert records[0]["body_bytes"] == 1_048_586


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
        "query": {}, "body": {
            "enabled": "{{enabled}}", "sequence": "{{$sequence}}",
        },
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
    assert calls[0]["json"] == {"enabled": True, "sequence": 1}
    assert calls[2]["json"] == {"enabled": True, "sequence": 2}
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
            assert kwargs["json"] == {
                "tenantName": "demo", "username": account, "passcode": password,
            }
            return LoginResponse(json.dumps({"data": {"access": token}}).encode())
        return Response()

    config = LoadTestAuthConfiguration(
        base_url="https://example.test/api", mode=LoadTestAuthMode.LOGIN,
        login_url="/session", account_field="username", password_field="passcode",
        login_body={"tenantName": "demo", "username": "cannot-override"},
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


@pytest.mark.parametrize(("response", "code"), [
    (LoginResponse(b"not-json"), "login_response_invalid"),
    (LoginResponse(b'{"data":{}}'), "login_token_path_missing"),
    (LoginResponse(b'{"data":{"token":123}}'), "login_token_invalid"),
])
def test_login_reports_safe_failure_category(response, code: str) -> None:
    config = LoadTestAuthConfiguration(
        base_url="https://example.test", mode=LoadTestAuthMode.LOGIN,
        login_url="/session", account="account", password="password",
    )

    with pytest.raises(LoadTestExecutionError) as captured:
        LoadTestExecutor(
            request_callable=lambda **_: response,
            resolver=lambda _: ["93.184.216.34"],
        ).test_authentication(config)

    assert captured.value.code == code
    assert "account" not in str(captured.value)
    assert "password" not in str(captured.value)


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


def test_explicit_private_hostname_allowlist_remains_pinned_and_bounded() -> None:
    calls = []
    config = LoadTestAuthConfiguration(
        base_url="http://erp.internal:8090", mode=LoadTestAuthMode.LOGIN,
        login_url="/session", account="account", password="password",
        token_path="result", auth_prefix="",
    )

    executor = LoadTestExecutor(
        request_callable=lambda **kwargs: calls.append(kwargs)
        or LoginResponse(b'{"result":"token"}'),
        resolver=lambda _: ["192.168.7.44"],
        allowed_private_hosts=("erp.internal",),
    )

    assert executor.test_authentication(config) is LoadTestAuthMode.LOGIN
    assert len(calls) == 1
    with pytest.raises(LoadTestExecutionError):
        LoadTestExecutor(
            request_callable=lambda **_: LoginResponse(b'{"result":"token"}'),
            resolver=lambda _: ["192.168.7.44"],
        ).test_authentication(config)


@pytest.mark.parametrize("address", ["127.0.0.1", "169.254.1.1", "224.0.0.1"])
def test_private_hostname_allowlist_never_allows_dangerous_addresses(address: str) -> None:
    config = LoadTestAuthConfiguration(
        base_url="http://erp.internal", mode=LoadTestAuthMode.LOGIN,
        login_url="/session", account="account", password="password",
    )
    with pytest.raises(LoadTestExecutionError):
        LoadTestExecutor(
            request_callable=lambda **_: LoginResponse(b'{"data":{"token":"token"}}'),
            resolver=lambda _: [address], allowed_private_hosts=("erp.internal",),
        ).test_authentication(config)


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