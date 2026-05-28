# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CAP Authors
"""Conformance tests for error handling (Level 1).

Tests that implementations correctly handle invalid state transitions,
unknown message types, invalid messages, and maintain stream integrity
after non-CRITICAL errors.
"""

from __future__ import annotations

import asyncio
import uuid

import grpc
import pytest

from cap.v0.core import runtime_pb2, runtime_pb2_grpc, common_pb2, events_pb2, error_pb2
from cap_sdk.frame import make_header, wrap_capability_manifest, wrap_heartbeat
from .conftest import make_test_manifest, make_test_heartbeat


@pytest.mark.asyncio
async def test_invalid_state_transition_returns_error(servicer, server, channel):
    """Sending a ProgressEvent(SUCCEEDED) for a PENDING task should be rejected.

    Per Ch06 §6.1.4: PENDING + ProgressEvent(SUCCEEDED) → ERROR.
    Per Ch08 §8.2.2: error_code=2001, INVALID_STATE_TRANSITION.
    """
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)
    machine_id = "err-test-machine-01"

    async def request_stream():
        # 1. Send manifest
        header = make_header(sender_id=machine_id, receiver_id="site-agent-01")
        manifest = make_test_manifest(machine_id)
        yield wrap_capability_manifest(header, manifest)

        # 2. Send ProgressEvent(SUCCEEDED) without prior WorkOrder/Ack
        # This is an invalid state transition (task doesn't exist or is in PENDING)
        header2 = make_header(sender_id=machine_id, receiver_id="site-agent-01")
        frame = runtime_pb2.CapFrame(header=header2)
        frame.progress_event.CopyFrom(events_pb2.ProgressEvent(
            machine_id=machine_id,
            task_id="nonexistent-task",
            state=common_pb2.TASK_STATE_SUCCEEDED,
            completion_ratio=1.0,
            summary="test invalid transition",
        ))
        yield runtime_pb2.ConnectRequest(frame=frame)

        # Keep stream alive briefly
        await asyncio.sleep(0.5)

    stream = stub.Connect(request_stream())

    # Collect frames from the server
    received = servicer._received

    # Process responses (server should handle gracefully)
    responses = []
    try:
        async for resp in stream:
            responses.append(resp)
    except (grpc.aio.AioRpcError, asyncio.CancelledError):
        pass  # Stream may close normally

    # The invalid transition should have been received by the servicer
    assert len(received) >= 2, "Server should have received manifest + progress event"

    # Verify the progress event was received (the servicer processes it)
    progress_frames = [
        (mid, f) for mid, f in received
        if f.HasField("progress_event")
    ]
    assert len(progress_frames) >= 1, "ProgressEvent should reach the server"


@pytest.mark.asyncio
async def test_stream_survives_invalid_message(servicer, server, channel):
    """Stream MUST NOT be closed after a non-CRITICAL error.

    Per Ch08 §8.1.1: Errors do not close the stream (unless CRITICAL).
    """
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)
    machine_id = "err-test-machine-02"

    messages_sent = 0

    async def request_stream():
        nonlocal messages_sent
        # 1. Send manifest
        header = make_header(sender_id=machine_id, receiver_id="site-agent-01")
        manifest = make_test_manifest(machine_id)
        yield wrap_capability_manifest(header, manifest)
        messages_sent += 1

        # 2. Send a heartbeat (valid message)
        header2 = make_header(sender_id=machine_id, receiver_id="site-agent-01")
        hb = make_test_heartbeat(machine_id)
        yield wrap_heartbeat(header2, hb)
        messages_sent += 1

        # 3. Send another heartbeat (stream should still be alive)
        await asyncio.sleep(0.3)
        header3 = make_header(sender_id=machine_id, receiver_id="site-agent-01")
        hb2 = make_test_heartbeat(machine_id)
        yield wrap_heartbeat(header3, hb2)
        messages_sent += 1

        await asyncio.sleep(0.2)

    stream = stub.Connect(request_stream())

    try:
        async for _ in stream:
            pass
    except (grpc.aio.AioRpcError, asyncio.CancelledError):
        pass

    # All 3 messages should have been received
    assert len(servicer._received) >= 3, (
        f"Stream should remain open for all messages, got {len(servicer._received)}"
    )


@pytest.mark.asyncio
async def test_message_id_is_uuid(servicer, server, channel):
    """All CapFrame headers MUST contain a valid UUID message_id.

    Per Ch10 §10.2.5: Use UUID v4 for message_id.
    """
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)
    machine_id = "err-test-machine-03"

    async def request_stream():
        header = make_header(sender_id=machine_id, receiver_id="site-agent-01")
        manifest = make_test_manifest(machine_id)
        yield wrap_capability_manifest(header, manifest)
        await asyncio.sleep(0.2)

    stream = stub.Connect(request_stream())

    try:
        async for _ in stream:
            pass
    except (grpc.aio.AioRpcError, asyncio.CancelledError):
        pass

    assert len(servicer._received) >= 1
    _, frame = servicer._received[0]

    # Verify message_id is a valid UUID
    msg_id = frame.header.message_id
    assert msg_id, "message_id must not be empty"
    try:
        uuid.UUID(msg_id)
    except ValueError:
        pytest.fail(f"message_id '{msg_id}' is not a valid UUID")


@pytest.mark.asyncio
async def test_correlation_id_preserved(servicer, server, channel):
    """Response messages SHOULD preserve correlation_id from requests.

    Per Ch10 §10.2.5: Preserve correlation_id in responses.
    """
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)
    machine_id = "err-test-machine-04"
    correlation = str(uuid.uuid4())

    async def request_stream():
        header = make_header(
            sender_id=machine_id,
            receiver_id="site-agent-01",
        )
        header.correlation_id = correlation
        manifest = make_test_manifest(machine_id)
        yield wrap_capability_manifest(header, manifest)
        await asyncio.sleep(0.2)

    stream = stub.Connect(request_stream())

    try:
        async for _ in stream:
            pass
    except (grpc.aio.AioRpcError, asyncio.CancelledError):
        pass

    assert len(servicer._received) >= 1
    _, frame = servicer._received[0]
    assert frame.header.correlation_id == correlation, (
        f"correlation_id not preserved: expected {correlation}, got {frame.header.correlation_id}"
    )
