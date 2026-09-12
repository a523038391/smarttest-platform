from datetime import datetime, timezone
from uuid import uuid4

import pytest

from packages.protocol import Engine
from services.api.domain import (
    AttemptState,
    InvalidTransition,
    RunRecord,
    RunState,
    transition_attempt,
    transition_run,
)


def test_run_happy_path_and_terminal_guard() -> None:
    state = RunState.CREATED
    for target in (
        RunState.QUEUED,
        RunState.DISPATCHING,
        RunState.RUNNING,
        RunState.SUCCEEDED,
    ):
        state = transition_run(state, target)

    with pytest.raises(InvalidTransition):
        transition_run(state, RunState.RUNNING)


def test_attempt_happy_path_and_terminal_guard() -> None:
    state = AttemptState.PENDING
    for target in (
        AttemptState.CLAIMED,
        AttemptState.PREPARING,
        AttemptState.RUNNING,
        AttemptState.COLLECTING,
        AttemptState.SUCCEEDED,
    ):
        state = transition_attempt(state, target)

    with pytest.raises(InvalidTransition):
        transition_attempt(state, AttemptState.FAILED)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (RunState.CREATED, RunState.RUNNING),
        (RunState.CANCELLING, RunState.SUCCEEDED),
        (RunState.RUNNING, RunState.CANCELLED),
        (RunState.CANCELLED, RunState.QUEUED),
    ],
)
def test_illegal_run_transitions(current: RunState, target: RunState) -> None:
    with pytest.raises(InvalidTransition):
        transition_run(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (AttemptState.PENDING, AttemptState.RUNNING),
        (AttemptState.PREPARING, AttemptState.SUCCEEDED),
        (AttemptState.SUCCEEDED, AttemptState.FAILED),
    ],
)
def test_illegal_attempt_transitions(
    current: AttemptState, target: AttemptState
) -> None:
    with pytest.raises(InvalidTransition):
        transition_attempt(current, target)


def test_run_record_uses_validated_transition_result(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(timezone.utc)
    record = RunRecord(uuid4(), Engine.HTTP, {}, RunState.CREATED, now, now)
    monkeypatch.setattr(
        "services.api.domain.transition_run",
        lambda _current, _target: RunState.CANCELLING,
    )

    updated = record.with_state(RunState.QUEUED)

    assert updated.state is RunState.CANCELLING