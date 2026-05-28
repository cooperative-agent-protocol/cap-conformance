# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CAP Authors
"""Conformance tests for ALO lifecycle in CAP.

Tests:
- ALODescriptor in CapabilityManifest
- ALOStateUpdate round-trip
- Heartbeat with alo_state_summary
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from google.protobuf.timestamp_pb2 import Timestamp

from cap.v0.core import runtime_pb2, runtime_pb2_grpc, common_pb2, machine_agent_pb2, alo_pb2
from cap_sdk.frame import (
    make_header,
    wrap_capability_manifest,
    wrap_heartbeat,
    wrap_alo_state_update,
)
from .conftest import make_test_manifest_with_alo, make_test_heartbeat_with_alo_summary


def _now_ts() -> Timestamp:
    ts = Timestamp()
    ts.FromDatetime(datetime.now(timezone.utc))
    return ts


@pytest.mark.asyncio
async def test_manifest_includes_alo_descriptor(servicer, server, channel):
    """Manifest with ALODescriptor is received and has valid fields."""
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)

    manifest = make_test_manifest_with_alo("alo-test-01")
    header = make_header(sender_id="alo-test-01", receiver_id="site-agent-01")

    async def send_manifest():
        yield wrap_capability_manifest(header, manifest)
        await asyncio.sleep(0.5)

    stream = stub.Connect(send_manifest())
    await asyncio.sleep(0.3)
    stream.cancel()

    # Verify manifest was received
    assert len(servicer._received) >= 1
    mid, frame = servicer._received[0]
    assert mid == "alo-test-01"
    assert frame.HasField("capability_manifest")

    m = frame.capability_manifest
    assert m.HasField("alo_descriptor")
    alo = m.alo_descriptor
    assert alo.alo_id == "alo-test-01"
    assert alo.alo_type == "machine"
    assert alo.canonical_name == "テスト掘削機"
    assert len(alo.properties) >= 1
    assert alo.properties[0].key == "max_reach_m"
    assert alo.manager.available_skills == ["construction.excavate_batch"]


@pytest.mark.asyncio
async def test_alo_state_update_roundtrip(servicer, server, channel):
    """Machine sends ALOStateUpdate, server receives it correctly."""
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)

    # First send manifest to register, then ALO update
    from .conftest import make_test_manifest
    manifest = make_test_manifest("alo-update-01")
    mh = make_header(sender_id="alo-update-01", receiver_id="site-agent-01")

    update = alo_pb2.ALOStateUpdate(
        machine_id="alo-update-01",
        alo=alo_pb2.ALODescriptor(
            alo_id="alo-update-01",
            alo_type="machine",
            canonical_name="更新テスト機",
            state_description="掘削中: 進捗50%",
            updated_at=_now_ts(),
        ),
        update_reason="state_change",
    )
    uh = make_header(sender_id="alo-update-01", receiver_id="site-agent-01")

    async def send_msgs():
        yield wrap_capability_manifest(mh, manifest)
        await asyncio.sleep(0.2)
        yield wrap_alo_state_update(uh, update)
        await asyncio.sleep(0.5)

    stream = stub.Connect(send_msgs())
    await asyncio.sleep(0.5)
    stream.cancel()

    # Find the ALOStateUpdate frame
    alo_frames = [
        (mid, f) for mid, f in servicer._received
        if f.HasField("alo_state_update")
    ]
    assert len(alo_frames) >= 1
    mid, frame = alo_frames[0]
    assert mid == "alo-update-01"
    u = frame.alo_state_update
    assert u.machine_id == "alo-update-01"
    assert u.alo.alo_id == "alo-update-01"
    assert u.alo.state_description == "掘削中: 進捗50%"
    assert u.update_reason == "state_change"


@pytest.mark.asyncio
async def test_heartbeat_includes_alo_state_summary(servicer, server, channel):
    """Heartbeat with alo_state_summary is received with non-empty string."""
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)

    from .conftest import make_test_manifest
    manifest = make_test_manifest("hb-alo-01")
    mh = make_header(sender_id="hb-alo-01", receiver_id="site-agent-01")

    hb = make_test_heartbeat_with_alo_summary(
        "hb-alo-01", "テスト機: 通常稼働中、燃料100%"
    )
    hh = make_header(sender_id="hb-alo-01", receiver_id="site-agent-01")

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
    assert frame.heartbeat.alo_state_summary == "テスト機: 通常稼働中、燃料100%"
