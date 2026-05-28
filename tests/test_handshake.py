# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CAP Authors
"""Conformance Suite: Handshake

Validates that:
1. Machine agent sends CapabilityManifest as first message
2. Server registers the machine
3. Manifest fields are correctly received
"""

from __future__ import annotations

import asyncio

import grpc
import pytest

from cap.v0.core import runtime_pb2, runtime_pb2_grpc, common_pb2
from cap_sdk.frame import make_header, wrap_capability_manifest
from .conftest import make_test_manifest

MACHINE_ID = "test-excavator-hs"


@pytest.mark.asyncio
async def test_manifest_registers_machine(servicer, server, channel):
    """Sending CapabilityManifest should register the machine in the servicer."""
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)
    manifest = make_test_manifest(MACHINE_ID)

    async def request_iter():
        header = make_header(sender_id=MACHINE_ID, receiver_id="site-agent-01")
        yield wrap_capability_manifest(header, manifest)
        await asyncio.sleep(0.5)

    stream = stub.Connect(request_iter())

    try:
        async for _ in stream:
            pass
    except (grpc.aio.AioRpcError, asyncio.CancelledError):
        pass

    # Verify manifest was received
    manifests = [
        f for _, f in servicer._received if f.HasField("capability_manifest")
    ]
    assert len(manifests) >= 1
    received_manifest = manifests[0].capability_manifest
    assert received_manifest.machine_id == MACHINE_ID
    assert received_manifest.machine_type == common_pb2.MACHINE_TYPE_EXCAVATOR
    assert len(received_manifest.capabilities) == 1
    assert received_manifest.capabilities[0].skill == "construction.excavate_batch"


@pytest.mark.asyncio
async def test_manifest_includes_hal_profile(servicer, server, channel):
    """CapabilityManifest should include hal_profile and software_version."""
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)
    manifest = make_test_manifest(MACHINE_ID)

    async def request_iter():
        header = make_header(sender_id=MACHINE_ID, receiver_id="site-agent-01")
        yield wrap_capability_manifest(header, manifest)
        await asyncio.sleep(0.5)

    stream = stub.Connect(request_iter())

    try:
        async for _ in stream:
            pass
    except (grpc.aio.AioRpcError, asyncio.CancelledError):
        pass

    manifests = [
        f for _, f in servicer._received if f.HasField("capability_manifest")
    ]
    assert len(manifests) >= 1
    m = manifests[0].capability_manifest
    assert m.hal_profile == "test/fake"
    assert m.software_version == "0.1.0-test"
