# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CAP Authors
"""Conformance tests for reconnection protocol (Level 1).

Tests that a Machine Agent reconnects properly after disconnection,
sending a fresh CapabilityManifest as the first message.
"""

from __future__ import annotations

import asyncio

import grpc
import pytest

from cap.v0.core import runtime_pb2, runtime_pb2_grpc, common_pb2, machine_agent_pb2
from cap_sdk.frame import make_header, wrap_capability_manifest, wrap_heartbeat
from .conftest import make_test_manifest, make_test_heartbeat


@pytest.mark.asyncio
async def test_reconnect_sends_fresh_manifest(servicer, server, channel):
    """After reconnection, Machine Agent MUST send a fresh CapabilityManifest.

    Per Ch08 §8.3.2 step 4a: Send a fresh CapabilityManifest on reconnect.
    Per Ch04 §4.2.1 rule 1: First message MUST be CapabilityManifest.
    """
    srv, port = server
    machine_id = "reconnect-test-01"

    # First connection
    async with grpc.aio.insecure_channel(f"localhost:{port}") as ch1:
        stub1 = runtime_pb2_grpc.CapRuntimeServiceStub(ch1)

        async def first_stream():
            header = make_header(sender_id=machine_id, receiver_id="site-agent-01")
            manifest = make_test_manifest(machine_id)
            yield wrap_capability_manifest(header, manifest)

            header2 = make_header(sender_id=machine_id, receiver_id="site-agent-01")
            hb = make_test_heartbeat(machine_id)
            yield wrap_heartbeat(header2, hb)

            await asyncio.sleep(0.2)

        stream1 = stub1.Connect(first_stream())
        try:
            async for _ in stream1:
                pass
        except (grpc.aio.AioRpcError, asyncio.CancelledError):
            pass

    # Verify first connection was registered and then cleaned up
    assert machine_id not in servicer.machines, (
        "Machine should be unregistered after disconnect"
    )

    # Clear received for clean second connection check
    first_count = len(servicer._received)

    # Second connection (simulating reconnect)
    async with grpc.aio.insecure_channel(f"localhost:{port}") as ch2:
        stub2 = runtime_pb2_grpc.CapRuntimeServiceStub(ch2)

        async def second_stream():
            # Must send manifest first on reconnect
            header = make_header(sender_id=machine_id, receiver_id="site-agent-01")
            manifest = make_test_manifest(machine_id)
            # Could include active_task_id in heartbeat
            manifest.software_version = "0.1.1-reconnect"
            yield wrap_capability_manifest(header, manifest)

            header2 = make_header(sender_id=machine_id, receiver_id="site-agent-01")
            hb = make_test_heartbeat(machine_id)
            hb.active_task_id = "task-from-previous-session"
            yield wrap_heartbeat(header2, hb)

            await asyncio.sleep(0.2)

        stream2 = stub2.Connect(second_stream())
        try:
            async for _ in stream2:
                pass
        except (grpc.aio.AioRpcError, asyncio.CancelledError):
            pass

    # Verify the second connection sent manifest first
    reconnect_frames = servicer._received[first_count:]
    assert len(reconnect_frames) >= 2, "Reconnection should send manifest + heartbeat"

    _, first_frame = reconnect_frames[0]
    assert first_frame.HasField("capability_manifest"), (
        "First message after reconnect MUST be CapabilityManifest"
    )
    assert first_frame.capability_manifest.machine_id == machine_id
    assert first_frame.capability_manifest.software_version == "0.1.1-reconnect"

    # Verify heartbeat contains active_task_id
    _, second_frame = reconnect_frames[1]
    assert second_frame.HasField("heartbeat"), "Second message should be Heartbeat"
    assert second_frame.heartbeat.active_task_id == "task-from-previous-session"
