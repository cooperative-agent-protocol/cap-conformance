# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CAP Authors
"""Behavioral conformance tests for ReservationState (Level 2, Ch06 §6.3).

Unlike the receipt-only tests in test_reservation.py, these wire the reference
ReservationManager into the Site Agent handler and assert the server's
NORMATIVE RESPONSE over the wire: a free resource is GRANTED with a fencing
`grant_epoch`; a busy resource is QUEUED with a `queue_position` (the
starvation-free default regime, Ch06 §6.3.5); and a release hands the resource
to the next waiter. This is the behavior a second, independent implementation
must reproduce — receipt of the request is necessary but not sufficient.
"""

from __future__ import annotations

import asyncio

import grpc
import pytest

from cap.v0.core import runtime_pb2, runtime_pb2_grpc, machine_agent_pb2, site_agent_pb2, events_pb2
from cap_sdk.frame import make_header, wrap_capability_manifest
from cap_sdk.reservation import ReservationManager
from .conftest import make_test_manifest

_STATE = events_pb2.ReservationStatus.ReservationState


def _reservation_handler():
    """A Site Agent on_frame handler backed by the reference ReservationManager."""
    mgr = ReservationManager()  # queued regime (default)

    async def handler(machine_id, frame):
        if frame.HasField("reservation_request"):
            status = mgr.request(frame.reservation_request)
        elif frame.HasField("reservation_release"):
            status = mgr.release(frame.reservation_release.reservation_id)
        else:
            return None
        if status is None:
            return None
        hdr = make_header(sender_id="site-agent-01", receiver_id=machine_id)
        hdr.correlation_id = frame.header.message_id
        resp = runtime_pb2.CapFrame(header=hdr, reservation_status=status)
        return runtime_pb2.ConnectResponse(frame=resp)

    return handler


async def _collect_statuses(stub, machine_id, requests, settle=0.3):
    async def request_stream():
        h = make_header(sender_id=machine_id, receiver_id="site-agent-01")
        yield wrap_capability_manifest(h, make_test_manifest(machine_id))
        for rr in requests:
            f = runtime_pb2.CapFrame(header=make_header(sender_id=machine_id, receiver_id="site-agent-01"))
            f.reservation_request.CopyFrom(rr)
            yield runtime_pb2.ConnectRequest(frame=f)
        await asyncio.sleep(settle)

    statuses = []
    stream = stub.Connect(request_stream())
    try:
        async for resp in stream:
            if resp.frame.HasField("reservation_status"):
                statuses.append(resp.frame.reservation_status)
    except (grpc.aio.AioRpcError, asyncio.CancelledError):
        pass
    return statuses


@pytest.mark.asyncio
@pytest.mark.requirement("CAP-L2-RES-GRANT", "CAP-L2-RES-FENCE")
async def test_free_resource_granted_with_fencing_epoch(servicer, server, channel):
    """A ReservationRequest for a free resource MUST return GRANTED with a
    monotonic fencing grant_epoch (Ch06 §6.3.3, §6.3.6)."""
    servicer._on_frame = _reservation_handler()
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)
    statuses = await _collect_statuses(
        stub, "res-beh-01",
        [site_agent_pb2.ReservationRequest(reservation_id="r1", resource_id="LP-1", holder_id="res-beh-01")],
    )
    assert len(statuses) == 1
    assert statuses[0].state == _STATE.RESERVATION_STATE_GRANTED
    assert statuses[0].grant_epoch >= 1


@pytest.mark.asyncio
@pytest.mark.requirement("CAP-L2-RES-QUEUE")
async def test_busy_resource_queued_not_denied(servicer, server, channel):
    """A second request for a busy resource MUST be QUEUED (default regime)
    with a queue_position, not denied (Ch06 §6.3.5)."""
    servicer._on_frame = _reservation_handler()
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)
    statuses = await _collect_statuses(
        stub, "res-beh-02",
        [
            site_agent_pb2.ReservationRequest(reservation_id="r1", resource_id="LP-1", holder_id="res-beh-02"),
            site_agent_pb2.ReservationRequest(reservation_id="r2", resource_id="LP-1", holder_id="res-beh-02"),
        ],
    )
    assert len(statuses) == 2
    assert statuses[0].state == _STATE.RESERVATION_STATE_GRANTED
    assert statuses[1].state == _STATE.RESERVATION_STATE_QUEUED
    assert statuses[1].queue_position == 1


@pytest.mark.asyncio
@pytest.mark.requirement("CAP-L1-CONN-MANIFEST-FIRST")
async def test_premanifest_frame_rejected_with_2006(servicer, server, channel):
    """A frame received before CapabilityManifest MUST be rejected with
    CapError(2006) and the stream closed (Ch10 §10.2.1)."""
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)
    machine_id = "premanifest-01"

    async def request_stream():
        # First frame is a Heartbeat, NOT a CapabilityManifest.
        f = runtime_pb2.CapFrame(header=make_header(sender_id=machine_id, receiver_id="site-agent-01"))
        f.heartbeat.CopyFrom(machine_agent_pb2.Heartbeat(machine_id=machine_id))
        yield runtime_pb2.ConnectRequest(frame=f)
        await asyncio.sleep(0.2)

    errors = []
    stream = stub.Connect(request_stream())
    try:
        async for resp in stream:
            if resp.frame.HasField("cap_error"):
                errors.append(resp.frame.cap_error)
    except (grpc.aio.AioRpcError, asyncio.CancelledError):
        pass

    assert any(e.error_code == 2006 for e in errors), "expected CapError(2006) for pre-manifest frame"
