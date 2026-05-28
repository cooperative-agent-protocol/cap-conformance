# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CAP Authors
"""Conformance Suite: Work Order Lifecycle

Validates that:
1. WorkOrder can be sent to a connected machine
2. Machine responds with WorkOrderAck
3. Machine sends ProgressEvent updates
4. Task reaches terminal state (SUCCEEDED or FAILED)
"""

from __future__ import annotations

import asyncio

import grpc
import pytest

from cap.v0.core import (
    runtime_pb2,
    runtime_pb2_grpc,
    common_pb2,
    site_agent_pb2,
    machine_agent_pb2,
    events_pb2,
)
from cap_sdk.frame import (
    make_header,
    wrap_capability_manifest,
    wrap_work_order_ack,
    wrap_progress_event,
)
from .conftest import make_test_manifest

MACHINE_ID = "test-excavator-wol"


@pytest.mark.asyncio
async def test_work_order_ack_accepted(servicer, server, channel):
    """Machine should ACK a work order with ACCEPTED."""
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)

    work_order = site_agent_pb2.WorkOrder(
        task_id="task-test-001",
        work_package_id="wp-test",
        target_machine_id=MACHINE_ID,
        skill="construction.excavate_batch",
        target_zone=common_pb2.GeoZoneRef(
            map_id="test", zone_id="zone-1", revision="r1"
        ),
        priority=5,
    )

    received_responses: list[runtime_pb2.CapFrame] = []

    async def on_frame_with_wo(machine_id, frame):
        if frame.HasField("capability_manifest"):
            header = make_header(
                sender_id="site-agent-01",
                receiver_id=machine_id,
                work_package_id="wp-test",
            )
            resp_frame = runtime_pb2.CapFrame(header=header, work_order=work_order)
            return runtime_pb2.ConnectResponse(frame=resp_frame)
        if frame.HasField("work_order_ack"):
            servicer._received.append((machine_id, frame))
        return None

    servicer._on_frame = on_frame_with_wo

    async def request_iter():
        manifest = make_test_manifest(MACHINE_ID)
        header = make_header(sender_id=MACHINE_ID, receiver_id="site-agent-01")
        yield wrap_capability_manifest(header, manifest)
        await asyncio.sleep(0.5)

        ack = machine_agent_pb2.WorkOrderAck(
            task_id="task-test-001",
            decision=machine_agent_pb2.WorkOrderAck.DECISION_ACCEPTED,
        )
        header = make_header(
            sender_id=MACHINE_ID,
            receiver_id="site-agent-01",
            work_package_id="wp-test",
        )
        yield wrap_work_order_ack(header, ack)
        await asyncio.sleep(0.3)

    stream = stub.Connect(request_iter())
    try:
        async for resp in stream:
            received_responses.append(resp.frame)
    except (grpc.aio.AioRpcError, asyncio.CancelledError):
        pass

    wo_frames = [f for f in received_responses if f.HasField("work_order")]
    assert len(wo_frames) >= 1
    assert wo_frames[0].work_order.task_id == "task-test-001"
    assert wo_frames[0].work_order.skill == "construction.excavate_batch"


@pytest.mark.asyncio
async def test_work_order_reject(servicer, server, channel):
    """Machine should be able to REJECT a work order."""
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)

    work_order = site_agent_pb2.WorkOrder(
        task_id="task-test-002",
        work_package_id="wp-test",
        target_machine_id=MACHINE_ID,
        skill="unknown_skill",
        priority=1,
    )

    async def on_frame_with_wo(machine_id, frame):
        if frame.HasField("capability_manifest"):
            header = make_header(
                sender_id="site-agent-01",
                receiver_id=machine_id,
                work_package_id="wp-test",
            )
            resp_frame = runtime_pb2.CapFrame(header=header, work_order=work_order)
            return runtime_pb2.ConnectResponse(frame=resp_frame)
        if frame.HasField("work_order_ack"):
            servicer._received.append((machine_id, frame))
        return None

    servicer._on_frame = on_frame_with_wo

    async def request_iter():
        manifest = make_test_manifest(MACHINE_ID)
        header = make_header(sender_id=MACHINE_ID, receiver_id="site-agent-01")
        yield wrap_capability_manifest(header, manifest)
        await asyncio.sleep(0.5)

        ack = machine_agent_pb2.WorkOrderAck(
            task_id="task-test-002",
            decision=machine_agent_pb2.WorkOrderAck.DECISION_REJECTED,
            reason="Unsupported skill: unknown_skill",
        )
        header = make_header(
            sender_id=MACHINE_ID,
            receiver_id="site-agent-01",
            work_package_id="wp-test",
        )
        yield wrap_work_order_ack(header, ack)
        await asyncio.sleep(0.3)

    received_responses = []
    stream = stub.Connect(request_iter())
    try:
        async for resp in stream:
            received_responses.append(resp.frame)
    except (grpc.aio.AioRpcError, asyncio.CancelledError):
        pass

    assert any(f.HasField("work_order") for f in received_responses)

    acks = [f for _, f in servicer._received if f.HasField("work_order_ack")]
    assert len(acks) >= 1
    assert acks[0].work_order_ack.decision == machine_agent_pb2.WorkOrderAck.DECISION_REJECTED


@pytest.mark.asyncio
async def test_progress_to_succeeded(servicer, server, channel):
    """Machine should report progress and reach SUCCEEDED state."""
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)

    async def on_frame_capture(machine_id, frame):
        servicer._received.append((machine_id, frame))
        return None

    servicer._on_frame = on_frame_capture

    async def request_iter():
        manifest = make_test_manifest(MACHINE_ID)
        header = make_header(sender_id=MACHINE_ID, receiver_id="site-agent-01")
        yield wrap_capability_manifest(header, manifest)
        await asyncio.sleep(0.2)

        header = make_header(
            sender_id=MACHINE_ID,
            receiver_id="site-agent-01",
            work_package_id="wp-test",
        )
        progress = events_pb2.ProgressEvent(
            machine_id=MACHINE_ID,
            task_id="task-test-003",
            state=common_pb2.TASK_STATE_RUNNING,
            completion_ratio=0.5,
            summary="Half done",
        )
        yield wrap_progress_event(header, progress)
        await asyncio.sleep(0.2)

        header = make_header(
            sender_id=MACHINE_ID,
            receiver_id="site-agent-01",
            work_package_id="wp-test",
        )
        progress = events_pb2.ProgressEvent(
            machine_id=MACHINE_ID,
            task_id="task-test-003",
            state=common_pb2.TASK_STATE_SUCCEEDED,
            completion_ratio=1.0,
            summary="Done",
        )
        yield wrap_progress_event(header, progress)
        await asyncio.sleep(0.3)

    stream = stub.Connect(request_iter())
    try:
        async for _ in stream:
            pass
    except (grpc.aio.AioRpcError, asyncio.CancelledError):
        pass

    progress_frames = [
        f for _, f in servicer._received if f.HasField("progress_event")
    ]
    assert len(progress_frames) >= 2

    assert progress_frames[0].progress_event.state == common_pb2.TASK_STATE_RUNNING
    assert progress_frames[0].progress_event.completion_ratio == 0.5

    assert progress_frames[-1].progress_event.state == common_pb2.TASK_STATE_SUCCEEDED
    assert progress_frames[-1].progress_event.completion_ratio == 1.0
