import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from services.api.ai_cases import AiCaseGenerator, CaseGenerationError
from services.api.quality_domain import RequirementStatus
from services.api.app import create_app
from services.api.quality_repository import QualityRepository


class StubProvider:
    def __init__(self, response: str | Exception) -> None:
        self.response = response
        self.requests = []

    def generate(self, requirement, count: int) -> str:
        self.requests.append((requirement, count))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def valid_response() -> str:
    return json.dumps({"cases": [{
        "title": "Valid login",
        "description": "Happy path",
        "preconditions": "Account exists",
        "priority": "HIGH",
        "steps": [{
            "order": 1,
            "action": "Submit valid credentials",
            "expected_result": "Dashboard is displayed",
        }],
    }]})


def test_generator_validates_structured_provider_output() -> None:
    repository = QualityRepository()
    requirement = repository.create_requirement(uuid4(), "Login", "Users sign in")
    provider = StubProvider(valid_response())

    candidates = AiCaseGenerator(provider).generate(requirement, 3)

    assert candidates[0].title == "Valid login"
    assert candidates[0].steps[0].order == 1
    assert provider.requests == [(requirement, 3)]


def test_generator_rejects_invalid_count_before_calling_provider() -> None:
    requirement = QualityRepository().create_requirement(uuid4(), "Login", "")
    provider = StubProvider(valid_response())

    with pytest.raises(ValueError, match="count must be between"):
        AiCaseGenerator(provider).generate(requirement, 21)

    assert provider.requests == []


@pytest.mark.parametrize("response", [
    "not-json",
    json.dumps({"cases": [{"title": "Incomplete"}]}),
    json.dumps({"cases": [], "secret": "must-not-be-accepted"}),
])
def test_generator_rejects_invalid_candidates(response: str) -> None:
    requirement = QualityRepository().create_requirement(uuid4(), "Login", "")
    with pytest.raises(CaseGenerationError):
        AiCaseGenerator(StubProvider(response)).generate(requirement, 1)


def test_ai_generation_api_and_sanitized_failures() -> None:
    repository = QualityRepository()
    requirement = repository.create_requirement(uuid4(), "Login", "")
    success = TestClient(create_app(
        quality_repository=repository,
        case_generator=AiCaseGenerator(StubProvider(valid_response())),
    )).post("/api/v1/ai/case-generations", json={
        "requirement_id": str(requirement.id), "count": 2,
    })
    unavailable = TestClient(create_app(
        quality_repository=repository
    )).post("/api/v1/ai/case-generations", json={
        "requirement_id": str(requirement.id), "count": 2,
    })
    failed = TestClient(create_app(
        quality_repository=repository,
        case_generator=AiCaseGenerator(StubProvider(RuntimeError("token-marker"))),
    )).post("/api/v1/ai/case-generations", json={
        "requirement_id": str(requirement.id), "count": 2,
    })

    assert success.status_code == 200
    assert success.json()["candidates"][0]["priority"] == "HIGH"
    assert unavailable.status_code == 503
    assert failed.status_code == 502
    assert "token-marker" not in failed.text


def test_ai_generation_rejects_archived_requirement() -> None:
    repository = QualityRepository()
    requirement = repository.create_requirement(uuid4(), "Login", "")
    active = repository.update_requirement(
        requirement.id, "Login", "", RequirementStatus.ACTIVE, 0
    )
    repository.update_requirement(
        requirement.id, "Login", "", RequirementStatus.ARCHIVED,
        active.state_version,
    )
    provider = StubProvider(valid_response())
    client = TestClient(create_app(
        quality_repository=repository,
        case_generator=AiCaseGenerator(provider),
    ))

    response = client.post("/api/v1/ai/case-generations", json={
        "requirement_id": str(requirement.id), "count": 2,
    })

    assert response.status_code == 409
    assert response.json()["code"] == "invalid_asset_transition"
    assert provider.requests == []


def test_ai_generation_returns_not_found_for_unknown_requirement() -> None:
    response = TestClient(create_app()).post(
        "/api/v1/ai/case-generations",
        json={"requirement_id": str(uuid4()), "count": 2},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "requirement_not_found"