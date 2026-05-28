<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 CAP Authors
-->

# Governance — `cap-conformance`

This repository follows the CAP project umbrella governance defined in
[cap-spec/GOVERNANCE.md](../cap-spec/GOVERNANCE.md).

Repository-specific points:

## Maintainership

`cap-conformance` is maintained by the CAP steering committee
(initially the project BDFL). New maintainers may be added per the
umbrella governance.

## Decision authority

| Decision | Authority |
|---|---|
| New Core conformance test | Maintainer review + CI green |
| New Conformance Level (L1/L2/L3/L4+) | RFC in cap-spec |
| New Domain Pack conformance suite | Domain Pack RFC in cap-spec |
| Breaking changes to existing tests | Maintainer review + CHANGELOG note + minor version bump in cap-conformance |

## Stability

Conformance tests, once published in a release, are stable. Adding
new tests within a Level is a MINOR bump; the new test is advisory
until the next annual Level revision. Removing or weakening a test is
a MAJOR bump.

## Compatibility matrix

Pinned in [cap-spec/docs/compatibility.md](../cap-spec/docs/compatibility.md).
