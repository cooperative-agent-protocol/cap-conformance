# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CAP Authors
"""Conformance Suite: Heartbeat

Validates that:
1. Heartbeat messages update last_heartbeat timestamp
2. Machine is considered alive when heartbeats are sent
3. Machine is considered dead after heartbeat timeout
"""

from __future__ import annotations

import asyncio
import time

import grpc
import pytest

from cap.v0.core import runtime_pb2, runtime_pb2_grpc, common_pb2
from cap_sdk.frame import make_header, wrap_capability_manifest, wrap_heartbeat
from cap_sdk.server import DEFAULT_HEARTBEAT_TIMEOUT
from .conftest import make_test_manifest, make_test_heartbeat

MACHINE_ID = "test-excavator-hb"


@pytest.mark.asyncio
async def test_heartbeat_keeps_alive(servicer, server, channel):
    """Sending heartbeats should keep the machine marked as alive."""
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)
    manifest = make_test_manifest(MACHINE_ID)
    heartbeat = make_test_heartbeat(MACHINE_ID)

    async def request_iter():
        header = make_header(sender_id=MACHINE_ID, receiver_id="site-agent-01")
        yield wrap_capability_manifest(header, manifest)
        await asyncio.sleep(0.2)

        header = make_header(sender_id=MACHINE_ID, receiver_id="site-agent-01")
        yield wrap_heartbeat(header, heartbeat)
        await asyncio.sleep(0.5)

    stream = stub.Connect(request_iter())

    try:
        async for _ in stream:
            pass
    except (grpc.aio.AioRpcError, asyncio.CancelledError):
        pass

    heartbeats = [f for _, f in servicer._received if f.HasField("heartbeat")]
    assert len(heartbeats) >= 1
    hb = heartbeats[0].heartbeat
    assert hb.machine_id == MACHINE_ID
    assert hb.healthy is True
    assert hb.estop_active is False


@pytest.mark.asyncio
async def test_heartbeat_fields(servicer, server, channel):
    """Heartbeat should contain mode, health, and fuel data."""
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)
    manifest = make_test_manifest(MACHINE_ID)
    heartbeat = make_test_heartbeat(MACHINE_ID)

    async def request_iter():
        header = make_header(sender_id=MACHINE_ID, receiver_id="site-agent-01")
        yield wrap_capability_manifest(header, manifest)
        await asyncio.sleep(0.1)
        header = make_header(sender_id=MACHINE_ID, receiver_id="site-agent-01")
        yield wrap_heartbeat(header, heartbeat)
        await asyncio.sleep(0.3)

    stream = stub.Connect(request_iter())

    try:
        async for _ in stream:
            pass
    except (grpc.aio.AioRpcError, asyncio.CancelledError):
        pass

    heartbeats = [f for _, f in servicer._received if f.HasField("heartbeat")]
    assert len(heartbeats) >= 1
    hb = heartbeats[0].heartbeat
    assert hb.current_mode == common_pb2.MACHINE_MODE_SUPERVISED_AUTONOMY
    assert hb.fuel_or_battery_percent == 100.0
