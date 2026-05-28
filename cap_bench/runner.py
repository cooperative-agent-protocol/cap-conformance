# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CAP Authors
"""CAP-Bench scenario runner.

Two layers separated by design so the evaluation logic survives backend
changes:

1. ``ScenarioBackend.execute(spec) -> RawOutcome`` — opaque execution.
   ``StubBackend`` is the bundled fallback; real backends wire a
   conformant Site / Machine agent stack to the conformance servicer
   plus an in-memory simulator.

2. ``evaluate_outcome(spec, raw) -> ScenarioResult`` — pure function. Maps
   raw metrics + final state onto the SuccessMetrics contract, assigns
   the earliest applicable FailureLabel, and returns a ScenarioResult.

A third layer (e.g. a ``WorldStateProbe``) is optional: implementations
that want a live verify-node feed of terrain/payload deltas can plug
one in without changing the runner contract above.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Protocol

from cap_bench.schema import (
    PerSkillOutcome,
    ScenarioResult,
    ScenarioSpec,
)
from cap_bench.taxonomy import FailureLabel


# --- Raw outcome (between backend and evaluator) ------------------------


@dataclass
class RawOutcome:
    """What a backend produces. Pre-evaluation, label-free.

    The runner's evaluator is responsible for turning this into a
    ScenarioResult with a FailureLabel. Backends should not pre-judge
    success.
    """

    final_task_state: str  # "SUCCEEDED" | "FAILED" | "BLOCKED" | "TIMEOUT" | "ERROR"
    duration_s: float
    cycles_used: int
    safety_aborted: bool = False
    recoveries_used: list[str] = field(default_factory=list)
    measured_metrics: dict[str, float] = field(default_factory=dict)
    per_skill_outcomes: list[PerSkillOutcome] = field(default_factory=list)
    error: str | None = None
    """Set when the backend itself errored (HAL crash, gRPC drop). Maps to L5."""

    failure_label_hint: FailureLabel | None = None
    """Backend's diagnosis of why the run failed.

    Lets refusal scenarios be evaluated: a backend that detects a
    precondition violation sets
    ``failure_label_hint=L3_PRECONDITION_FAILURE``; the evaluator
    then matches it against ``SuccessMetrics.expected_failure_label``.

    Should NOT be set for SUCCESS runs — the evaluator ignores it
    when final_task_state matches the expected SUCCESS state.
    """


# --- Backend protocol ---------------------------------------------------


class ScenarioBackend(Protocol):
    """A pluggable executor for ScenarioSpec.

    Bundled implementation: ``StubBackend``. Real implementations
    typically wire the cap_sdk servicer + an in-memory simulator +
    a conformant Site / Machine agent stack.
    """

    async def execute(self, spec: ScenarioSpec) -> RawOutcome: ...


# --- Stub backend -------------------------------------------------------


class StubBackend:
    """Deterministic happy-path stub. Does not execute any agent.

    Used to lock the runner API and the test scaffolding before a real
    backend lands. For any scenario whose ``initial_world`` has at
    least one excavation zone with a volume_m3 target, it fabricates an
    outcome matching that target.

    This intentionally satisfies the basic scenario's metrics so the
    baseline test goes green. Replacing it with a real backend is what
    produces a meaningful conformance measurement.
    """

    async def execute(self, spec: ScenarioSpec) -> RawOutcome:
        # Tiny await so the call is genuinely async.
        await asyncio.sleep(0)

        excavation_zones = [z for z in spec.initial_world.zones if z.type == "excavation"]
        excavators = [m for m in spec.initial_world.machines if m.type == "excavator"]

        if not excavation_zones or not excavators:
            return RawOutcome(
                final_task_state="FAILED",
                duration_s=0.5,
                cycles_used=1,
                error="StubBackend requires at least one excavator and one excavation zone",
            )

        zone = excavation_zones[0]
        excavator = excavators[0]
        target_volume = zone.volume_m3 or 0.0

        per_skill = [
            PerSkillOutcome(
                skill_name="construction.excavate_batch",
                machine_id=excavator.id,
                duration_s=120.0,
                succeeded=True,
                measured_metrics={
                    "volume_excavated_m3": target_volume,
                    "bucket_fill_avg": 0.72,
                },
            )
        ]

        return RawOutcome(
            final_task_state="SUCCEEDED",
            duration_s=120.0,
            cycles_used=8,
            safety_aborted=False,
            recoveries_used=[],
            measured_metrics={
                "volume_excavated_m3": target_volume,
                "bucket_fill_avg": 0.72,
                "distance_traveled_m": 14.0,
            },
            per_skill_outcomes=per_skill,
        )


# --- Evaluator ----------------------------------------------------------


def evaluate_outcome(spec: ScenarioSpec, raw: RawOutcome) -> ScenarioResult:
    """Pure function: RawOutcome × ScenarioSpec → ScenarioResult.

    Two distinct flows:

    A. **Refusal-scenario flow** — used when
       ``spec.success_metrics.expected_failure_label`` is set. The
       scenario is testing that the system refuses for a *specific*
       reason (L3, L7, etc.). The evaluator trusts the backend's
       ``raw.failure_label_hint`` over its own L4 fallback because the
       backend is the authority on why it refused. Safety (L6) and
       backend errors (L5) still override — those are unconditional
       infrastructure failures.

    B. **Happy-path flow** — used when no expected_failure_label is set.
       The evaluator runs its priority detection (L5 > L6 > L4 > L1
       state-mismatch / unauthorized-recovery > L7 metric thresholds).

    Priority within Flow A:
      1. raw.error → L5 (backend itself broke; refusal can't be trusted)
      2. raw.safety_aborted (when scenario forbids it) → L6 (safety
         override always wins)
      3. final_task_state mismatch → L1
      4. failure_label_hint vs expected_failure_label match? → succeeded
      5. otherwise → L1 (mismatch)

    Priority within Flow B:
      1. error → L5
      2. safety_aborted → L6
      3. cycles_used > max_cycles → L4
      4. unauthorized recovery → L1
      5. final_task_state != expected → L1
      6. metric below threshold → L7

    Returns a ScenarioResult where ``succeeded`` is the conjunction of
    "no failure label" AND "all required metrics met".
    """
    # Always-on infrastructure overrides — apply to both flows.
    if raw.error is not None:
        return _result(
            spec, raw,
            succeeded=False,
            label=FailureLabel.L5_TOOL_ERROR,
            notes=f"backend error: {raw.error}",
        )
    if raw.safety_aborted and spec.success_metrics.no_safety_abort:
        return _result(
            spec, raw,
            succeeded=False,
            label=FailureLabel.L6_SAFETY_ABORT,
            notes="Safety Supervisor aborted execution",
        )

    expected_hint = spec.success_metrics.expected_failure_label
    if expected_hint is not None:
        return _evaluate_refusal_scenario(spec, raw, expected_hint)
    return _evaluate_happy_path(spec, raw)


# --- Refusal-scenario flow ---------------------------------------------


def _evaluate_refusal_scenario(
    spec: ScenarioSpec,
    raw: RawOutcome,
    expected_hint: FailureLabel,
) -> ScenarioResult:
    """Scenario is testing a specific refusal label; trust the backend hint."""
    # final_task_state must match (typically FAILED)
    if raw.final_task_state != spec.success_metrics.task_state:
        return _result(
            spec, raw,
            succeeded=False,
            label=FailureLabel.L1_PLAN_FAILURE,
            notes=(
                f"task_state {raw.final_task_state} "
                f"!= expected {spec.success_metrics.task_state}"
            ),
        )

    if raw.failure_label_hint == expected_hint:
        return _result(
            spec, raw,
            succeeded=True,
            label=None,
            notes=f"refusal verified: {expected_hint.value}",
        )

    reported = (
        raw.failure_label_hint.value
        if raw.failure_label_hint is not None
        else "None"
    )
    return _result(
        spec, raw,
        succeeded=False,
        label=FailureLabel.L1_PLAN_FAILURE,
        notes=(
            f"refusal mismatch: expected {expected_hint.value}, "
            f"backend reported {reported}"
        ),
    )


# --- Happy-path flow ---------------------------------------------------


def _evaluate_happy_path(spec: ScenarioSpec, raw: RawOutcome) -> ScenarioResult:
    """Scenario expects success (or generic FAILED with no specific label)."""
    if raw.final_task_state == "TIMEOUT" or raw.cycles_used > spec.max_cycles:
        return _result(
            spec, raw,
            succeeded=False,
            label=FailureLabel.L4_EXECUTION_TIMEOUT,
            notes=f"hit max_cycles={spec.max_cycles} or wall budget",
        )

    allowed = {r.value for r in spec.allowed_recovery}
    unauthorized = [r for r in raw.recoveries_used if r not in allowed]
    if unauthorized or (spec.success_metrics.no_recovery and raw.recoveries_used):
        return _result(
            spec, raw,
            succeeded=False,
            label=FailureLabel.L1_PLAN_FAILURE,
            notes=f"unauthorized or forbidden recovery: {raw.recoveries_used}",
        )

    if raw.final_task_state != spec.success_metrics.task_state:
        return _result(
            spec, raw,
            succeeded=False,
            label=FailureLabel.L1_PLAN_FAILURE,
            notes=(
                f"task_state {raw.final_task_state} "
                f"!= expected {spec.success_metrics.task_state}"
            ),
        )

    metric_failures = _check_metric_thresholds(spec, raw)
    if metric_failures:
        return _result(
            spec, raw,
            succeeded=False,
            label=FailureLabel.L7_VERIFICATION_FAILURE,
            notes="; ".join(metric_failures),
        )

    return _result(spec, raw, succeeded=True, label=None, notes="")


# --- Internal helper ---------------------------------------------------


def _result(
    spec: ScenarioSpec,
    raw: RawOutcome,
    *,
    succeeded: bool,
    label: FailureLabel | None,
    notes: str,
) -> ScenarioResult:
    """Build a ScenarioResult from common (spec, raw) fields.

    Centralizes the boilerplate so individual paths above stay short
    and the per-result fields stay consistent (duration_s, cycles_used,
    measured_metrics, per_skill_outcomes are always copied from raw).
    """
    return ScenarioResult(
        scenario_id=spec.id,
        succeeded=succeeded,
        failure_label=label,
        duration_s=raw.duration_s,
        cycles_used=raw.cycles_used,
        measured_metrics=dict(raw.measured_metrics),
        per_skill_outcomes=list(raw.per_skill_outcomes),
        notes=notes,
    )


def _check_metric_thresholds(spec: ScenarioSpec, raw: RawOutcome) -> list[str]:
    """Return human-readable descriptions of any metric threshold violations."""
    sm = spec.success_metrics
    m = raw.measured_metrics
    failures: list[str] = []

    def _check_min(metric_key: str, threshold: float | None) -> None:
        if threshold is None:
            return
        observed = m.get(metric_key)
        if observed is None:
            failures.append(f"{metric_key} not measured (required >= {threshold})")
            return
        if observed < threshold:
            failures.append(f"{metric_key}={observed:.3f} < {threshold:.3f}")

    def _check_max(metric_key: str, threshold: float | None) -> None:
        if threshold is None:
            return
        observed = m.get(metric_key)
        if observed is None:
            return  # max thresholds are silent if not measured
        if observed > threshold:
            failures.append(f"{metric_key}={observed:.3f} > {threshold:.3f}")

    _check_min("volume_excavated_m3", sm.volume_excavated_m3_min)
    _check_min("volume_dumped_m3", sm.volume_dumped_m3_min)
    _check_min("bucket_fill_avg", sm.bucket_fill_avg_min)
    _check_max("distance_traveled_m", sm.distance_traveled_m_max)
    _check_max("duration_s", sm.duration_s_max)
    if sm.cycle_count_min is not None:
        observed = m.get("cycle_count")
        if observed is None or observed < sm.cycle_count_min:
            failures.append(
                f"cycle_count={observed} < {sm.cycle_count_min}"
            )

    return failures


# --- Top-level entry point ----------------------------------------------


async def run_scenario(
    spec: ScenarioSpec,
    backend: ScenarioBackend | None = None,
) -> ScenarioResult:
    """Run one scenario end to end and return its labeled result.

    Default backend is ``StubBackend``. Tests can pass a real
    backend instance to exercise a conformant implementation.
    """
    if backend is None:
        backend = StubBackend()
    raw = await backend.execute(spec)
    return evaluate_outcome(spec, raw)
