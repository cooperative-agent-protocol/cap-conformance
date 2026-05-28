<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 CAP Authors
-->

# Changelog — `cap-conformance`

[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format,
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) per
[cap-spec ADR-008](../cap-spec/docs/adr/008-versioning.md).

## [Unreleased]

### Added
- SPDX-License-Identifier headers across all test sources.
- Skill-name retag in scenarios: `excavate_batch` → `construction.excavate_batch`,
  `haul_route` → `construction.haul_route`.
- Repo-level governance: LICENSE, NOTICE, CODE_OF_CONDUCT.md,
  TRADEMARK_POLICY.md, `.github/` issue + PR templates, FUNDING.yml.

### Changed
- Python imports updated `from cap.v0 import ...` → `from cap.v0.core import ...`
  following the cap-spec Phase 0.3 namespace reorganization.

### Pending
- `--target <endpoint>` + `--domain <pack>` pytest fixtures to decouple from
  cap-reference's in-process servicer (Autonomous backlog A5).
- Split tests into `core/` (mandatory Level 1/2/3) and
  `domains/construction/` (opt-in) per [ADR-011](../cap-spec/docs/adr/011-domain-pack-architecture.md).

## [0.1.0] — Unreleased
First public tag — planned alongside cap-spec v0.1.0.
