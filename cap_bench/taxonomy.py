# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CAP Authors
"""Failure taxonomy for CAP-Bench scenarios.

The 8-category labeling scheme used to classify scenario outcomes.
L3 (precondition) and L7 (verification) are detectable only when the
backend implements SkillContract preconditions / effect verification;
otherwise they collapse into L4 (timeout) or L1 (plan failure).
"""

from __future__ import annotations

from enum import Enum


class FailureLabel(str, Enum):
    """Why a scenario did not reach SUCCESS.

    SUCCESS is the absence of a failure label. Each non-success run gets
    exactly one label — the earliest cause in the pipeline.
    """

    L1_PLAN_FAILURE = "L1_PLAN_FAILURE"
    """Site Agent could not produce a valid TaskGraph from the instruction.

    Examples: capability mismatch, zone schema violation, no machine satisfies
    required_skills filter, LLM returned malformed assignment.
    """

    L2_DISPATCH_FAILURE = "L2_DISPATCH_FAILURE"
    """TaskGraph compiled but no machine could be selected / dispatched.

    Examples: machine selection returned empty, all candidates rejected
    by affordance filter, reservation conflict at dispatch time.
    """

    L3_PRECONDITION_FAILURE = "L3_PRECONDITION_FAILURE"
    """Skill started but its preconditions were not satisfied at runtime.

    Detectable only when SkillManifest.preconditions are evaluated by
    the backend; otherwise these collapse into L4 timeout or L7
    verification failure.
    """

    L4_EXECUTION_TIMEOUT = "L4_EXECUTION_TIMEOUT"
    """Machine Agent hit max_iterations or budget_seconds without terminal state."""

    L5_TOOL_ERROR = "L5_TOOL_ERROR"
    """Underlying tool / HAL / planner raised an unrecoverable error.

    Examples: HAL gRPC error, Hybrid A* could not find path, primitive switch
    failed, sensor stream disconnected.
    """

    L6_SAFETY_ABORT = "L6_SAFETY_ABORT"
    """Safety Supervisor intervened (E-stop, geofence, proximity).

    Distinct from L3 because Safety is the deterministic last line — its
    intervention is always correct behavior, but indicates the planning
    layer failed to avoid the unsafe state.
    """

    L7_VERIFICATION_FAILURE = "L7_VERIFICATION_FAILURE"
    """Skill reported COMPLETION but expected_effects were not observed.

    Detectable only when the backend evaluates
    ``SkillManifest.success_detector_fn`` (or equivalent). Otherwise
    these are silently mislabeled as SUCCESS.
    """

    L8_COORDINATION_FAILURE = "L8_COORDINATION_FAILURE"
    """Multi-machine coordination broke down.

    Examples: shadow price oscillation, deadlock between reservations,
    starvation, Site Agent could not resolve conflicting Machine reports.
    """


class RecoveryAction(str, Enum):
    """Recovery actions a scenario may permit.

    Listed in `ScenarioSpec.allowed_recovery`. If a recovery occurred that
    is NOT in this list, the scenario is failed regardless of final state.
    """

    RETRY_ONCE = "retry_once"
    """Single retry of the same skill with same arguments."""

    REPLAN = "replan"
    """Site Agent re-issues the work order (potentially to a different machine)."""

    REQUEST_TRUCK_REPOSITION = "request_truck_reposition"
    """Excavator requests dump truck to reposition."""

    ADJUST_DIG_DEPTH = "adjust_dig_depth"
    """Per-skill recovery for low_bucket_fill."""

    LOCAL_REPLAN = "local_replan"
    """Reactive layer triggered local path replan (already implemented)."""
