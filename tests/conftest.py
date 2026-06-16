# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CAP Authors
"""Shared fixtures for conformance tests.

Flags:

    --transport {grpc,mqtt}     Transport profile for the conformance run.
                                Default: grpc.
                                MQTT mode requires mosquitto on
                                localhost:1883.

    --target <endpoint>         Drive tests against an external CAP runtime
                                instead of the bundled in-process Python
                                servicer. Format: grpc://host:port for gRPC
                                target, or mqtt://host:port for MQTT.
                                Default: (empty) — start the in-process
                                reference servicer on a random port.

    --domain <name>             Enable Domain-Pack-specific tests. Tests
                                marked with @pytest.mark.domain(<name>)
                                are skipped unless --domain matches.
                                Specify multiple via comma:
                                `--domain construction,agriculture`.
                                Default: (empty) — Core tests only;
                                domain-marked tests are skipped.

The fixtures `server`, `channel`, and `servicer` honour `--target` and
adjust their setup accordingly. Tests written against these fixtures
work unmodified against either the bundled reference or an external
implementation. This is the abstraction that makes cap-conformance
**implementation-agnostic** per ADR-011 (Domain Pack Architecture).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import AsyncIterator
from datetime import datetime, timezone
from urllib.parse import urlparse

import grpc
import pytest
import pytest_asyncio
from google.protobuf.timestamp_pb2 import Timestamp

from cap.v0.core import runtime_pb2, runtime_pb2_grpc, common_pb2, machine_agent_pb2, entity_pb2, dialogue_pb2
from cap_sdk.server import CapRuntimeServicer, serve
from cap_sdk.frame import make_header, wrap_capability_manifest, wrap_heartbeat


def pytest_addoption(parser):
    parser.addoption(
        "--transport",
        action="store",
        default="grpc",
        choices=["grpc", "mqtt"],
        help="Transport to use for conformance tests (default: grpc)",
    )
    parser.addoption(
        "--target",
        action="store",
        default="",
        help=(
            "External CAP runtime endpoint to test against, "
            "e.g. grpc://localhost:50051. Empty (default) = use the bundled "
            "in-process reference servicer."
        ),
    )
    parser.addoption(
        "--domain",
        action="store",
        default="",
        help=(
            "Comma-separated list of Domain Pack names to enable, "
            "e.g. --domain construction. Domain-tagged tests are skipped "
            "unless their tag is in this list. Empty (default) = Core only."
        ),
    )


def pytest_configure(config):
    """Register custom markers so pytest does not warn on `domain` marker."""
    config.addinivalue_line(
        "markers",
        "domain(name): mark a test as belonging to a specific Domain Pack "
        "(e.g. `@pytest.mark.domain('construction')`). Skipped unless "
        "`--domain <name>` matches.",
    )
    config.addinivalue_line(
        "markers",
        "core: mark a test as Core-only (always runs regardless of --domain).",
    )
    config.addinivalue_line(
        "markers",
        "requirement(*ids): map this test to the normative requirement IDs "
        "(Ch08 error codes / Ch10 conformance checkboxes) it discharges, for "
        "the requirement-traceability matrix printed at end of run.",
    )


# Requirement-ID -> [test node ids], populated at collection time.
_REQUIREMENT_COVERAGE: dict[str, list[str]] = {}


def pytest_collection_modifyitems(config, items):
    """Skip Domain Pack tests whose tag is not in --domain, and build the
    requirement-traceability index from `@pytest.mark.requirement(...)`."""
    enabled = config.getoption("--domain", default="") or ""
    enabled_set = {d.strip() for d in enabled.split(",") if d.strip()}
    skip_marker = pytest.mark.skip(reason="Domain Pack not enabled via --domain")
    _REQUIREMENT_COVERAGE.clear()
    for item in items:
        for marker in item.iter_markers(name="domain"):
            if not marker.args:
                continue
            pack_name = marker.args[0]
            if pack_name not in enabled_set:
                item.add_marker(skip_marker)
                break
        for marker in item.iter_markers(name="requirement"):
            for req_id in marker.args:
                _REQUIREMENT_COVERAGE.setdefault(req_id, []).append(item.nodeid)


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Print the requirement-traceability matrix (requirement ID -> tests)."""
    if not _REQUIREMENT_COVERAGE:
        return
    terminalreporter.section("CAP requirement traceability")
    for req_id in sorted(_REQUIREMENT_COVERAGE):
        tests = _REQUIREMENT_COVERAGE[req_id]
        names = ", ".join(t.split("::")[-1] for t in tests)
        terminalreporter.write_line(f"  {req_id}: {names}")


@pytest.fixture(scope="session")
def transport_type(request):
    return request.config.getoption("--transport")


@pytest.fixture(scope="session")
def target_endpoint(request) -> str:
    """Raw --target string. Empty = in-process bundled reference."""
    return request.config.getoption("--target") or ""


@pytest.fixture(scope="session")
def target_url(target_endpoint):
    """Parsed --target as a urllib.parse.ParseResult, or None if empty."""
    return urlparse(target_endpoint) if target_endpoint else None


def _now_ts() -> Timestamp:
    ts = Timestamp()
    ts.FromDatetime(datetime.now(timezone.utc))
    return ts


@pytest_asyncio.fixture
async def servicer(target_url):
    """A CapRuntimeServicer with a simple frame collector.

    Returns the bundled reference servicer instance. When --target points
    to an external endpoint, tests that need to introspect server-side
    state via `servicer._received` should be marked with
    `@pytest.mark.skipif` against the target option (those tests verify
    the reference implementation itself, not vendor conformance).
    """
    if target_url is not None:
        pytest.skip(
            "Test depends on bundled reference servicer internals; "
            "incompatible with --target."
        )
    received: list[tuple[str, runtime_pb2.CapFrame]] = []

    async def on_frame(machine_id, frame):
        received.append((machine_id, frame))
        return None

    s = CapRuntimeServicer(on_frame=on_frame)
    s._received = received  # type: ignore
    return s


@pytest_asyncio.fixture
async def server(servicer, target_url):
    """Start a gRPC server.

    When --target is empty (default): serve the **same** bundled
    ``servicer`` instance that the test configures via
    ``servicer._on_frame`` and inspects via ``servicer._received``, on a
    random local port, and yield (server, port).  Serving the identical
    instance is essential: tests override ``servicer._on_frame`` to make
    the server dispatch WorkOrders, so the served servicer and the
    ``servicer`` fixture must be one and the same object.

    When --target is set: yield (None, port) where port is parsed from the
    target URL; tests connect to the external endpoint via the `channel`
    fixture below.
    """
    if target_url is not None:
        # External target; no local server.
        port = target_url.port
        host = target_url.hostname or "localhost"
        yield None, (host, port)
        return

    # In-process reference servicer path: serve the SAME instance as the
    # `servicer` fixture so per-test `servicer._on_frame` overrides take effect.
    srv = grpc.aio.server()
    runtime_pb2_grpc.add_CapRuntimeServiceServicer_to_server(servicer, srv)
    port = srv.add_insecure_port("[::]:0")
    await srv.start()
    yield srv, port
    await srv.stop(grace=0)


@pytest_asyncio.fixture
async def channel(server, target_url):
    """gRPC channel — to the bundled local server, or to the --target endpoint."""
    srv, port_info = server
    if target_url is not None:
        host, port = port_info
        endpoint = f"{host}:{port}"
        async with grpc.aio.insecure_channel(endpoint) as ch:
            yield ch
        return
    # local in-process server
    async with grpc.aio.insecure_channel(f"localhost:{port_info}") as ch:
        yield ch


@pytest_asyncio.fixture
async def target_channel(target_url):
    """A gRPC channel to the external --target endpoint.

    Independent of the `servicer`/`server` fixtures (which exist only for the
    in-process bundled reference and skip under --target). Tests using this
    fixture self-skip when no --target is given, so the default-mode run is
    unaffected. This is the entry point for the external-target conformance
    profile in test_external_target.py.
    """
    if target_url is None:
        pytest.skip("external-target profile: requires --target grpc://host:port")
    host = target_url.hostname or "localhost"
    port = target_url.port
    async with grpc.aio.insecure_channel(f"{host}:{port}") as ch:
        yield ch


def make_test_manifest(machine_id: str = "test-machine-01"):
    """Create a minimal CapabilityManifest for testing."""
    return machine_agent_pb2.CapabilityManifest(
        machine_id=machine_id,
        machine_type=common_pb2.MACHINE_TYPE_EXCAVATOR,
        capabilities=[
            machine_agent_pb2.Capability(skill="construction.excavate_batch", limits=[]),
        ],
        current_mode=common_pb2.MACHINE_MODE_SUPERVISED_AUTONOMY,
        hal_profile="test/fake",
        software_version="0.1.0-test",
    )


def make_test_manifest_with_entity(machine_id: str = "test-machine-01"):
    """Create a CapabilityManifest with EntityDescriptor for testing."""
    return machine_agent_pb2.CapabilityManifest(
        machine_id=machine_id,
        machine_type=common_pb2.MACHINE_TYPE_EXCAVATOR,
        capabilities=[
            machine_agent_pb2.Capability(skill="construction.excavate_batch", limits=[]),
        ],
        current_mode=common_pb2.MACHINE_MODE_SUPERVISED_AUTONOMY,
        hal_profile="test/fake",
        software_version="0.2.0-test",
        entity_descriptor=entity_pb2.EntityDescriptor(
            entity_id=machine_id,
            entity_type="machine",
            canonical_name="テスト掘削機",
            properties=[
                entity_pb2.EntityProperty(key="max_reach_m", value="9.9", unit="m", dtype="float"),
            ],
            state_description="テスト状態",
            manager=entity_pb2.EntityManager(
                available_skills=["construction.excavate_batch"],
            ),
            owner_agent_id=machine_id,
            updated_at=_now_ts(),
        ),
    )


def make_test_heartbeat(machine_id: str = "test-machine-01"):
    """Create a minimal Heartbeat for testing."""
    return machine_agent_pb2.Heartbeat(
        machine_id=machine_id,
        current_mode=common_pb2.MACHINE_MODE_SUPERVISED_AUTONOMY,
        healthy=True,
        estop_active=False,
        fuel_or_battery_percent=100.0,
    )


def make_test_heartbeat_with_entity_summary(
    machine_id: str = "test-machine-01",
    summary: str = "テスト機: 通常稼働中、燃料100%",
):
    """Create a Heartbeat with entity_state_summary for testing."""
    return machine_agent_pb2.Heartbeat(
        machine_id=machine_id,
        current_mode=common_pb2.MACHINE_MODE_SUPERVISED_AUTONOMY,
        healthy=True,
        estop_active=False,
        fuel_or_battery_percent=100.0,
        entity_state_summary=summary,
    )


def make_test_dialogue(
    machine_id: str = "test-machine-01",
    task_id: str = "test-task-01",
    intent: dialogue_pb2.DialogueIntent.ValueType = dialogue_pb2.DIALOGUE_INTENT_SITUATION_REPORT,
):
    """Create a test AgentDialogue with a SituationReport."""
    provenance = dialogue_pb2.DialogueProvenance(
        agent_id=machine_id,
        agent_role="machine_agent",
        confidence=0.8,
        model_id="fake-rule-based",
        generated_at=_now_ts(),
        reasoning_summary="Test provenance",
    )

    msg = dialogue_pb2.DialogueMessage(
        message_id=str(uuid.uuid4()),
        intent=intent,
        provenance=provenance,
        timestamp=_now_ts(),
    )

    if intent == dialogue_pb2.DIALOGUE_INTENT_SITUATION_REPORT:
        msg.situation_report.CopyFrom(dialogue_pb2.SituationReport(
            machine_id=machine_id,
            task_id=task_id,
            situation_summary="テスト状況報告",
            recommended_action="テスト推奨アクション",
            provenance=provenance,
        ))
    elif intent == dialogue_pb2.DIALOGUE_INTENT_LOCAL_DECISION:
        msg.local_decision.CopyFrom(dialogue_pb2.LocalDecision(
            machine_id=machine_id,
            task_id=task_id,
            scope=dialogue_pb2.LOCAL_DECISION_SCOPE_TASK,
            decision_summary="テスト判断",
            rationale="テスト理由",
            approval_required=True,
            approved=False,
            provenance=provenance,
        ))
    elif intent == dialogue_pb2.DIALOGUE_INTENT_PLAN_PROPOSAL:
        msg.plan_proposal.CopyFrom(dialogue_pb2.PlanProposal(
            machine_id=machine_id,
            task_id=task_id,
            proposal_summary="テスト提案",
            expected_outcome="テスト結果",
            provenance=provenance,
        ))
    else:
        msg.text = "テストメッセージ"

    return dialogue_pb2.AgentDialogue(
        dialogue_id=str(uuid.uuid4()),
        participant_ids=[machine_id, "site-agent-01"],
        task_id=task_id,
        messages=[msg],
        created_at=_now_ts(),
    )
