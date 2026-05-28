# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CAP Authors
"""Pydantic schema for CAP-Bench scenario specifications.

The canonical vocabulary for CAP-Bench scenarios. The metric keys
(`volume_excavated_m3_min`, `bucket_fill_avg_min`, ...) are
intentionally the same names that downstream concepts use in
``SkillManifest.expected_effects`` and ``Episode.total_outcome.kpis``,
so a single label flows end-to-end without rename churn.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from cap_bench.taxonomy import FailureLabel, RecoveryAction


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- World primitives ----------------------------------------------------


Pose = Annotated[
    tuple[float, float, float],
    Field(description="(x_m, y_m, yaw_rad), site frame"),
]


class MachineSpec(_Frozen):
    """A machine present in the initial world."""

    id: str
    type: Literal["excavator", "dump_truck", "bulldozer", "carrier", "roller"]
    model: str = Field(
        description=(
            "Model number identifier (scenario-local string). "
            "Implementations map this to their own machine profile registry."
        ),
    )
    pose: Pose
    fuel_percent: float = Field(default=100.0, ge=0.0, le=100.0)
    payload_m3: float = Field(
        default=0.0,
        ge=0.0,
        description="Current payload volume (m3). Used by effect verifiers.",
    )
    payload_capacity_m3: float | None = Field(
        default=None,
        gt=0.0,
        description=(
            "Total payload capacity (m3). "
            "When set, ``min_payload_capacity_m3`` precondition checks "
            "(capacity - payload_m3) >= threshold. "
            "When None, the precondition is treated as soft-skip rather "
            "than failing — capacity-sensitive skills should require this."
        ),
    )


class ZoneSpec(_Frozen):
    """A spatial zone in the initial world.

    Geometry is either a circle (`center` + `radius_m`) or polygon
    (`polygon`). Exactly one must be set.
    """

    id: str
    type: Literal["excavation", "dump", "load_point", "keepout", "stockpile"]
    center: tuple[float, float] | None = None
    radius_m: float | None = Field(default=None, gt=0.0)
    polygon: list[tuple[float, float]] | None = Field(default=None, min_length=3)
    volume_m3: float | None = Field(
        default=None,
        ge=0.0,
        description="For excavation zones: target volume. For dump zones: remaining capacity.",
    )
    soil_class: Literal["sand", "clay", "gravel", "rock", "mixed"] | None = None
    slope_deg_max: float | None = Field(default=None, ge=0.0, le=90.0)

    @field_validator("polygon", mode="after")
    @classmethod
    def _check_geometry(cls, polygon, info):
        center = info.data.get("center")
        radius = info.data.get("radius_m")
        circle_set = center is not None and radius is not None
        polygon_set = polygon is not None
        if circle_set == polygon_set:
            raise ValueError(
                "ZoneSpec must specify exactly one of (center+radius_m) or polygon"
            )
        return polygon


class ObstacleSpec(_Frozen):
    """A static or moving obstacle."""

    id: str
    pose: Pose
    radius_m: float = Field(gt=0.0)
    moving: bool = False


class InitialWorld(_Frozen):
    """Top-level world container for a scenario's starting state."""

    machines: list[MachineSpec] = Field(min_length=1)
    zones: list[ZoneSpec] = Field(default_factory=list)
    obstacles: list[ObstacleSpec] = Field(default_factory=list)


# --- Expected task graph -------------------------------------------------


class ExpectedTaskGraph(_Frozen):
    """Soft constraints on the TaskGraph the Site Agent should produce.

    `must_contain_skills` and `forbidden_skills` are checked against the
    set of skill names appearing in the compiled graph. They are advisory
    — a scenario can still pass with a different skill mix, as long as
    `success_metrics` are met.
    """

    must_contain_skills: list[str] = Field(default_factory=list)
    forbidden_skills: list[str] = Field(default_factory=list)
    min_node_count: int | None = Field(default=None, ge=1)
    max_node_count: int | None = Field(default=None, ge=1)


# --- Success metrics -----------------------------------------------------


class SuccessMetrics(_Frozen):
    """Required outcomes for the scenario to be labeled SUCCESS.

    Every field is optional; only the ones a scenario cares about should
    be set. Field names are shared with ``SkillManifest`` and
    ``Episode`` KPI vocabularies — do NOT rename without updating
    those layers.

    Refusal scenarios (the system is *expected* to fail) set both
    ``task_state`` and ``expected_failure_label``. The evaluator then
    requires the backend to (a) return the matching task_state AND
    (b) supply a ``failure_label_hint`` matching ``expected_failure_label``.
    Either alone is insufficient — the suite must distinguish "FAILED
    for the right reason" (L3 precondition) from "FAILED for the
    wrong reason" (L4 timeout, L5 tool error).
    """

    task_state: Literal["SUCCEEDED", "FAILED", "BLOCKED"] = "SUCCEEDED"
    """Final TaskState the scenario expects on the root task."""

    expected_failure_label: FailureLabel | None = Field(
        default=None,
        description=(
            "When task_state != SUCCEEDED, the failure must carry this label. "
            "Set for refusal scenarios that test specific detection paths "
            "(e.g. L3 for precondition tests). None for happy-path scenarios."
        ),
    )

    no_safety_abort: bool = True
    """If True, any L6 Safety Supervisor intervention fails the scenario."""

    no_recovery: bool = False
    """If True, any recovery action (even one in allowed_recovery) fails the scenario."""

    # --- Earthwork-specific (excavate / haul / dump) ---
    volume_excavated_m3_min: float | None = Field(default=None, ge=0.0)
    volume_dumped_m3_min: float | None = Field(default=None, ge=0.0)
    bucket_fill_avg_min: float | None = Field(default=None, ge=0.0, le=1.0)
    distance_traveled_m_max: float | None = Field(default=None, ge=0.0)
    cycle_count_min: int | None = Field(default=None, ge=1)

    # --- Generic ---
    duration_s_max: float | None = Field(default=None, gt=0.0)
    final_pose_within: dict[str, float] | None = Field(
        default=None,
        description="Per-machine pose tolerance: {machine_id: tolerance_m}",
    )


# --- Multi-skill scenario assignments -----------------------------------


class ExpectedAssignmentSpec(_Frozen):
    """One pre-determined Site→Machine assignment for multi-skill scenarios.

    Single-skill scenarios (e.g. scn_excavate_basic_01) leave this list
    empty and the bench's ``_synthesize_allocation`` infers a single
    ``(machine, zone)`` pairing from intent matching. Multi-skill
    scenarios (e.g. excavate+haul chain) declare each assignment
    explicitly so the synthesis stays deterministic — what the LLM
    Site Agent would emit, but pinned by the scenario author for
    reproducibility.

    Used by chain scenarios (scn_excavate_haul_chain_01) to validate
    that multiple skill bundles run end-to-end against the same
    backend pipeline.
    """

    skill: str = Field(
        description="SkillManifest.name (scenario-local skill identifier)",
    )
    machine_id: str = Field(
        description="Target machine id from initial_world.machines",
    )
    zone_id: str = Field(
        default="",
        description="Target zone id; empty when the skill is zoneless",
    )
    priority: int = Field(default=5, ge=1, le=10)


# --- Simulator-side knobs (test hooks, not real-system fields) ----------


class SimulatorOverrides(_Frozen):
    """Test-only knobs that shape what mock backends report.

    These exist so a scenario can simulate degraded execution without
    requiring an actual physical mismatch in initial_world. They have no
    effect on a real implementation run — the field is consumed only by
    in-memory mock backends.

    ``yield_factor`` exercises L7_VERIFICATION_FAILURE detection: a
    backend that synthesizes
    ``measured_volume = zone.volume_m3 * yield_factor`` lets us write
    scenarios where the skill "promised" a delta but the simulator
    delivers less.
    """

    yield_factor: float | None = Field(
        default=None,
        gt=0.0,
        le=1.5,
        description=(
            "Multiplier applied by mock backends to synthesized output "
            "metrics (volume_excavated_m3, etc.). 1.0 = nominal, "
            "<1.0 = degraded execution, >1.0 = over-performance. "
            "Out of (0.0, 1.5] is rejected to avoid pathological tests."
        ),
    )


# --- Top-level scenario spec --------------------------------------------


class ScenarioSpec(_Frozen):
    """A single CAP-Bench scenario."""

    id: str = Field(pattern=r"^scn_[a-z0-9_]+_\d{2}$")
    """Stable scenario id, e.g. 'scn_excavate_basic_01'."""

    description: str = ""
    instruction: str = Field(min_length=1)
    """Natural language instruction passed to the Site Agent."""

    initial_world: InitialWorld
    expected_task_graph: ExpectedTaskGraph = Field(default_factory=ExpectedTaskGraph)
    success_metrics: SuccessMetrics = Field(default_factory=SuccessMetrics)
    simulator_overrides: SimulatorOverrides = Field(
        default_factory=SimulatorOverrides,
        description=(
            "Mock-backend knobs (e.g. yield_factor) for testing degraded "
            "execution paths. Ignored by real implementation runs."
        ),
    )
    expected_assignments: list[ExpectedAssignmentSpec] = Field(
        default_factory=list,
        description=(
            "Explicit Site→Machine assignments for multi-skill scenarios. "
            "When set, the bench bypasses single-skill inference and emits "
            "exactly these assignments. When empty, falls back to single-"
            "skill intent matching (most baseline scenarios)."
        ),
    )

    allowed_recovery: list[RecoveryAction] = Field(default_factory=list)
    max_cycles: int = Field(default=30, ge=1)
    """Max Machine Agent ReAct iterations across all machines."""

    budget_seconds: float = Field(default=600.0, gt=0.0)
    """Wall clock budget. Hitting this is L4_EXECUTION_TIMEOUT."""

    tags: list[str] = Field(default_factory=list)
    """Free-form tags for filtering, e.g. 'baseline', 'recovery', 'haul'."""


# --- Result --------------------------------------------------------------


class PerSkillOutcome(_Frozen):
    """Outcome of one skill execution. Reused by Episode KPI records."""

    skill_name: str
    machine_id: str
    duration_s: float
    succeeded: bool
    failure_label: FailureLabel | None = None
    measured_metrics: dict[str, float] = Field(default_factory=dict)


class ScenarioResult(_Frozen):
    """The outcome of running a ScenarioSpec.

    A run is SUCCESS iff `failure_label is None` AND all required
    success_metrics were met. The runner sets `failure_label` to the
    earliest cause; downstream phases may revise the label as more
    detection becomes available.
    """

    scenario_id: str
    succeeded: bool
    failure_label: FailureLabel | None
    duration_s: float
    cycles_used: int
    measured_metrics: dict[str, float] = Field(default_factory=dict)
    """Metric values observed at scenario end. Keys match SuccessMetrics fields."""

    per_skill_outcomes: list[PerSkillOutcome] = Field(default_factory=list)
    notes: str = ""


# --- Loading -------------------------------------------------------------


def load_scenario(path: str | Path) -> ScenarioSpec:
    """Load a yaml scenario file and validate it against ScenarioSpec.

    Raises pydantic.ValidationError on schema violations.
    """
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return ScenarioSpec.model_validate(raw)
