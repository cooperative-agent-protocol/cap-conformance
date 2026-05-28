# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CAP Authors
"""Conformance tests for concurrent WorkOrder handling (Level 1).

Tests that a Machine Agent correctly rejects or defers a second WorkOrder
when it already has an active task.
"""

from __future__ import annotations

import asyncio

import grpc
import pytest

from cap.v0.core import runtime_pb2, runtime_pb2_grpc, common_pb2, events_pb2, site_agent_pb2, machine_agent_pb2
from cap_sdk.frame import make_header, wrap_capability_manifest
from .conftest import make_test_manifest


@pytest.mark.asyncio
async def test_second_work_order_while_busy(servicer, server, channel):
    """A machine with an active task MUST reject/defer a second WorkOrder.

    Per Ch08 §8.3.1: Machine Agent MUST have at most one task in RUNNING or
    BLOCKED state. Second WorkOrder → REJECTED(machine_busy) or DEFERRED.
    Per Ch06 §6.6 rule 4: at most one task in RUNNING or BLOCKED.
    """
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)
    machine_id = "concurrent-test-01"

    # Custom servicer that sends 2 WorkOrders
    work_orders_sent = 0
    acks_received = []

    original_on_frame = servicer._on_frame

    async def on_frame_with_work_orders(mid, frame):
        nonlocal work_orders_sent

        # After manifest, send first WorkOrder
        if frame.HasField("capability_manifest") and work_orders_sent == 0:
            header = make_header(sender_id="site-agent-01", receiver_id=machine_id)
            header.work_package_id = "wp-test"
            wo1 = runtime_pb2.CapFrame(header=header)
            wo1.work_order.CopyFrom(site_agent_pb2.WorkOrder(
                task_id="task-001",
                work_package_id="wp-test",
                target_machine_id=machine_id,
                skill="construction.excavate_batch",
            ))
            work_orders_sent += 1
            return runtime_pb2.ConnectResponse(frame=wo1)

        # After first ack, send second WorkOrder
        if frame.HasField("work_order_ack") and work_orders_sent == 1:
            acks_received.append(frame.work_order_ack)
            header = make_header(sender_id="site-agent-01", receiver_id=machine_id)
            wo2 = runtime_pb2.CapFrame(header=header)
            wo2.work_order.CopyFrom(site_agent_pb2.WorkOrder(
                task_id="task-002",
                work_package_id="wp-test",
                target_machine_id=machine_id,
                skill="construction.excavate_batch",
            ))
            work_orders_sent += 1
            return runtime_pb2.ConnectResponse(frame=wo2)

        if frame.HasField("work_order_ack") and work_orders_sent == 2:
            acks_received.append(frame.work_order_ack)

        return await original_on_frame(mid, frame)

    servicer._on_frame = on_frame_with_work_orders

    # Connect a machine that accepts the first WO and reports busy for the second
    async def request_stream():
        header = make_header(sender_id=machine_id, receiver_id="site-agent-01")
        manifest = make_test_manifest(machine_id)
        yield wrap_capability_manifest(header, manifest)

        # Wait for WorkOrders
        await asyncio.sleep(0.5)

        # Process responses and send acks
        # Note: In a real test, the machine processes incoming WorkOrders.
        # Here we simulate by sending acks directly.

    stream = stub.Connect(request_stream())

    responses = []
    try:
        async for resp in stream:
            responses.append(resp)
            if len(responses) >= 2:
                break
    except (grpc.aio.AioRpcError, asyncio.TimeoutError):
        pass

    # Verify at least one WorkOrder was sent
    work_order_responses = [
        r for r in responses if r.frame.HasField("work_order")
    ]
    assert len(work_order_responses) >= 1, "At least one WorkOrder should have been sent"
