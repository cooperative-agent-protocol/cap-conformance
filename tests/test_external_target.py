# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CAP Authors
"""External-target conformance profile.

These tests run ONLY under ``--target grpc://host:port`` (they self-skip in
default in-process mode, so the published in-process result is unchanged).
They act as CAP machine-agent *clients* against a black-box external runtime
and assert only client-observable behaviour over the ``Connect`` bidi stream:
stream acceptance, heartbeat survival, reservation arbitration, AgentDialogue
relay, and acceptance of safety/handover/progress frames.

Two classes of evidence, kept separate in the report:
  * shared-SDK acceptance  -- exercises the ``cap_sdk`` servicer as deployed
    (manifest/heartbeat/stream survival/frame acceptance);
  * site-handler behaviour -- exercises the target's own ``on_frame`` logic
    (reservation grant/deny/release/regrant, dialogue relay).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from cap.v0.core import runtime_pb2, runtime_pb2_grpc, common_pb2, events_pb2
from cap_sdk.frame import (
    make_header,
    wrap_capability_manifest,
    wrap_heartbeat,
    wrap_reservation_request,
    wrap_reservation_release,
    wrap_safety_event,
    wrap_handover_event,
    wrap_progress_event,
)
from .conftest import make_test_manifest, make_test_heartbeat, make_test_dialogue


class MachineSession:
    """Drives one CAP ``Connect`` bidi stream as a machine-agent client.

    Outbound frames are pushed through an async queue; inbound frames are
    collected by a background reader task. ``recv_frame`` waits for the next
    inbound frame matching an optional predicate.
    """

    def __init__(self, channel, machine_id: str):
        self._stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)
        self.machine_id = machine_id
        self._out: asyncio.Queue = asyncio.Queue()
        self._in: asyncio.Queue = asyncio.Queue()
        self._closed = False
        self._call = None
        self._reader = None

    async def _requests(self):
        while True:
            frame = await self._out.get()
            if frame is None:
                return
            yield frame

    async def start(self):
        self._call = self._stub.Connect(self._requests())

        async def _read():
            try:
                async for resp in self._call:
                    await self._in.put(resp.frame)
            except asyncio.CancelledError:
                raise
            except Exception:  # stream closed by server
                pass

        self._reader = asyncio.create_task(_read())

    async def send(self, connect_request):
        # cap_sdk wrap_* helpers return ConnectRequest; forward as-is.
        await self._out.put(connect_request)

    async def send_manifest(self, **kw):
        await self.send(wrap_capability_manifest(
            make_header(sender_id=self.machine_id, receiver_id="site-agent-01"),
            make_test_manifest(self.machine_id), **kw))

    async def send_heartbeat(self):
        await self.send(wrap_heartbeat(
            make_header(sender_id=self.machine_id, receiver_id="site-agent-01"),
            make_test_heartbeat(self.machine_id)))

    async def recv_frame(self, predicate=None, timeout=5.0):
        """Return the next inbound frame (optionally matching predicate)."""
        async def _pull():
            while True:
                frame = await self._in.get()
                if predicate is None or predicate(frame):
                    return frame
        return await asyncio.wait_for(_pull(), timeout=timeout)

    def stream_alive(self) -> bool:
        return self._reader is not None and not self._reader.done()

    async def close(self):
        if self._closed:
            return
        self._closed = True
        await self._out.put(None)
        if self._reader:
            self._reader.cancel()
            try:
                await self._reader
            except (asyncio.CancelledError, Exception):
                pass


async def _open(channel, machine_id, *, manifest=True):
    s = MachineSession(channel, machine_id)
    await s.start()
    if manifest:
        await s.send_manifest()
        await s.send_heartbeat()
        await asyncio.sleep(0.1)
    return s


def _is_status(frame):
    return frame.HasField("reservation_status")


def _rid(prefix: str) -> str:
    """A resource id unique to this test invocation.

    The external target holds reservation state for the lifetime of the
    process (and across pytest invocations against the same long-lived
    target), so each test must use a fresh resource id to stay idempotent.
    """
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _resreq(machine_id, resource_id, reservation_id=None):
    return wrap_reservation_request(
        make_header(sender_id=machine_id, receiver_id="site-agent-01"),
        __import__("cap.v0.core.site_agent_pb2", fromlist=["ReservationRequest"])
        .ReservationRequest(
            reservation_id=reservation_id or str(uuid.uuid4()),
            resource_id=resource_id,
            holder_id=machine_id,
            reason="conformance external-target probe",
        ),
    )


# ─── shared-SDK acceptance ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_target_manifest_accepted_stream_open(target_channel):
    s = await _open(target_channel, "ext-m1")
    await s.send_heartbeat()
    await asyncio.sleep(0.2)
    assert s.stream_alive()
    await s.close()


@pytest.mark.asyncio
async def test_target_heartbeat_stream_survives(target_channel):
    s = await _open(target_channel, "ext-hb")
    for _ in range(5):
        await s.send_heartbeat()
        await asyncio.sleep(0.05)
    assert s.stream_alive()
    await s.close()


@pytest.mark.asyncio
async def test_target_stream_survives_unusual_frames(target_channel):
    """Progress for an unknown task + a safety event must not kill the stream
    (Ch08 §8.1.1: malformed/unexpected frames keep the stream open)."""
    s = await _open(target_channel, "ext-odd")
    await s.send(wrap_progress_event(
        make_header(sender_id="ext-odd", receiver_id="site-agent-01"),
        events_pb2.ProgressEvent(task_id="no-such-task", machine_id="ext-odd",
                                 state=common_pb2.TASK_STATE_RUNNING,
                                 completion_ratio=0.5, summary="orphan progress")))
    await s.send(wrap_safety_event(
        make_header(sender_id="ext-odd", receiver_id="site-agent-01"),
        events_pb2.SafetyEvent(machine_id="ext-odd", event_type="TEST",
                               summary="probe", motion_inhibited=False)))
    await s.send_heartbeat()
    await asyncio.sleep(0.2)
    assert s.stream_alive()
    await s.close()


@pytest.mark.asyncio
async def test_target_safety_and_handover_accepted(target_channel):
    s = await _open(target_channel, "ext-sh")
    await s.send(wrap_handover_event(
        make_header(sender_id="ext-sh", receiver_id="site-agent-01"),
        events_pb2.HandoverEvent(machine_id="ext-sh", task_id="t-ho",
                                 requested_mode=common_pb2.MACHINE_MODE_TELEOP,
                                 reason="probe", operator_required=True)))
    await s.send_heartbeat()
    await asyncio.sleep(0.2)
    assert s.stream_alive()
    await s.close()


@pytest.mark.asyncio
async def test_target_reconnect_same_machine_id(target_channel):
    s1 = await _open(target_channel, "ext-rc")
    await s1.close()
    await asyncio.sleep(0.1)
    s2 = await _open(target_channel, "ext-rc")
    await s2.send_heartbeat()
    await asyncio.sleep(0.2)
    assert s2.stream_alive()
    await s2.close()


# ─── site-handler behaviour (reservation arbitration) ───────────────────

@pytest.mark.asyncio
async def test_target_reservation_granted_when_free(target_channel):
    s = await _open(target_channel, "ext-r1")
    rid_free = _rid("zone-free")
    await s.send(_resreq("ext-r1", rid_free))
    st = await s.recv_frame(_is_status)
    assert st.reservation_status.state == events_pb2.ReservationStatus.RESERVATION_STATE_GRANTED
    await s.close()


@pytest.mark.asyncio
async def test_target_reservation_denied_on_conflict(target_channel):
    a = await _open(target_channel, "ext-a")
    b = await _open(target_channel, "ext-b")
    rsrc = _rid("zone-shared")
    await a.send(_resreq("ext-a", rsrc))
    sta = await a.recv_frame(_is_status)
    assert sta.reservation_status.state == events_pb2.ReservationStatus.RESERVATION_STATE_GRANTED
    await b.send(_resreq("ext-b", rsrc))
    stb = await b.recv_frame(_is_status)
    assert stb.reservation_status.state == events_pb2.ReservationStatus.RESERVATION_STATE_DENIED
    await a.close()
    await b.close()


@pytest.mark.asyncio
async def test_target_reservation_release_and_regrant(target_channel):
    a = await _open(target_channel, "ext-rel-a")
    rid = str(uuid.uuid4())
    rsrc = _rid("zone-rg")
    await a.send(_resreq("ext-rel-a", rsrc, reservation_id=rid))
    st = await a.recv_frame(_is_status)
    assert st.reservation_status.state == events_pb2.ReservationStatus.RESERVATION_STATE_GRANTED
    # Release, then a second machine can be granted the same resource.
    await a.send(wrap_reservation_release(
        make_header(sender_id="ext-rel-a", receiver_id="site-agent-01"),
        __import__("cap.v0.core.site_agent_pb2", fromlist=["ReservationRelease"])
        .ReservationRelease(reservation_id=rid, reason="done")))
    rel = await a.recv_frame(_is_status)
    assert rel.reservation_status.state == events_pb2.ReservationStatus.RESERVATION_STATE_RELEASED
    b = await _open(target_channel, "ext-rel-b")
    await b.send(_resreq("ext-rel-b", rsrc))
    stb = await b.recv_frame(_is_status)
    assert stb.reservation_status.state == events_pb2.ReservationStatus.RESERVATION_STATE_GRANTED
    await a.close()
    await b.close()


# ─── site-handler behaviour (dialogue relay) ────────────────────────────

@pytest.mark.asyncio
async def test_target_dialogue_relayed_to_peer(target_channel):
    """A's dialogue naming [A, B] is relayed to B; B's outbox flushes on its
    next inbound frame, so B sends a heartbeat to receive the relayed frame."""
    a = await _open(target_channel, "ext-dlg-a")
    b = await _open(target_channel, "ext-dlg-b")
    dlg = make_test_dialogue("ext-dlg-a", task_id="t-rel")
    dlg.participant_ids[:] = ["ext-dlg-a", "ext-dlg-b"]
    await a.send(runtime_pb2.ConnectRequest(frame=runtime_pb2.CapFrame(
        header=make_header(sender_id="ext-dlg-a", receiver_id="ext-dlg-b"),
        agent_dialogue=dlg)))
    await asyncio.sleep(0.1)
    # Flush B's outbox with a heartbeat, then read the relayed dialogue.
    await b.send_heartbeat()
    frame = await b.recv_frame(lambda f: f.HasField("agent_dialogue"), timeout=5.0)
    assert frame.agent_dialogue.task_id == "t-rel"
    await a.close()
    await b.close()


@pytest.mark.asyncio
async def test_target_dialogue_to_absent_peer_keeps_sender_alive(target_channel):
    """Naming a participant that is not connected must not crash the sender's
    stream (a relay to an unknown machine should be tolerated)."""
    a = await _open(target_channel, "ext-dlg-solo")
    dlg = make_test_dialogue("ext-dlg-solo", task_id="t-absent")
    dlg.participant_ids[:] = ["ext-dlg-solo", "ext-not-connected"]
    await a.send(runtime_pb2.ConnectRequest(frame=runtime_pb2.CapFrame(
        header=make_header(sender_id="ext-dlg-solo", receiver_id="ext-not-connected"),
        agent_dialogue=dlg)))
    await a.send_heartbeat()
    await asyncio.sleep(0.3)
    assert a.stream_alive()
    await a.close()
