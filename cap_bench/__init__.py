# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CAP Authors
"""CAP-Bench — yaml-driven E2E scenario benchmark for CAP.

Defines the scenario / metric / failure-label vocabulary used by the
conformance suite. The metric keys here are intended to be reused by
downstream concepts (e.g. ``SkillManifest.expected_effects`` and
``Episode.total_outcome.kpis``) so a single label flows end-to-end.
"""

from cap_bench.schema import (
    ExpectedAssignmentSpec,
    ExpectedTaskGraph,
    InitialWorld,
    MachineSpec,
    ObstacleSpec,
    ScenarioResult,
    ScenarioSpec,
    SimulatorOverrides,
    SuccessMetrics,
    ZoneSpec,
    load_scenario,
)
from cap_bench.taxonomy import FailureLabel, RecoveryAction

__all__ = [
    "ExpectedAssignmentSpec",
    "ExpectedTaskGraph",
    "FailureLabel",
    "InitialWorld",
    "MachineSpec",
    "ObstacleSpec",
    "RecoveryAction",
    "ScenarioResult",
    "ScenarioSpec",
    "SimulatorOverrides",
    "SuccessMetrics",
    "ZoneSpec",
    "load_scenario",
]
