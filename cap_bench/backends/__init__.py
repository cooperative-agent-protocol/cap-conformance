# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CAP Authors
"""Pluggable scenario backends.

The contract: a backend takes a ScenarioSpec, executes whatever subset of
the CAP stack it represents, and returns a RawOutcome. Evaluation against
SuccessMetrics is done by the runner — backends do not pre-judge success.

Available backends:

  - StubBackend (cap_bench.runner.StubBackend, also re-exported here):
    Deterministic happy-path stub. Fabricates an outcome that matches the
    scenario's excavation target. Used to lock the bench scaffolding
    before real backends land.

To plug a real implementation in, add a module under this package that
exposes an async ``execute(spec: ScenarioSpec) -> RawOutcome`` callable
and register it with the runner. Implementations live outside this
repo — cap-conformance ships only spec-level adapters.
"""

from cap_bench.runner import StubBackend

__all__ = ["StubBackend"]
