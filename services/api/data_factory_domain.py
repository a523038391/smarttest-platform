"""Domain records and graph invariants for Data Factory workflows."""
from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any, Mapping


MAX_WORKFLOW_NODES = 100
MAX_WORKFLOW_EDGES = 500
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$")


class WorkflowValidationError(ValueError):
    """Raised when a workflow graph violates a domain invariant."""


class WorkflowNodeType(str, Enum):
    HTTP = "http"
    CONDITION = "condition"
    PARALLEL = "parallel"


class WorkflowEdgeOutcome(str, Enum):
    ALWAYS = "always"
    SUCCESS = "success"
    FAILURE = "failure"
    TRUE = "true"
    FALSE = "false"


@dataclass(frozen=True, slots=True)
class WorkflowNode:
    id: str
    name: str
    type: WorkflowNodeType
    x: float
    y: float
    config: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class WorkflowEdge:
    id: str
    source: str
    target: str
    outcome: WorkflowEdgeOutcome


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    nodes: tuple[WorkflowNode, ...] = ()
    edges: tuple[WorkflowEdge, ...] = ()
    variables: Mapping[str, Any] = field(default_factory=dict)
    id: str = "debug"


_COMPATIBLE_OUTCOMES = {
    WorkflowNodeType.HTTP: frozenset({
        WorkflowEdgeOutcome.ALWAYS,
        WorkflowEdgeOutcome.SUCCESS,
        WorkflowEdgeOutcome.FAILURE,
    }),
    WorkflowNodeType.CONDITION: frozenset({
        WorkflowEdgeOutcome.ALWAYS,
        WorkflowEdgeOutcome.TRUE,
        WorkflowEdgeOutcome.FALSE,
    }),
    WorkflowNodeType.PARALLEL: frozenset({WorkflowEdgeOutcome.ALWAYS}),
}


def _validate_identifier(value: str, label: str) -> None:
    if not isinstance(value, str) or not IDENTIFIER_PATTERN.fullmatch(value):
        raise WorkflowValidationError(f"{label} is invalid")


def validate_workflow_graph(definition: WorkflowDefinition) -> None:
    """Validate identifiers, references, edge semantics, roots, and acyclicity."""
    if len(definition.nodes) > MAX_WORKFLOW_NODES:
        raise WorkflowValidationError("workflow has too many nodes")
    if len(definition.edges) > MAX_WORKFLOW_EDGES:
        raise WorkflowValidationError("workflow has too many edges")

    nodes: dict[str, WorkflowNode] = {}
    for node in definition.nodes:
        _validate_identifier(node.id, "node id")
        try:
            WorkflowNodeType(node.type)
        except ValueError:
            raise WorkflowValidationError(f"node {node.id} type is invalid") from None
        if node.id in nodes:
            raise WorkflowValidationError(f"duplicate node id: {node.id}")
        nodes[node.id] = node

    edge_ids: set[str] = set()
    incoming = {node_id: 0 for node_id in nodes}
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    for edge in definition.edges:
        _validate_identifier(edge.id, "edge id")
        if edge.id in edge_ids:
            raise WorkflowValidationError(f"duplicate edge id: {edge.id}")
        edge_ids.add(edge.id)
        if edge.source not in nodes or edge.target not in nodes:
            raise WorkflowValidationError(f"edge {edge.id} references an unknown node")
        try:
            outcome = WorkflowEdgeOutcome(edge.outcome)
            source_type = WorkflowNodeType(nodes[edge.source].type)
        except ValueError:
            raise WorkflowValidationError(f"edge {edge.id} outcome is invalid") from None
        if outcome not in _COMPATIBLE_OUTCOMES[source_type]:
            raise WorkflowValidationError(
                f"edge {edge.id} outcome is incompatible with its source node"
            )
        incoming[edge.target] += 1
        outgoing[edge.source].append(edge.target)

    if not nodes:
        if definition.edges:
            raise WorkflowValidationError("an empty workflow cannot contain edges")
        return
    roots = [node_id for node_id, count in incoming.items() if count == 0]
    if not roots:
        raise WorkflowValidationError("workflow must have at least one root node")

    remaining = dict(incoming)
    ready = list(roots)
    visited = 0
    while ready:
        current = ready.pop()
        visited += 1
        for target in outgoing[current]:
            remaining[target] -= 1
            if remaining[target] == 0:
                ready.append(target)
    if visited != len(nodes):
        raise WorkflowValidationError("workflow must be a DAG")


# Concise aliases for consumers that do not use the Workflow prefix.
NodeType = WorkflowNodeType
EdgeOutcome = WorkflowEdgeOutcome
validate_workflow = validate_workflow_graph