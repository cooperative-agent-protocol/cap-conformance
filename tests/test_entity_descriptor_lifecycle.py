# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CAP Authors
"""Conformance tests for Entity Descriptor lifecycle in CAP.

Tests:
- EntityDescriptor in CapabilityManifest
- EntityStateUpdate round-trip
- Heartbeat with entity_state_summary
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from google.protobuf.timestamp_pb2 import Timestamp

from cap.v0.core import runtime_pb2, runtime_pb2_grpc, common_pb2, machine_agent_pb2, entity_pb2
from cap_sdk.frame import (
    make_header,
    wrap_capability_manifest,
    wrap_heartbeat,
    wrap_entity_state_update,
)
from .conftest import make_test_manifest_with_entity, make_test_heartbeat_with_entity_summary


def _now_ts() -> Timestamp:
    ts = Timestamp()
    ts.FromDatetime(datetime.now(timezone.utc))
    return ts


@pytest.mark.asyncio
async def test_manifest_includes_entity_descriptor(servicer, server, channel):
    """Manifest with EntityDescriptor is received and has valid fields."""
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)

    manifest = make_test_manifest_with_entity("entity-test-01")
    header = make_header(sender_id="entity-test-01", receiver_id="site-agent-01")

    async def send_manifest():
        yield wrap_capability_manifest(header, manifest)
        await asyncio.sleep(0.5)

    stream = stub.Connect(send_manifest())
    await asyncio.sleep(0.3)
    stream.cancel()

    # Verify manifest was received
    assert len(servicer._received) >= 1
    mid, frame = servicer._received[0]
    assert mid == "entity-test-01"
    assert frame.HasField("capability_manifest")

    m = frame.capability_manifest
    assert m.HasField("entity_descriptor")
    descriptor = m.entity_descriptor
    assert descriptor.entity_id == "entity-test-01"
    assert descriptor.entity_type == "machine"
    assert descriptor.canonical_name == "テスト掘削機"
    assert len(descriptor.properties) >= 1
    assert descriptor.properties[0].key == "max_reach_m"
    assert descriptor.manager.available_skills == ["construction.excavate_batch"]


@pytest.mark.asyncio
async def test_entity_state_update_roundtrip(servicer, server, channel):
    """Machine sends EntityStateUpdate, server receives it correctly."""
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)

    # First send manifest to register, then Entity Descriptor update
    from .conftest import make_test_manifest
    manifest = make_test_manifest("entity-update-01")
    mh = make_header(sender_id="entity-update-01", receiver_id="site-agent-01")

    update = entity_pb2.EntityStateUpdate(
        machine_id="entity-update-01",
        descriptor=entity_pb2.EntityDescriptor(
            entity_id="entity-update-01",
            entity_type="machine",
            canonical_name="更新テスト機",
            state_description="掘削中: 進捗50%",
            updated_at=_now_ts(),
        ),
        update_reason="state_change",
    )
    uh = make_header(sender_id="entity-update-01", receiver_id="site-agent-01")

    async def send_msgs():
        yield wrap_capability_manifest(mh, manifest)
        await asyncio.sleep(0.2)
        yield wrap_entity_state_update(uh, update)
        await asyncio.sleep(0.5)

    stream = stub.Connect(send_msgs())
    await asyncio.sleep(0.5)
    stream.cancel()

    # Find the EntityStateUpdate frame
    entity_frames = [
        (mid, f) for mid, f in servicer._received
        if f.HasField("entity_state_update")
    ]
    assert len(entity_frames) >= 1
    mid, frame = entity_frames[0]
    assert mid == "entity-update-01"
    u = frame.entity_state_update
    assert u.machine_id == "entity-update-01"
    assert u.descriptor.entity_id == "entity-update-01"
    assert u.descriptor.state_description == "掘削中: 進捗50%"
    assert u.update_reason == "state_change"


@pytest.mark.asyncio
async def test_heartbeat_includes_entity_state_summary(servicer, server, channel):
    """Heartbeat with entity_state_summary is received with non-empty string."""
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)

    from .conftest import make_test_manifest
    manifest = make_test_manifest("hb-entity-01")
    mh = make_header(sender_id="hb-entity-01", receiver_id="site-agent-01")

    hb = make_test_heartbeat_with_entity_summary(
        "hb-entity-01", "テスト機: 通常稼働中、燃料100%"
    )
    hh = make_header(sender_id="hb-entity-01", receiver_id="site-agent-01")

    async def send_msgs():
        yield wrap_capability_manifest(mh, manifest)
        await asyncio.sleep(0.2)
        yield wrap_heartbeat(hh, hb)
        await asyncio.sleep(0.5)

    stream = stub.Connect(send_msgs())
    await asyncio.sleep(0.5)
    stream.cancel()

    # Find heartbeat frame
    hb_frames = [
        (mid, f) for mid, f in servicer._received
        if f.HasField("heartbeat")
    ]
    assert len(hb_frames) >= 1
    mid, frame = hb_frames[0]
    assert frame.heartbeat.entity_state_summary == "テスト機: 通常稼働中、燃料100%"
