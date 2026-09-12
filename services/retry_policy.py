"""Retry decisions shared by Runner Manager and orchestration tests."""
from dataclasses import dataclass
from typing import Mapping

from packages.protocol import Engine, ResultEnvelope, TaskEnvelope


@dataclass(frozen=True, slots=True)
class RetryDecision:
    should_retry: bool
    delay_seconds: int = 0
    reason: str = ""


def decide_retry(
    task: TaskEnvelope, result: ResultEnvelope, attempt_number: int,
) -> RetryDecision:
    policy = task.execution_policy.retry
    if attempt_number >= policy.max_attempts:
        return RetryDecision(False, reason="max_attempts_reached")
    if result.outcome not in policy.retry_on:
        return RetryDecision(False, reason="outcome_not_retryable")
    if task.engine is Engine.HTTP and not policy.allow_non_idempotent_http:
        requests = [task.parameters.get("request", {})]
        requests.extend(step.request for step in task.execution_policy.before_steps)
        requests.extend(step.request for step in task.execution_policy.after_steps)
        methods = {
            str(request.get("method", "GET")).upper()
            if isinstance(request, Mapping) else ""
            for request in requests
        }
        if not methods.issubset({"GET", "HEAD", "OPTIONS", "TRACE", "PUT", "DELETE"}):
            return RetryDecision(False, reason="non_idempotent_http")
    exponent = max(0, attempt_number - 1)
    delay = min(policy.backoff_seconds * (2 ** exponent), policy.max_backoff_seconds)
    return RetryDecision(True, delay, f"retryable_{result.outcome.value}")