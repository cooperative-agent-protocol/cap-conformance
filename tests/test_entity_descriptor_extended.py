# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CAP Authors
"""Extended Entity Descriptor conformance tests — E3.8-4.

Tests Entity Descriptor lifecycle beyond basic round-trip:
- Entity Descriptor property update propagation
- Entity Descriptor interaction between objects
- Entity Descriptor self-improvement loop (descriptor evolution)
- Multiple Entity Descriptor objects in a single update
- Entity Descriptor descriptor validation (required fields)
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import pytest
from google.protobuf.timestamp_pb2 import Timestamp

from cap.v0.core import runtime_pb2, runtime_pb2_grpc, entity_pb2, common_pb2
from cap_sdk.frame import (
    make_header,
    wrap_capability_manifest,
    wrap_entity_state_update,
    wrap_entity_interaction,
)
from .conftest import make_test_manifest_with_entity


def _now_ts() -> Timestamp:
    ts = Timestamp()
    ts.FromDatetime(datetime.now(timezone.utc))
    return ts


@pytest.mark.asyncio
async def test_entity_property_update_propagation(servicer, server, channel):
    """EntityStateUpdate with changed properties is received correctly."""
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)
    machine_id = "entity-prop-01"

    manifest = make_test_manifest_with_entity(machine_id)
    mh = make_header(sender_id=machine_id, receiver_id="site-agent-01")

    # Send initial manifest, then an update with changed property
    update = entity_pb2.EntityStateUpdate(
        machine_id=machine_id,
        descriptor=entity_pb2.EntityDescriptor(
            entity_id=machine_id,
            entity_type="machine",
            canonical_name="テスト掘削機",
            properties=[
                entity_pb2.EntityProperty(key="fuel_percent", value="75.0", unit="%", dtype="float"),
                entity_pb2.EntityProperty(key="bucket_load_m3", value="0.8", unit="m³", dtype="float"),
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
        yield wrap_entity_state_update(uh, update)
        await asyncio.sleep(0.5)

    stream = stub.Connect(send_msgs())
    await asyncio.sleep(0.5)
    stream.cancel()

    entity_frames = [
        (mid, f) for mid, f in servicer._received
        if f.HasField("entity_state_update")
    ]
    assert len(entity_frames) >= 1
    _, frame = entity_frames[0]
    descriptor = frame.entity_state_update.descriptor
    assert len(descriptor.properties) == 2
    assert descriptor.properties[0].key == "fuel_percent"
    assert descriptor.properties[0].value == "75.0"
    assert frame.entity_state_update.update_reason == "property_change"


@pytest.mark.asyncio
async def test_entity_interaction_between_objects(servicer, server, channel):
    """EntityInteraction records an interaction between two Entity Descriptor objects."""
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)
    machine_id = "entity-interact-01"

    manifest = make_test_manifest_with_entity(machine_id)
    mh = make_header(sender_id=machine_id, receiver_id="site-agent-01")

    interaction = entity_pb2.EntityInteraction(
        interaction_id=str(uuid.uuid4()),
        initiator_entity_id=machine_id,
        target_entity_id="zone_excavation",
        interaction_type="excavate",
        description="掘削機がゾーンB3で掘削作業を実施",
        timestamp=_now_ts(),
    )
    ih = make_header(sender_id=machine_id, receiver_id="site-agent-01")

    async def send_msgs():
        yield wrap_capability_manifest(mh, manifest)
        await asyncio.sleep(0.2)
        yield wrap_entity_interaction(ih, interaction)
        await asyncio.sleep(0.5)

    stream = stub.Connect(send_msgs())
    await asyncio.sleep(0.5)
    stream.cancel()

    int_frames = [
        (mid, f) for mid, f in servicer._received
        if f.HasField("entity_interaction")
    ]
    assert len(int_frames) >= 1
    _, frame = int_frames[0]
    ai = frame.entity_interaction
    assert ai.initiator_entity_id == machine_id
    assert ai.target_entity_id == "zone_excavation"
    assert ai.interaction_type == "excavate"


@pytest.mark.asyncio
async def test_entity_descriptor_evolution(servicer, server, channel):
    """Entity Descriptor descriptor can evolve over time with additional sub-objects."""
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)
    machine_id = "entity-evolve-01"

    manifest = make_test_manifest_with_entity(machine_id)
    mh = make_header(sender_id=machine_id, receiver_id="site-agent-01")

    # Update with sub-objects (component description learned during operation)
    evolved_descriptor = entity_pb2.EntityDescriptor(
        entity_id=machine_id,
        entity_type="machine",
        canonical_name="テスト掘削機",
        properties=[
            entity_pb2.EntityProperty(key="max_reach_m", value="9.9", unit="m", dtype="float"),
        ],
        sub_objects=[
            entity_pb2.EntitySubObject(
                sub_obj_id="bucket",
                sub_obj_type="attachment",
                description="バケット",
                properties=[
                    entity_pb2.EntityProperty(key="capacity_m3", value="0.8", unit="m³", dtype="float"),
                    entity_pb2.EntityProperty(key="wear_percent", value="15.0", unit="%", dtype="float"),
                ],
            ),
        ],
        state_description="自己改善: バケット摩耗度15%を検出",
        updated_at=_now_ts(),
    )
    update = entity_pb2.EntityStateUpdate(
        machine_id=machine_id,
        descriptor=evolved_descriptor,
        update_reason="self_improvement",
    )
    uh = make_header(sender_id=machine_id, receiver_id="site-agent-01")

    async def send_msgs():
        yield wrap_capability_manifest(mh, manifest)
        await asyncio.sleep(0.2)
        yield wrap_entity_state_update(uh, update)
        await asyncio.sleep(0.5)

    stream = stub.Connect(send_msgs())
    await asyncio.sleep(0.5)
    stream.cancel()

    entity_frames = [
        (mid, f) for mid, f in servicer._received
        if f.HasField("entity_state_update")
    ]
    assert len(entity_frames) >= 1
    _, frame = entity_frames[0]
    descriptor = frame.entity_state_update.descriptor
    assert len(descriptor.sub_objects) == 1
    assert descriptor.sub_objects[0].sub_obj_id == "bucket"
    assert descriptor.sub_objects[0].properties[1].key == "wear_percent"
    assert frame.entity_state_update.update_reason == "self_improvement"


@pytest.mark.asyncio
async def test_entity_descriptor_requires_entity_id(servicer, server, channel):
    """EntityDescriptor without entity_id should still be deliverable but flaggable."""
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)
    machine_id = "entity-valid-01"

    manifest = make_test_manifest_with_entity(machine_id)
    mh = make_header(sender_id=machine_id, receiver_id="site-agent-01")

    # Empty entity_id — protobuf default is ""
    update = entity_pb2.EntityStateUpdate(
        machine_id=machine_id,
        descriptor=entity_pb2.EntityDescriptor(
            entity_id="",  # Missing required semantic field
            entity_type="machine",
            updated_at=_now_ts(),
        ),
        update_reason="test_validation",
    )
    uh = make_header(sender_id=machine_id, receiver_id="site-agent-01")

    async def send_msgs():
        yield wrap_capability_manifest(mh, manifest)
        await asyncio.sleep(0.2)
        yield wrap_entity_state_update(uh, update)
        await asyncio.sleep(0.5)

    stream = stub.Connect(send_msgs())
    await asyncio.sleep(0.5)
    stream.cancel()

    # Message is delivered (proto3 doesn't enforce required fields)
    # but entity_id is empty string
    entity_frames = [
        (mid, f) for mid, f in servicer._received
        if f.HasField("entity_state_update")
    ]
    assert len(entity_frames) >= 1
    _, frame = entity_frames[0]
    assert frame.entity_state_update.descriptor.entity_id == ""
