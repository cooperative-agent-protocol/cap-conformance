# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CAP Authors
"""Conformance tests for DialogueFlow edge cases (Level 3, Ch06 §6.5).

Tests approval rules, LocalDecision scope enforcement, and dialogue round-trips.
"""

from __future__ import annotations

import asyncio
import uuid

import grpc
import pytest

from cap.v0.core import runtime_pb2, runtime_pb2_grpc, common_pb2, dialogue_pb2
from cap_sdk.frame import make_header, wrap_capability_manifest, wrap_agent_dialogue
from .conftest import make_test_manifest, make_test_dialogue


@pytest.mark.asyncio
async def test_local_decision_scope_task_requires_approval(servicer, server, channel):
    """LocalDecision with scope=TASK MUST NOT be executed without approval.

    Per Ch06 §6.5.3: Machine MUST NOT execute until approved=true.
    """
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)
    machine_id = "dial-edge-01"

    async def request_stream():
        header = make_header(sender_id=machine_id, receiver_id="site-agent-01")
        yield wrap_capability_manifest(header, make_test_manifest(machine_id))

        # Send LocalDecision with scope=TASK and approval_required=True
        dialogue = make_test_dialogue(
            machine_id=machine_id,
            task_id="task-dial-01",
            intent=dialogue_pb2.DIALOGUE_INTENT_LOCAL_DECISION,
        )
        header2 = make_header(sender_id=machine_id, receiver_id="site-agent-01")
        yield wrap_agent_dialogue(header2, dialogue)
        await asyncio.sleep(0.3)

    stream = stub.Connect(request_stream())
    try:
        async for _ in stream:
            pass
    except (grpc.aio.AioRpcError, asyncio.CancelledError):
        pass

    dialogue_frames = [
        (mid, f) for mid, f in servicer._received
        if f.HasField("agent_dialogue")
    ]
    assert len(dialogue_frames) >= 1, "AgentDialogue should be received"
    _, frame = dialogue_frames[0]

    # Verify the dialogue has LOCAL_DECISION intent
    assert len(frame.agent_dialogue.messages) >= 1
    msg = frame.agent_dialogue.messages[0]
    assert msg.intent == dialogue_pb2.DIALOGUE_INTENT_LOCAL_DECISION
    assert msg.HasField("local_decision")
    assert msg.local_decision.scope == dialogue_pb2.LOCAL_DECISION_SCOPE_TASK
    assert msg.local_decision.approval_required is True
    assert msg.local_decision.approved is False  # Not yet approved


@pytest.mark.asyncio
async def test_local_decision_scope_cross_task_requires_approval(servicer, server, channel):
    """LocalDecision with scope=CROSS_TASK MUST require approval.

    Per Ch06 §6.5.3: TASK and CROSS_TASK scope require approval.
    """
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)
    machine_id = "dial-edge-02"

    provenance = dialogue_pb2.DialogueProvenance(
        agent_id=machine_id,
        agent_role="machine_agent",
        confidence=0.85,
        model_id="fake-rule-based",
        reasoning_summary="Cross-task coordination needed",
    )

    msg = dialogue_pb2.DialogueMessage(
        message_id=str(uuid.uuid4()),
        intent=dialogue_pb2.DIALOGUE_INTENT_LOCAL_DECISION,
        provenance=provenance,
        local_decision=dialogue_pb2.LocalDecision(
            machine_id=machine_id,
            task_id="task-dial-02",
            scope=dialogue_pb2.LOCAL_DECISION_SCOPE_CROSS_TASK,
            decision_summary="Need to coordinate with dump truck",
            rationale="Load point congestion detected",
            approval_required=True,
            approved=False,
            provenance=provenance,
        ),
    )

    dialogue = dialogue_pb2.AgentDialogue(
        dialogue_id=str(uuid.uuid4()),
        participant_ids=[machine_id, "site-agent-01"],
        task_id="task-dial-02",
        messages=[msg],
    )

    async def request_stream():
        header = make_header(sender_id=machine_id, receiver_id="site-agent-01")
        yield wrap_capability_manifest(header, make_test_manifest(machine_id))

        header2 = make_header(sender_id=machine_id, receiver_id="site-agent-01")
        yield wrap_agent_dialogue(header2, dialogue)
        await asyncio.sleep(0.3)

    stream = stub.Connect(request_stream())
    try:
        async for _ in stream:
            pass
    except (grpc.aio.AioRpcError, asyncio.CancelledError):
        pass

    dialogue_frames = [
        (mid, f) for mid, f in servicer._received
        if f.HasField("agent_dialogue")
    ]
    assert len(dialogue_frames) >= 1
    _, frame = dialogue_frames[0]
    msg = frame.agent_dialogue.messages[0]
    assert msg.local_decision.scope == dialogue_pb2.LOCAL_DECISION_SCOPE_CROSS_TASK
    assert msg.local_decision.approval_required is True


@pytest.mark.asyncio
async def test_plan_proposal_round_trip(servicer, server, channel):
    """PlanProposal can be sent and received.

    Per Ch06 §6.5.2: PlanProposal requires approval before execution.
    """
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)
    machine_id = "dial-edge-03"

    dialogue = make_test_dialogue(
        machine_id=machine_id,
        task_id="task-dial-03",
        intent=dialogue_pb2.DIALOGUE_INTENT_PLAN_PROPOSAL,
    )

    async def request_stream():
        header = make_header(sender_id=machine_id, receiver_id="site-agent-01")
        yield wrap_capability_manifest(header, make_test_manifest(machine_id))

        header2 = make_header(sender_id=machine_id, receiver_id="site-agent-01")
        yield wrap_agent_dialogue(header2, dialogue)
        await asyncio.sleep(0.3)

    stream = stub.Connect(request_stream())
    try:
        async for _ in stream:
            pass
    except (grpc.aio.AioRpcError, asyncio.CancelledError):
        pass

    dialogue_frames = [
        (mid, f) for mid, f in servicer._received
        if f.HasField("agent_dialogue")
    ]
    assert len(dialogue_frames) >= 1
    _, frame = dialogue_frames[0]
    msg = frame.agent_dialogue.messages[0]
    assert msg.intent == dialogue_pb2.DIALOGUE_INTENT_PLAN_PROPOSAL
    assert msg.HasField("plan_proposal")


@pytest.mark.asyncio
async def test_dialogue_provenance_has_model_id(servicer, server, channel):
    """DialogueProvenance MUST include model_id.

    Per Ch06 §6.5 + Ch09 §9.4: provenance enables non-repudiation.
    """
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)
    machine_id = "dial-edge-04"

    dialogue = make_test_dialogue(
        machine_id=machine_id,
        task_id="task-dial-04",
        intent=dialogue_pb2.DIALOGUE_INTENT_SITUATION_REPORT,
    )

    async def request_stream():
        header = make_header(sender_id=machine_id, receiver_id="site-agent-01")
        yield wrap_capability_manifest(header, make_test_manifest(machine_id))

        header2 = make_header(sender_id=machine_id, receiver_id="site-agent-01")
        yield wrap_agent_dialogue(header2, dialogue)
        await asyncio.sleep(0.2)

    stream = stub.Connect(request_stream())
    try:
        async for _ in stream:
            pass
    except (grpc.aio.AioRpcError, asyncio.CancelledError):
        pass

    dialogue_frames = [
        (mid, f) for mid, f in servicer._received
        if f.HasField("agent_dialogue")
    ]
    assert len(dialogue_frames) >= 1
    _, frame = dialogue_frames[0]
    msg = frame.agent_dialogue.messages[0]
    assert msg.provenance.model_id != "", "model_id must not be empty"
    assert msg.provenance.agent_id == machine_id
    assert msg.provenance.agent_role == "machine_agent"
    assert 0.0 <= msg.provenance.confidence <= 1.0


@pytest.mark.asyncio
async def test_situation_report_no_approval_required(servicer, server, channel):
    """SITUATION_REPORT intent does NOT require approval.

    Per Ch06 §6.5.2: SITUATION_REPORT → ACKNOWLEDGEMENT (no approval).
    """
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)
    machine_id = "dial-edge-05"

    dialogue = make_test_dialogue(
        machine_id=machine_id,
        task_id="task-dial-05",
        intent=dialogue_pb2.DIALOGUE_INTENT_SITUATION_REPORT,
    )

    async def request_stream():
        header = make_header(sender_id=machine_id, receiver_id="site-agent-01")
        yield wrap_capability_manifest(header, make_test_manifest(machine_id))

        header2 = make_header(sender_id=machine_id, receiver_id="site-agent-01")
        yield wrap_agent_dialogue(header2, dialogue)
        await asyncio.sleep(0.2)

    stream = stub.Connect(request_stream())
    try:
        async for _ in stream:
            pass
    except (grpc.aio.AioRpcError, asyncio.CancelledError):
        pass

    dialogue_frames = [
        (mid, f) for mid, f in servicer._received
        if f.HasField("agent_dialogue")
    ]
    assert len(dialogue_frames) >= 1
    _, frame = dialogue_frames[0]
    msg = frame.agent_dialogue.messages[0]
    assert msg.intent == dialogue_pb2.DIALOGUE_INTENT_SITUATION_REPORT
    # Situation reports have no approval_required field (it's in LocalDecision only)
    assert msg.HasField("situation_report")
