"""Strict API schemas for the frontend Data Factory contract."""
from datetime import datetime
import json
import math
import re
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from .data_factory_domain import (
    IDENTIFIER_PATTERN, MAX_WORKFLOW_EDGES, MAX_WORKFLOW_NODES,
    WorkflowDefinition, WorkflowEdge, WorkflowEdgeOutcome, WorkflowNode,
    WorkflowNodeType, validate_workflow_graph,
)
from .schemas import ApiModel


MAX_JSON_BYTES = 1024 * 1024
MAX_JSON_DEPTH = 32
MAX_OBJECT_ITEMS = 1_000
JSON_PATH_PATTERN = re.compile(
    r"^\$(?:\.[A-Za-z_][A-Za-z0-9_-]*(?:\[[0-9]+\])?)*$"
)
VARIABLE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"})
CONDITION_OPERATORS = frozenset({
    "equals", "not_equals", "contains", "exists", "gt", "gte", "lt", "lte",
})


def _validate_json(value: Any, *, depth: int = 0) -> Any:
    if depth > MAX_JSON_DEPTH:
        raise ValueError("JSON value is too deeply nested")
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON numbers must be finite")
        return value
    if isinstance(value, list):
        if len(value) > MAX_OBJECT_ITEMS:
            raise ValueError("JSON array has too many items")
        return [_validate_json(item, depth=depth + 1) for item in value]
    if isinstance(value, dict):
        if len(value) > MAX_OBJECT_ITEMS or not all(isinstance(key, str) for key in value):
            raise ValueError("JSON object is invalid or too large")
        return {key: _validate_json(item, depth=depth + 1) for key, item in value.items()}
    raise ValueError("value must be valid JSON")


def _bounded_json(value: Any) -> Any:
    validated = _validate_json(value)
    encoded = json.dumps(validated, ensure_ascii=False, separators=(",", ":")).encode()
    if len(encoded) > MAX_JSON_BYTES:
        raise ValueError("JSON value cannot exceed 1 MiB")
    return validated


class HttpNodeConfig(ApiModel):
    method: str = "GET"
    url: str = Field(min_length=1, max_length=8_192)
    headers: dict[str, Any] = Field(default_factory=dict, max_length=100)
    query: dict[str, Any] = Field(default_factory=dict, max_length=100)
    body: Any = None
    extractions: dict[str, str] = Field(default_factory=dict, max_length=100)
    timeout_seconds: float = Field(default=30, gt=0, le=120)
    expected_statuses: list[int] = Field(
        default_factory=lambda: list(range(200, 300)), min_length=1, max_length=500,
    )

    @field_validator("method")
    @classmethod
    def validate_method(cls, value: str) -> str:
        value = value.upper()
        if value not in HTTP_METHODS:
            raise ValueError("unsupported HTTP method")
        return value

    @field_validator("headers", "query", "body")
    @classmethod
    def validate_json_fields(cls, value: Any) -> Any:
        return _bounded_json(value)

    @field_validator("extractions")
    @classmethod
    def validate_extractions(cls, value: dict[str, str]) -> dict[str, str]:
        for name, path in value.items():
            if not VARIABLE_PATTERN.fullmatch(name):
                raise ValueError("extraction variable name is invalid")
            if len(path) > 1_024 or not JSON_PATH_PATTERN.fullmatch(path):
                raise ValueError("only simple JSON paths are supported")
        return value

    @field_validator("expected_statuses")
    @classmethod
    def validate_statuses(cls, value: list[int]) -> list[int]:
        if any(code < 100 or code > 599 for code in value) or len(set(value)) != len(value):
            raise ValueError("expected_statuses must contain unique HTTP status codes")
        return value


class ConditionNodeConfig(ApiModel):
    left: str = Field(max_length=8_192)
    operator: Literal[
        "equals", "not_equals", "contains", "exists", "gt", "gte", "lt", "lte"
    ]
    right: Any = None

    @field_validator("left", "right")
    @classmethod
    def validate_values(cls, value: Any) -> Any:
        return _bounded_json(value)


class ParallelNodeConfig(ApiModel):
    max_concurrency: int = Field(default=2, ge=1, le=100)


class WorkflowNodeSchema(ApiModel):
    id: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    type: WorkflowNodeType
    x: float = Field(ge=-1_000_000, le=1_000_000)
    y: float = Field(ge=-1_000_000, le=1_000_000)
    config: dict[str, Any]

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not IDENTIFIER_PATTERN.fullmatch(value):
            raise ValueError("node id is invalid")
        return value

    @field_validator("x", "y")
    @classmethod
    def validate_coordinate(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("node coordinates must be finite")
        return value

    @model_validator(mode="after")
    def validate_config(self) -> "WorkflowNodeSchema":
        model = {
            WorkflowNodeType.HTTP: HttpNodeConfig,
            WorkflowNodeType.CONDITION: ConditionNodeConfig,
            WorkflowNodeType.PARALLEL: ParallelNodeConfig,
        }[self.type].model_validate(self.config)
        self.config = model.model_dump(mode="python")
        return self

    def to_domain(self) -> WorkflowNode:
        return WorkflowNode(self.id, self.name, self.type, self.x, self.y, self.config)


class WorkflowEdgeSchema(ApiModel):
    id: str = Field(min_length=1, max_length=255)
    source: str = Field(min_length=1, max_length=255)
    target: str = Field(min_length=1, max_length=255)
    outcome: WorkflowEdgeOutcome

    @field_validator("id", "source", "target")
    @classmethod
    def validate_ids(cls, value: str) -> str:
        if not IDENTIFIER_PATTERN.fullmatch(value):
            raise ValueError("edge identifier is invalid")
        return value

    def to_domain(self) -> WorkflowEdge:
        return WorkflowEdge(self.id, self.source, self.target, self.outcome)


class WorkflowDefinitionSchema(ApiModel):
    nodes: list[WorkflowNodeSchema] = Field(default_factory=list, max_length=MAX_WORKFLOW_NODES)
    edges: list[WorkflowEdgeSchema] = Field(default_factory=list, max_length=MAX_WORKFLOW_EDGES)
    variables: dict[str, Any] = Field(default_factory=dict, max_length=MAX_OBJECT_ITEMS)

    @field_validator("variables")
    @classmethod
    def validate_variables(cls, value: dict[str, Any]) -> dict[str, Any]:
        if any(not VARIABLE_PATTERN.fullmatch(name) for name in value):
            raise ValueError("variable name is invalid")
        return _bounded_json(value)

    @model_validator(mode="after")
    def validate_graph(self) -> "WorkflowDefinitionSchema":
        validate_workflow_graph(self.to_domain())
        return self

    def to_domain(self, workflow_id: str = "debug") -> WorkflowDefinition:
        return WorkflowDefinition(
            tuple(node.to_domain() for node in self.nodes),
            tuple(edge.to_domain() for edge in self.edges),
            self.variables,
            workflow_id,
        )


class WorkflowExecutionSchema(ApiModel):
    variables: dict[str, Any] = Field(default_factory=dict, max_length=MAX_OBJECT_ITEMS)

    @field_validator("variables")
    @classmethod
    def validate_variables(cls, value: dict[str, Any]) -> dict[str, Any]:
        if any(not VARIABLE_PATTERN.fullmatch(name) for name in value):
            raise ValueError("variable name is invalid")
        return _bounded_json(value)


class WorkflowDebugSchema(WorkflowExecutionSchema):
    node_id: str = Field(min_length=1, max_length=255, pattern=IDENTIFIER_PATTERN.pattern)


class _DataFactoryWorkflowBody(WorkflowDefinitionSchema):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=100_000)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("workflow name must not be blank")
        return value


class DataFactoryWorkflowCreate(_DataFactoryWorkflowBody):
    project_id: UUID


class DataFactoryWorkflowUpdate(_DataFactoryWorkflowBody):
    project_id: UUID
    state_version: int = Field(ge=0)


class DataFactoryWorkflowResponse(_DataFactoryWorkflowBody):
    id: UUID
    project_id: UUID
    state_version: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, record: Any) -> "DataFactoryWorkflowResponse":
        return cls.model_validate(record, from_attributes=True)


class DataFactoryWorkflowListResponse(ApiModel):
    items: list[DataFactoryWorkflowResponse]
    total: int


class DataFactoryRunResponse(ApiModel):
    id: str
    workflow_id: str
    status: Literal["SUCCEEDED", "FAILED"]
    node_results: dict[str, Any]
    variables: dict[str, Any]
    started_at: datetime
    finished_at: datetime | None

    @classmethod
    def from_record(cls, record: Any) -> "DataFactoryRunResponse":
        return cls.model_validate(record, from_attributes=True)


class DataFactoryRunListResponse(ApiModel):
    items: list[DataFactoryRunResponse]
    total: int


WorkflowNodeWrite = WorkflowNodeSchema
WorkflowEdgeWrite = WorkflowEdgeSchema
DataFactoryWorkflowDefinition = WorkflowDefinitionSchema
WorkflowExecutionCreate = WorkflowExecutionSchema
WorkflowDebugCreate = WorkflowDebugSchema
WorkflowCreate = DataFactoryWorkflowCreate
WorkflowUpdate = DataFactoryWorkflowUpdate
WorkflowResponse = DataFactoryWorkflowResponse
WorkflowListResponse = DataFactoryWorkflowListResponse
WorkflowRunResponse = DataFactoryRunResponse
WorkflowRunListResponse = DataFactoryRunListResponse