# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CAP Authors
"""Conformance tests for HandoverEvent flow (Level 2, Ch06 §6.4).

Tests the complete handover negotiation flow and timeout behavior.
"""

from __future__ import annotations

import asyncio

import grpc
import pytest

from cap.v0.core import runtime_pb2, runtime_pb2_grpc, common_pb2, events_pb2
from cap_sdk.frame import make_header, wrap_capability_manifest, wrap_handover_event
from .conftest import make_test_manifest


@pytest.mark.asyncio
async def test_handover_event_received(servicer, server, channel):
    """Machine sends HandoverEvent; site agent receives it.

    Per Ch06 §6.4: Machine detects condition → sends HandoverEvent.
    """
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)
    machine_id = "handover-test-01"

    async def request_stream():
        header = make_header(sender_id=machine_id, receiver_id="site-agent-01")
        yield wrap_capability_manifest(header, make_test_manifest(machine_id))

        header2 = make_header(sender_id=machine_id, receiver_id="site-agent-01")
        yield wrap_handover_event(header2, events_pb2.HandoverEvent(
            task_id="task-ho-01",
            machine_id=machine_id,
            requested_mode=common_pb2.MACHINE_MODE_TELEOP,
            reason="obstacle detected in zone A",
            operator_required=True,
        ))
        await asyncio.sleep(0.3)

    stream = stub.Connect(request_stream())
    try:
        async for _ in stream:
            pass
    except (grpc.aio.AioRpcError, asyncio.CancelledError):
        pass

    ho_frames = [
        (mid, f) for mid, f in servicer._received
        if f.HasField("handover_event")
    ]
    assert len(ho_frames) >= 1, "HandoverEvent should be received"
    _, frame = ho_frames[0]
    assert frame.handover_event.requested_mode == common_pb2.MACHINE_MODE_TELEOP
    assert frame.handover_event.reason == "obstacle detected in zone A"
    assert frame.handover_event.operator_required is True


@pytest.mark.asyncio
async def test_handover_includes_task_context(servicer, server, channel):
    """HandoverEvent MUST include task_id and machine_id.

    Per Ch07: these are required fields for traceability.
    """
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)
    machine_id = "handover-test-02"

    async def request_stream():
        header = make_header(sender_id=machine_id, receiver_id="site-agent-01")
        yield wrap_capability_manifest(header, make_test_manifest(machine_id))

        header2 = make_header(sender_id=machine_id, receiver_id="site-agent-01")
        yield wrap_handover_event(header2, events_pb2.HandoverEvent(
            task_id="task-ho-02",
            machine_id=machine_id,
            requested_mode=common_pb2.MACHINE_MODE_TELEOP,
            reason="human detected",
        ))
        await asyncio.sleep(0.2)

    stream = stub.Connect(request_stream())
    try:
        async for _ in stream:
            pass
    except (grpc.aio.AioRpcError, asyncio.CancelledError):
        pass

    ho_frames = [
        (mid, f) for mid, f in servicer._received
        if f.HasField("handover_event")
    ]
    assert len(ho_frames) >= 1
    _, frame = ho_frames[0]
    assert frame.handover_event.task_id == "task-ho-02"
    assert frame.handover_event.machine_id == machine_id


@pytest.mark.asyncio
async def test_safety_event_received(servicer, server, channel):
    """SafetyEvent from machine is received by site agent.

    Per Ch06 §6.2.3: Safety Supervisor can trigger SAFE_STOP.
    """
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)
    machine_id = "safety-test-01"
    done = asyncio.Event()

    async def request_stream():
        header = make_header(sender_id=machine_id, receiver_id="site-agent-01")
        yield wrap_capability_manifest(header, make_test_manifest(machine_id))

        header2 = make_header(sender_id=machine_id, receiver_id="site-agent-01")
        frame = runtime_pb2.CapFrame(header=header2)
        frame.safety_event.CopyFrom(events_pb2.SafetyEvent(
            machine_id=machine_id,
            event_type="geofence_violation",
            summary="Entered keepout zone K-01",
            severity=common_pb2.FAULT_SEVERITY_CRITICAL,
            motion_inhibited=True,
        ))
        yield runtime_pb2.ConnectRequest(frame=frame)

        # Keep stream open until we've verified server received it
        await done.wait()

    stream = stub.Connect(request_stream())

    async def drain():
        try:
            async for _ in stream:
                pass
        except (grpc.aio.AioRpcError, asyncio.CancelledError):
            pass

    drain_task = asyncio.create_task(drain())

    # Wait for server to process frames
    for _ in range(20):
        await asyncio.sleep(0.05)
        safety_frames = [
            (mid, f) for mid, f in servicer._received
            if f.HasField("safety_event")
        ]
        if safety_frames:
            break

    done.set()
    await asyncio.sleep(0.1)
    drain_task.cancel()
    try:
        await drain_task
    except asyncio.CancelledError:
        pass

    assert len(safety_frames) >= 1, "SafetyEvent should be received"
    _, frame = safety_frames[0]
    assert frame.safety_event.severity == common_pb2.FAULT_SEVERITY_CRITICAL


@pytest.mark.asyncio
async def test_fault_event_received(servicer, server, channel):
    """FaultEvent from machine is received by site agent.

    Per Ch08: fault events propagate through the stream.
    """
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)
    machine_id = "fault-test-01"
    done = asyncio.Event()

    async def request_stream():
        header = make_header(sender_id=machine_id, receiver_id="site-agent-01")
        yield wrap_capability_manifest(header, make_test_manifest(machine_id))

        header2 = make_header(sender_id=machine_id, receiver_id="site-agent-01")
        frame = runtime_pb2.CapFrame(header=header2)
        frame.fault_event.CopyFrom(events_pb2.FaultEvent(
            machine_id=machine_id,
            fault_code="hydraulic_pressure_low",
            summary="Hydraulic pressure below threshold",
            severity=common_pb2.FAULT_SEVERITY_WARNING,
        ))
        yield runtime_pb2.ConnectRequest(frame=frame)

        await done.wait()

    stream = stub.Connect(request_stream())

    async def drain():
        try:
            async for _ in stream:
                pass
        except (grpc.aio.AioRpcError, asyncio.CancelledError):
            pass

    drain_task = asyncio.create_task(drain())

    for _ in range(20):
        await asyncio.sleep(0.05)
        fault_frames = [
            (mid, f) for mid, f in servicer._received
            if f.HasField("fault_event")
        ]
        if fault_frames:
            break

    done.set()
    await asyncio.sleep(0.1)
    drain_task.cancel()
    try:
        await drain_task
    except asyncio.CancelledError:
        pass

    assert len(fault_frames) >= 1, "FaultEvent should be received"
