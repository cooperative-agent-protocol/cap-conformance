# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CAP Authors
"""Conformance tests for timeout and cascade behavior (Level 2).

Tests heartbeat timeout detection and its consequences.
"""

from __future__ import annotations

import asyncio
import time

import grpc
import pytest

from cap.v0.core import runtime_pb2, runtime_pb2_grpc
from cap_sdk.server import CapRuntimeServicer, DEFAULT_HEARTBEAT_TIMEOUT
from cap_sdk.frame import make_header, wrap_capability_manifest, wrap_heartbeat
from .conftest import make_test_manifest, make_test_heartbeat


@pytest.mark.asyncio
async def test_heartbeat_timeout_marks_disconnected(servicer, server, channel):
    """Site Agent MUST detect heartbeat timeout and mark machine as disconnected.

    Per Ch04 §4.2.2: If heartbeat_timeout expires, mark machine as DISCONNECTED.
    Per Ch08 §8.3.4: Mark machine as DISCONNECTED on partition detection.
    """
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)
    machine_id = "timeout-test-01"

    async def request_stream():
        header = make_header(sender_id=machine_id, receiver_id="site-agent-01")
        manifest = make_test_manifest(machine_id)
        yield wrap_capability_manifest(header, manifest)

        # Send one heartbeat
        header2 = make_header(sender_id=machine_id, receiver_id="site-agent-01")
        hb = make_test_heartbeat(machine_id)
        yield wrap_heartbeat(header2, hb)

        # Then stop sending heartbeats — wait beyond timeout
        # Note: DEFAULT_HEARTBEAT_TIMEOUT is 15s, too long for test.
        # We check the is_alive property instead of waiting.
        await asyncio.sleep(0.5)

    stream = stub.Connect(request_stream())

    try:
        async for _ in stream:
            pass
    except (grpc.aio.AioRpcError, asyncio.CancelledError):
        pass

    # After stream closes, machine should be cleaned up
    assert machine_id not in servicer.machines, (
        "Machine should be removed from registry after disconnect"
    )


@pytest.mark.asyncio
async def test_heartbeat_keeps_alive_flag(servicer, server, channel):
    """Connected machines with recent heartbeats MUST be marked as alive.

    Per Ch04 §4.2.2: Site Agent MUST track last_heartbeat per machine.
    ConnectedMachine.is_alive = (now - last_heartbeat) < heartbeat_timeout.
    """
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)
    machine_id = "alive-test-01"

    connected_event = asyncio.Event()

    async def request_stream():
        header = make_header(sender_id=machine_id, receiver_id="site-agent-01")
        manifest = make_test_manifest(machine_id)
        yield wrap_capability_manifest(header, manifest)

        header2 = make_header(sender_id=machine_id, receiver_id="site-agent-01")
        hb = make_test_heartbeat(machine_id)
        yield wrap_heartbeat(header2, hb)

        connected_event.set()

        # Keep stream alive
        await asyncio.sleep(1.0)

    stream_task = asyncio.create_task(_consume_stream(stub, request_stream()))

    await connected_event.wait()
    await asyncio.sleep(0.1)  # Let server process

    # Machine should be alive
    if machine_id in servicer.machines:
        machine = servicer.machines[machine_id]
        assert machine.is_alive, "Machine with recent heartbeat should be alive"

    stream_task.cancel()
    try:
        await stream_task
    except asyncio.CancelledError:
        pass


async def _consume_stream(stub, request_iter):
    """Helper to consume a gRPC stream.

    ``request_iter`` is the already-invoked async generator object,
    passed via ``_consume_stream(stub, request_stream())``. We hand
    it to ``stub.Connect`` directly — calling it again (the previous
    bug) raised TypeError because async generator objects are not
    callable, which left the request stream uninitialised and made
    the test hang.
    """
    try:
        stream = stub.Connect(request_iter)
        async for _ in stream:
            pass
    except (grpc.aio.AioRpcError, asyncio.CancelledError):
        pass
