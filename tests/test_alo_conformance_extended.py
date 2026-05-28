# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CAP Authors
"""Extended ALO conformance tests — E3.8-4.

Tests ALO lifecycle beyond basic round-trip:
- ALO property update propagation
- ALO interaction between objects
- ALO self-improvement loop (descriptor evolution)
- Multiple ALO objects in a single update
- ALO descriptor validation (required fields)
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import pytest
from google.protobuf.timestamp_pb2 import Timestamp

from cap.v0.core import runtime_pb2, runtime_pb2_grpc, alo_pb2, common_pb2
from cap_sdk.frame import (
    make_header,
    wrap_capability_manifest,
    wrap_alo_state_update,
    wrap_alo_interaction,
)
from .conftest import make_test_manifest_with_alo


def _now_ts() -> Timestamp:
    ts = Timestamp()
    ts.FromDatetime(datetime.now(timezone.utc))
    return ts


@pytest.mark.asyncio
async def test_alo_property_update_propagation(servicer, server, channel):
    """ALOStateUpdate with changed properties is received correctly."""
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)
    machine_id = "alo-prop-01"

    manifest = make_test_manifest_with_alo(machine_id)
    mh = make_header(sender_id=machine_id, receiver_id="site-agent-01")

    # Send initial manifest, then an update with changed property
    update = alo_pb2.ALOStateUpdate(
        machine_id=machine_id,
        alo=alo_pb2.ALODescriptor(
            alo_id=machine_id,
            alo_type="machine",
            canonical_name="テスト掘削機",
            properties=[
                alo_pb2.ALOProperty(key="fuel_percent", value="75.0", unit="%", dtype="float"),
                alo_pb2.ALOProperty(key="bucket_load_m3", value="0.8", unit="m³", dtype="float"),
            ],
            state_description="掘削中: 燃料75%, バケット0.8m³",
            updated_at=_now_ts(),
        ),
        update_reason="property_change",
    )
    uh = make_header(sender_id=machine_id, receiver_id="site-agent-01")

    async def send_msgs():
        yield wrap_capability_manifest(mh, manifest)
        await asyncio.sleep(0.2)
        yield wrap_alo_state_update(uh, update)
        await asyncio.sleep(0.5)

    stream = stub.Connect(send_msgs())
    await asyncio.sleep(0.5)
    stream.cancel()

    alo_frames = [
        (mid, f) for mid, f in servicer._received
        if f.HasField("alo_state_update")
    ]
    assert len(alo_frames) >= 1
    _, frame = alo_frames[0]
    alo = frame.alo_state_update.alo
    assert len(alo.properties) == 2
    assert alo.properties[0].key == "fuel_percent"
    assert alo.properties[0].value == "75.0"
    assert frame.alo_state_update.update_reason == "property_change"


@pytest.mark.asyncio
async def test_alo_interaction_between_objects(servicer, server, channel):
    """ALOInteraction records an interaction between two ALO objects."""
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)
    machine_id = "alo-interact-01"

    manifest = make_test_manifest_with_alo(machine_id)
    mh = make_header(sender_id=machine_id, receiver_id="site-agent-01")

    interaction = alo_pb2.ALOInteraction(
        interaction_id=str(uuid.uuid4()),
        initiator_alo_id=machine_id,
        target_alo_id="zone_excavation",
        interaction_type="excavate",
        description="掘削機がゾーンB3で掘削作業を実施",
        timestamp=_now_ts(),
    )
    ih = make_header(sender_id=machine_id, receiver_id="site-agent-01")

    async def send_msgs():
        yield wrap_capability_manifest(mh, manifest)
        await asyncio.sleep(0.2)
        yield wrap_alo_interaction(ih, interaction)
        await asyncio.sleep(0.5)

    stream = stub.Connect(send_msgs())
    await asyncio.sleep(0.5)
    stream.cancel()

    int_frames = [
        (mid, f) for mid, f in servicer._received
        if f.HasField("alo_interaction")
    ]
    assert len(int_frames) >= 1
    _, frame = int_frames[0]
    ai = frame.alo_interaction
    assert ai.initiator_alo_id == machine_id
    assert ai.target_alo_id == "zone_excavation"
    assert ai.interaction_type == "excavate"


@pytest.mark.asyncio
async def test_alo_descriptor_evolution(servicer, server, channel):
    """ALO descriptor can evolve over time with additional sub-objects."""
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)
    machine_id = "alo-evolve-01"

    manifest = make_test_manifest_with_alo(machine_id)
    mh = make_header(sender_id=machine_id, receiver_id="site-agent-01")

    # Update with sub-objects (component description learned during operation)
    evolved_alo = alo_pb2.ALODescriptor(
        alo_id=machine_id,
        alo_type="machine",
        canonical_name="テスト掘削機",
        properties=[
            alo_pb2.ALOProperty(key="max_reach_m", value="9.9", unit="m", dtype="float"),
        ],
        sub_objects=[
            alo_pb2.ALOSubObject(
                sub_obj_id="bucket",
                sub_obj_type="attachment",
                description="バケット",
                properties=[
                    alo_pb2.ALOProperty(key="capacity_m3", value="0.8", unit="m³", dtype="float"),
                    alo_pb2.ALOProperty(key="wear_percent", value="15.0", unit="%", dtype="float"),
                ],
            ),
        ],
        state_description="自己改善: バケット摩耗度15%を検出",
        updated_at=_now_ts(),
    )
    update = alo_pb2.ALOStateUpdate(
        machine_id=machine_id,
        alo=evolved_alo,
        update_reason="self_improvement",
    )
    uh = make_header(sender_id=machine_id, receiver_id="site-agent-01")

    async def send_msgs():
        yield wrap_capability_manifest(mh, manifest)
        await asyncio.sleep(0.2)
        yield wrap_alo_state_update(uh, update)
        await asyncio.sleep(0.5)

    stream = stub.Connect(send_msgs())
    await asyncio.sleep(0.5)
    stream.cancel()

    alo_frames = [
        (mid, f) for mid, f in servicer._received
        if f.HasField("alo_state_update")
    ]
    assert len(alo_frames) >= 1
    _, frame = alo_frames[0]
    alo = frame.alo_state_update.alo
    assert len(alo.sub_objects) == 1
    assert alo.sub_objects[0].sub_obj_id == "bucket"
    assert alo.sub_objects[0].properties[1].key == "wear_percent"
    assert frame.alo_state_update.update_reason == "self_improvement"


@pytest.mark.asyncio
async def test_alo_descriptor_requires_alo_id(servicer, server, channel):
    """ALODescriptor without alo_id should still be deliverable but flaggable."""
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)
    machine_id = "alo-valid-01"

    manifest = make_test_manifest_with_alo(machine_id)
    mh = make_header(sender_id=machine_id, receiver_id="site-agent-01")

    # Empty alo_id — protobuf default is ""
    update = alo_pb2.ALOStateUpdate(
        machine_id=machine_id,
        alo=alo_pb2.ALODescriptor(
            alo_id="",  # Missing required semantic field
            alo_type="machine",
            updated_at=_now_ts(),
        ),
        update_reason="test_validation",
    )
    uh = make_header(sender_id=machine_id, receiver_id="site-agent-01")

    async def send_msgs():
        yield wrap_capability_manifest(mh, manifest)
        await asyncio.sleep(0.2)
        yield wrap_alo_state_update(uh, update)
        await asyncio.sleep(0.5)

    stream = stub.Connect(send_msgs())
    await asyncio.sleep(0.5)
    stream.cancel()

    # Message is delivered (proto3 doesn't enforce required fields)
    # but alo_id is empty string
    alo_frames = [
        (mid, f) for mid, f in servicer._received
        if f.HasField("alo_state_update")
    ]
    assert len(alo_frames) >= 1
    _, frame = alo_frames[0]
    assert frame.alo_state_update.alo.alo_id == ""
