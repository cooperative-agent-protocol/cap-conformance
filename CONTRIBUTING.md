<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 CAP Authors
-->

# Contributing to `cap-conformance`

Thank you for your interest. The CAP project's contribution rules are
defined in [cap-spec/CONTRIBUTING.md](../cap-spec/CONTRIBUTING.md);
this document covers conformance-suite-specific points.

## DCO

All commits must be signed off with `git commit -s`. See the upstream
DCO section in [cap-spec/CONTRIBUTING.md](../cap-spec/CONTRIBUTING.md).

## SPDX headers

All source files must carry an `SPDX-License-Identifier: Apache-2.0`
header. Use the `scripts/add_spdx_headers.sh` helper if you add new
files.

## How to add a test

### Core conformance test (mandatory for all implementations)

1. Choose a Conformance Level (1 / 2 / 3) — see
   [cap-spec/docs/specification/ch10-conformance.md](../cap-spec/docs/specification/ch10-conformance.md).
2. Add the test under `tests/` (post-A5 reorganization will move these
   to `core/`).
3. Use the `--target <endpoint>` fixture so the test runs against any
   conformant implementation, not only the bundled cap-reference servicer.
4. Document the test in CHANGELOG.md under `[Unreleased]`.

### Domain Pack test (opt-in)

1. Tests for a specific Domain Pack go under `domains/<name>/`.
2. Gated by `pytest --domain <name>`.
3. Must reference the Domain Pack proto types (e.g.,
   `cap.v0.domains.construction`), not bare Core types.

## RFC for new Levels

Adding a new Conformance Level (e.g., L4) requires an RFC — see
[cap-spec/rfcs/README.md](../cap-spec/rfcs/README.md).

## Style

- Python: ruff + black-compatible formatting.
- Test names: `test_<message_or_state>_<expected_behavior>`.
- Fixtures: prefer pytest fixtures over module-level globals.

## License

Contributions are Apache License 2.0 — see [LICENSE](LICENSE).
