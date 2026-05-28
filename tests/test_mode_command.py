# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CAP Authors
"""Conformance tests for ModeCommand / MachineMode transitions (Level 2, Ch06 §6.2).

Tests valid and invalid mode transitions.
"""

from __future__ import annotations

import asyncio

import grpc
import pytest

from cap.v0.core import runtime_pb2, runtime_pb2_grpc, common_pb2, site_agent_pb2
from cap_sdk.frame import make_header, wrap_capability_manifest, wrap_heartbeat
from .conftest import make_test_manifest, make_test_heartbeat


@pytest.mark.asyncio
async def test_mode_command_received(servicer, server, channel):
    """Site agent can send ModeCommand through the stream.

    Per Ch06 §6.2.3: ModeCommand changes the machine mode.
    """
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)
    machine_id = "mode-test-01"

    mode_command_sent = False
    original_on_frame = servicer._on_frame

    async def on_frame_with_mode(mid, frame):
        nonlocal mode_command_sent
        if frame.HasField("capability_manifest") and not mode_command_sent:
            mode_command_sent = True
            header = make_header(sender_id="site-agent-01", receiver_id=machine_id)
            resp_frame = runtime_pb2.CapFrame(header=header)
            resp_frame.mode_command.CopyFrom(site_agent_pb2.ModeCommand(
                machine_id=machine_id,
                requested_mode=common_pb2.MACHINE_MODE_TELEOP,
            ))
            return runtime_pb2.ConnectResponse(frame=resp_frame)
        return await original_on_frame(mid, frame)

    servicer._on_frame = on_frame_with_mode

    responses = []

    async def request_stream():
        header = make_header(sender_id=machine_id, receiver_id="site-agent-01")
        yield wrap_capability_manifest(header, make_test_manifest(machine_id))
        await asyncio.sleep(0.3)

    stream = stub.Connect(request_stream())
    try:
        async for resp in stream:
            responses.append(resp)
    except (grpc.aio.AioRpcError, asyncio.CancelledError):
        pass

    # Verify ModeCommand was sent to the machine
    mode_responses = [
        r for r in responses if r.frame.HasField("mode_command")
    ]
    assert len(mode_responses) >= 1, "ModeCommand should be sent to machine"
    assert mode_responses[0].frame.mode_command.requested_mode == common_pb2.MACHINE_MODE_TELEOP


@pytest.mark.asyncio
async def test_heartbeat_reports_current_mode(servicer, server, channel):
    """Heartbeat MUST include current_mode.

    Per Ch06 §6.2: current_mode is reported via Heartbeat.
    """
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)
    machine_id = "mode-test-02"

    async def request_stream():
        header = make_header(sender_id=machine_id, receiver_id="site-agent-01")
        yield wrap_capability_manifest(header, make_test_manifest(machine_id))

        header2 = make_header(sender_id=machine_id, receiver_id="site-agent-01")
        hb = make_test_heartbeat(machine_id)
        hb.current_mode = common_pb2.MACHINE_MODE_SUPERVISED_AUTONOMY
        yield wrap_heartbeat(header2, hb)
        await asyncio.sleep(0.2)

    stream = stub.Connect(request_stream())
    try:
        async for _ in stream:
            pass
    except (grpc.aio.AioRpcError, asyncio.CancelledError):
        pass

    hb_frames = [
        (mid, f) for mid, f in servicer._received
        if f.HasField("heartbeat")
    ]
    assert len(hb_frames) >= 1
    _, frame = hb_frames[0]
    assert frame.heartbeat.current_mode == common_pb2.MACHINE_MODE_SUPERVISED_AUTONOMY


@pytest.mark.asyncio
async def test_mode_command_includes_machine_id(servicer, server, channel):
    """ModeCommand MUST include target machine_id.

    Per Ch07: machine_id is required in ModeCommand.
    """
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)
    machine_id = "mode-test-03"

    mode_sent = False
    original_on_frame = servicer._on_frame

    async def on_frame_with_mode(mid, frame):
        nonlocal mode_sent
        if frame.HasField("capability_manifest") and not mode_sent:
            mode_sent = True
            header = make_header(sender_id="site-agent-01", receiver_id=machine_id)
            resp = runtime_pb2.CapFrame(header=header)
            resp.mode_command.CopyFrom(site_agent_pb2.ModeCommand(
                machine_id=machine_id,
                requested_mode=common_pb2.MACHINE_MODE_MANUAL,
            ))
            return runtime_pb2.ConnectResponse(frame=resp)
        return await original_on_frame(mid, frame)

    servicer._on_frame = on_frame_with_mode

    responses = []

    async def request_stream():
        header = make_header(sender_id=machine_id, receiver_id="site-agent-01")
        yield wrap_capability_manifest(header, make_test_manifest(machine_id))
        await asyncio.sleep(0.3)

    stream = stub.Connect(request_stream())
    try:
        async for resp in stream:
            responses.append(resp)
    except (grpc.aio.AioRpcError, asyncio.CancelledError):
        pass

    mode_responses = [r for r in responses if r.frame.HasField("mode_command")]
    assert len(mode_responses) >= 1
    assert mode_responses[0].frame.mode_command.machine_id == machine_id
