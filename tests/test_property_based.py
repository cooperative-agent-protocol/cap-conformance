# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CAP Authors
"""Property-based / fuzz conformance tests (seeded, reproducible).

Fixed example vectors only find the bugs the author imagined; these tests
generate randomized inputs across the field and transition space and assert
protocol-level properties, complementing the example-based suite:

  * ``validate_frame`` is TOTAL (never raises on any constructible CapFrame,
    including malformed / out-of-range-enum input) and SOUND (a frame missing a
    required header field, or with no body, is always reported). A validation
    layer that crashes on adversarial input or silently accepts a malformed
    frame is a conformance defect a fixed vector would miss.
  * ``validate_state_transition`` implements EXACTLY the Ch06 §6.1.4 TaskState
    relation over the full state x state space, and terminal states are
    absorbing under random transition walks --- the implementation-side image
    of the TaskState TLA model and its Property P1.

The legal TaskState relation is restated here directly from the specification
(Ch06 §6.1.4), so these tests check the validator against the spec, not against
its own table. Seeds are fixed, so any failure is bit-exact reproducible.
"""

from __future__ import annotations

import random

import pytest

from cap.v0.core import common_pb2, runtime_pb2
from cap_sdk.frame import make_header
from cap_sdk.validator import validate_frame, validate_state_transition

_TS = common_pb2  # alias

# --- TaskState relation, restated from Ch06 §6.1.4 (independent of the validator) ---
_TERMINAL = {
    _TS.TASK_STATE_SUCCEEDED,
    _TS.TASK_STATE_FAILED,
    _TS.TASK_STATE_CANCELLED,
    _TS.TASK_STATE_REJECTED,
    _TS.TASK_STATE_EXPIRED,
}
_SPEC_LEGAL = {
    (_TS.TASK_STATE_PENDING, _TS.TASK_STATE_ACCEPTED),
    (_TS.TASK_STATE_PENDING, _TS.TASK_STATE_REJECTED),
    (_TS.TASK_STATE_PENDING, _TS.TASK_STATE_EXPIRED),
    (_TS.TASK_STATE_PENDING, _TS.TASK_STATE_CANCELLED),
    (_TS.TASK_STATE_ACCEPTED, _TS.TASK_STATE_RUNNING),
    (_TS.TASK_STATE_ACCEPTED, _TS.TASK_STATE_CANCELLED),
    (_TS.TASK_STATE_RUNNING, _TS.TASK_STATE_BLOCKED),
    (_TS.TASK_STATE_RUNNING, _TS.TASK_STATE_SUCCEEDED),
    (_TS.TASK_STATE_RUNNING, _TS.TASK_STATE_FAILED),
    (_TS.TASK_STATE_RUNNING, _TS.TASK_STATE_CANCELLED),
    (_TS.TASK_STATE_BLOCKED, _TS.TASK_STATE_RUNNING),
    (_TS.TASK_STATE_BLOCKED, _TS.TASK_STATE_FAILED),
    (_TS.TASK_STATE_BLOCKED, _TS.TASK_STATE_CANCELLED),
}
# Same-state progress updates the spec treats as IGNORE (accepted, no transition).
_SPEC_IGNORE = {
    (_TS.TASK_STATE_RUNNING, _TS.TASK_STATE_RUNNING),
    (_TS.TASK_STATE_BLOCKED, _TS.TASK_STATE_BLOCKED),
}
_NON_UNSPEC = [v for v in _TS.TaskState.values() if v != _TS.TASK_STATE_UNSPECIFIED]


def _rand_str(rng: random.Random) -> str:
    return rng.choice(
        ["", "   ", "m1", "site-agent-01",
         "".join(rng.choice("ab-_0 ") for _ in range(rng.randint(0, 10)))]
    )


def _rand_frame(rng: random.Random) -> runtime_pb2.CapFrame:
    """A CapFrame with randomized, often-malformed fields (incl. out-of-range
    enum ints, which proto3 open enums admit)."""
    f = runtime_pb2.CapFrame()
    if rng.random() < 0.85:
        f.header.CopyFrom(make_header(sender_id=_rand_str(rng), receiver_id=_rand_str(rng)))
    if rng.random() < 0.15:
        f.header.message_id = ""
    body = rng.choice(
        ["manifest", "heartbeat", "work_order", "progress_event",
         "reservation_request", "mode_command", "cap_error", "dialogue", "none"]
    )
    if body == "manifest":
        f.capability_manifest.machine_id = _rand_str(rng)
        f.capability_manifest.machine_type = rng.randint(0, 12)
    elif body == "heartbeat":
        f.heartbeat.machine_id = _rand_str(rng)
        f.heartbeat.fuel_or_battery_percent = rng.choice([-10.0, 0.0, 50.0, 150.0])
    elif body == "work_order":
        f.work_order.task_id = _rand_str(rng)
        f.work_order.skill = _rand_str(rng)
    elif body == "progress_event":
        f.progress_event.machine_id = _rand_str(rng)
        f.progress_event.state = rng.randint(0, 12)
        f.progress_event.completion_ratio = rng.choice([-1.0, 0.0, 0.5, 1.0, 2.0])
    elif body == "reservation_request":
        f.reservation_request.reservation_id = _rand_str(rng)
        f.reservation_request.resource_id = _rand_str(rng)
        f.reservation_request.holder_id = _rand_str(rng)
    elif body == "mode_command":
        f.mode_command.machine_id = _rand_str(rng)
        f.mode_command.requested_mode = rng.randint(0, 12)
    elif body == "cap_error":
        f.cap_error.error_code = rng.randint(0, 5000)
    elif body == "dialogue":
        f.agent_dialogue.dialogue_id = _rand_str(rng)
    # "none": leave the body oneof unset
    return f


def test_validate_frame_is_total():
    """Robustness: validate_frame never raises and always returns list[str],
    over thousands of randomized (incl. malformed) frames."""
    for seed in range(3000):
        f = _rand_frame(random.Random(seed))
        try:
            errs = validate_frame(f)
        except Exception as e:  # noqa: BLE001
            pytest.fail(f"validate_frame raised on seed {seed}: {e!r}")
        assert isinstance(errs, list)
        assert all(isinstance(e, str) for e in errs)


def test_validate_frame_sound_on_missing_required_fields():
    """Soundness: empty header message_id/sender_id and an absent body are
    always reported as errors."""
    for seed in range(500):
        f = _rand_frame(random.Random(seed))
        f.header.message_id = ""
        f.header.sender_id = ""
        errs = validate_frame(f)
        assert any("message_id" in e for e in errs)
        assert any("sender_id" in e for e in errs)
    g = runtime_pb2.CapFrame()
    g.header.CopyFrom(make_header(sender_id="m", receiver_id="s"))
    assert any("body is empty" in e for e in validate_frame(g))


def test_taskstate_relation_matches_spec_exhaustively():
    """validate_state_transition equals the Ch06 TaskState relation over the
    full state x state space; terminal states accept nothing (P1 absorption)."""
    legal = _SPEC_LEGAL | _SPEC_IGNORE
    for cur in _NON_UNSPEC:
        for nxt in _NON_UNSPEC:
            ok, _ = validate_state_transition(cur, nxt)
            if cur in _TERMINAL:
                assert not ok, f"terminal {_TS.TaskState.Name(cur)} accepted a transition (P1 violated)"
            elif (cur, nxt) in legal:
                assert ok, f"spec-legal {_TS.TaskState.Name(cur)}->{_TS.TaskState.Name(nxt)} rejected"
            else:
                assert not ok, f"undocumented {_TS.TaskState.Name(cur)}->{_TS.TaskState.Name(nxt)} accepted"


def test_taskstate_random_walks_absorb_at_terminal():
    """Stateful property: random walks from PENDING that apply only accepted
    transitions never escape a terminal state (terminal absorption, P1)."""
    for seed in range(2000):
        rng = random.Random(seed)
        state = _TS.TASK_STATE_PENDING
        for _ in range(25):
            nxt = rng.choice(_NON_UNSPEC)
            ok, _ = validate_state_transition(state, nxt)
            if state in _TERMINAL:
                assert not ok, f"escaped terminal {_TS.TaskState.Name(state)}"
                break
            if ok and nxt != state:
                state = nxt
