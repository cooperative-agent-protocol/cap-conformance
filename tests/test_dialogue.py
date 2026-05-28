# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CAP Authors
"""Conformance tests for Agent Dialogue Protocol.

Tests:
- Dialogue round-trip (SituationReport)
- DialogueProvenance present on all messages
- DialogueIntent correct for each type
- LocalDecision CROSS_TASK requires approval (approved=false until site responds)
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from google.protobuf.timestamp_pb2 import Timestamp

from cap.v0.core import runtime_pb2, runtime_pb2_grpc, common_pb2, dialogue_pb2, alo_pb2, events_pb2
from cap_sdk.frame import make_header, wrap_capability_manifest, wrap_agent_dialogue
from .conftest import make_test_manifest, make_test_dialogue


def _now_ts() -> Timestamp:
    ts = Timestamp()
    ts.FromDatetime(datetime.now(timezone.utc))
    return ts


@pytest.mark.asyncio
async def test_dialogue_roundtrip(servicer, server, channel):
    """Machine sends AgentDialogue with SituationReport, server receives it."""
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)

    manifest = make_test_manifest("dlg-test-01")
    mh = make_header(sender_id="dlg-test-01", receiver_id="site-agent-01")

    dialogue = make_test_dialogue(
        machine_id="dlg-test-01",
        task_id="task-dlg-01",
        intent=dialogue_pb2.DIALOGUE_INTENT_SITUATION_REPORT,
    )
    dh = make_header(sender_id="dlg-test-01", receiver_id="site-agent-01")

    async def send_msgs():
        yield wrap_capability_manifest(mh, manifest)
        await asyncio.sleep(0.2)
        yield wrap_agent_dialogue(dh, dialogue)
        await asyncio.sleep(0.5)

    stream = stub.Connect(send_msgs())
    await asyncio.sleep(0.5)
    stream.cancel()

    # Find dialogue frame
    dlg_frames = [
        (mid, f) for mid, f in servicer._received
        if f.HasField("agent_dialogue")
    ]
    assert len(dlg_frames) >= 1
    mid, frame = dlg_frames[0]
    assert mid == "dlg-test-01"

    dlg = frame.agent_dialogue
    assert dlg.dialogue_id == dialogue.dialogue_id
    assert dlg.task_id == "task-dlg-01"
    assert len(dlg.messages) == 1

    msg = dlg.messages[0]
    assert msg.intent == dialogue_pb2.DIALOGUE_INTENT_SITUATION_REPORT
    assert msg.HasField("situation_report")
    sr = msg.situation_report
    assert sr.machine_id == "dlg-test-01"
    assert sr.situation_summary == "テスト状況報告"


@pytest.mark.asyncio
async def test_dialogue_provenance_present(servicer, server, channel):
    """Every DialogueMessage has non-empty provenance."""
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)

    manifest = make_test_manifest("prov-test-01")
    mh = make_header(sender_id="prov-test-01", receiver_id="site-agent-01")

    dialogue = make_test_dialogue(
        machine_id="prov-test-01",
        task_id="task-prov-01",
    )
    dh = make_header(sender_id="prov-test-01", receiver_id="site-agent-01")

    async def send_msgs():
        yield wrap_capability_manifest(mh, manifest)
        await asyncio.sleep(0.2)
        yield wrap_agent_dialogue(dh, dialogue)
        await asyncio.sleep(0.5)

    stream = stub.Connect(send_msgs())
    await asyncio.sleep(0.5)
    stream.cancel()

    dlg_frames = [
        (mid, f) for mid, f in servicer._received
        if f.HasField("agent_dialogue")
    ]
    assert len(dlg_frames) >= 1

    for mid, frame in dlg_frames:
        dlg = frame.agent_dialogue
        for msg in dlg.messages:
            assert msg.HasField("provenance"), "DialogueMessage must have provenance"
            prov = msg.provenance
            assert prov.agent_id, "provenance.agent_id must be non-empty"
            assert prov.HasField("generated_at"), "provenance.generated_at must be set"
            assert prov.model_id, "provenance.model_id must be non-empty"


@pytest.mark.asyncio
async def test_dialogue_intent_correct(servicer, server, channel):
    """Each intent type round-trips correctly."""
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)

    manifest = make_test_manifest("intent-test-01")
    mh = make_header(sender_id="intent-test-01", receiver_id="site-agent-01")

    intents_to_test = [
        dialogue_pb2.DIALOGUE_INTENT_SITUATION_REPORT,
        dialogue_pb2.DIALOGUE_INTENT_LOCAL_DECISION,
        dialogue_pb2.DIALOGUE_INTENT_PLAN_PROPOSAL,
        dialogue_pb2.DIALOGUE_INTENT_ACKNOWLEDGEMENT,
    ]

    dialogues = []
    for intent in intents_to_test:
        dialogues.append(make_test_dialogue(
            machine_id="intent-test-01",
            task_id="task-intent-01",
            intent=intent,
        ))

    async def send_msgs():
        yield wrap_capability_manifest(mh, manifest)
        await asyncio.sleep(0.2)
        for dlg in dialogues:
            dh = make_header(sender_id="intent-test-01", receiver_id="site-agent-01")
            yield wrap_agent_dialogue(dh, dlg)
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.5)

    stream = stub.Connect(send_msgs())
    await asyncio.sleep(1.0)
    stream.cancel()

    dlg_frames = [
        f for mid, f in servicer._received
        if f.HasField("agent_dialogue")
    ]
    assert len(dlg_frames) == len(intents_to_test)

    received_intents = [f.agent_dialogue.messages[0].intent for f in dlg_frames]
    assert received_intents == intents_to_test


@pytest.mark.asyncio
async def test_local_decision_cross_task_requires_approval(servicer, server, channel):
    """LocalDecision with scope=CROSS_TASK has approved=false (until site responds)."""
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)

    manifest = make_test_manifest("cross-test-01")
    mh = make_header(sender_id="cross-test-01", receiver_id="site-agent-01")

    # Create a CROSS_TASK decision
    provenance = dialogue_pb2.DialogueProvenance(
        agent_id="cross-test-01",
        agent_role="machine_agent",
        confidence=0.7,
        model_id="fake-rule-based",
        generated_at=_now_ts(),
        reasoning_summary="Test cross-task decision",
    )
    decision = dialogue_pb2.LocalDecision(
        machine_id="cross-test-01",
        task_id="task-cross-01",
        scope=dialogue_pb2.LOCAL_DECISION_SCOPE_CROSS_TASK,
        decision_summary="複数タスクに影響する判断",
        rationale="テスト理由",
        approval_required=True,
        approved=False,  # Must remain false until site agent approves
        provenance=provenance,
    )
    msg = dialogue_pb2.DialogueMessage(
        message_id=str(uuid.uuid4()),
        intent=dialogue_pb2.DIALOGUE_INTENT_LOCAL_DECISION,
        provenance=provenance,
        timestamp=_now_ts(),
    )
    msg.local_decision.CopyFrom(decision)

    dialogue = dialogue_pb2.AgentDialogue(
        dialogue_id=str(uuid.uuid4()),
        participant_ids=["cross-test-01", "site-agent-01"],
        task_id="task-cross-01",
        messages=[msg],
        created_at=_now_ts(),
    )
    dh = make_header(sender_id="cross-test-01", receiver_id="site-agent-01")

    async def send_msgs():
        yield wrap_capability_manifest(mh, manifest)
        await asyncio.sleep(0.2)
        yield wrap_agent_dialogue(dh, dialogue)
        await asyncio.sleep(0.5)

    stream = stub.Connect(send_msgs())
    await asyncio.sleep(0.5)
    stream.cancel()

    dlg_frames = [
        f for mid, f in servicer._received
        if f.HasField("agent_dialogue")
    ]
    assert len(dlg_frames) >= 1

    frame = dlg_frames[0]
    ld = frame.agent_dialogue.messages[0].local_decision
    assert ld.scope == dialogue_pb2.LOCAL_DECISION_SCOPE_CROSS_TASK
    assert ld.approval_required is True
    assert ld.approved is False  # Not yet approved by site agent


# ─── New Dialogue Types: ExecutionCommit, SubgoalComplete, HandoverRequest ───


def _make_dialogue_with_content(machine_id, task_id, intent, content_field, content_msg):
    """Helper to build an AgentDialogue with a specific content oneof."""
    provenance = dialogue_pb2.DialogueProvenance(
        agent_id=machine_id,
        agent_role="machine_agent",
        confidence=0.9,
        model_id="fake-rule-based",
        generated_at=_now_ts(),
        reasoning_summary="Test",
    )
    msg = dialogue_pb2.DialogueMessage(
        message_id=str(uuid.uuid4()),
        intent=intent,
        provenance=provenance,
        timestamp=_now_ts(),
    )
    getattr(msg, content_field).CopyFrom(content_msg)
    return dialogue_pb2.AgentDialogue(
        dialogue_id=str(uuid.uuid4()),
        participant_ids=[machine_id, "site-agent-01"],
        task_id=task_id,
        messages=[msg],
        created_at=_now_ts(),
    )


@pytest.mark.asyncio
async def test_execution_commit_roundtrip(servicer, server, channel):
    """ExecutionCommit round-trips correctly through gRPC."""
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)
    mid = "ec-test-01"

    manifest = make_test_manifest(mid)
    mh = make_header(sender_id=mid, receiver_id="site-agent-01")

    commit = dialogue_pb2.ExecutionCommit(
        machine_id=mid,
        task_id="task-ec-01",
        commit_summary="条件充足。B3-cell-14で掘削開始する",
        conditions=["zone_clear", "reservation_granted"],
    )
    dialogue = _make_dialogue_with_content(
        mid, "task-ec-01",
        dialogue_pb2.DIALOGUE_INTENT_EXECUTION_COMMIT,
        "execution_commit", commit,
    )
    dh = make_header(sender_id=mid, receiver_id="site-agent-01")

    async def send_msgs():
        yield wrap_capability_manifest(mh, manifest)
        await asyncio.sleep(0.2)
        yield wrap_agent_dialogue(dh, dialogue)
        await asyncio.sleep(0.5)

    stream = stub.Connect(send_msgs())
    await asyncio.sleep(0.5)
    stream.cancel()

    dlg_frames = [f for _, f in servicer._received if f.HasField("agent_dialogue")]
    assert len(dlg_frames) >= 1

    msg = dlg_frames[0].agent_dialogue.messages[0]
    assert msg.intent == dialogue_pb2.DIALOGUE_INTENT_EXECUTION_COMMIT
    assert msg.HasField("execution_commit")
    ec = msg.execution_commit
    assert ec.machine_id == mid
    assert ec.commit_summary == "条件充足。B3-cell-14で掘削開始する"
    assert "zone_clear" in ec.conditions
    assert "reservation_granted" in ec.conditions


@pytest.mark.asyncio
async def test_subgoal_complete_roundtrip(servicer, server, channel):
    """SubgoalComplete round-trips correctly through gRPC."""
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)
    mid = "sg-test-01"

    manifest = make_test_manifest(mid)
    mh = make_header(sender_id=mid, receiver_id="site-agent-01")

    subgoal = dialogue_pb2.SubgoalComplete(
        machine_id=mid,
        task_id="task-sg-01",
        subgoal_description="10m³のうち4m³掘削完了",
        progress_percent=40.0,
    )
    dialogue = _make_dialogue_with_content(
        mid, "task-sg-01",
        dialogue_pb2.DIALOGUE_INTENT_SUBGOAL_COMPLETE,
        "subgoal_complete", subgoal,
    )
    dh = make_header(sender_id=mid, receiver_id="site-agent-01")

    async def send_msgs():
        yield wrap_capability_manifest(mh, manifest)
        await asyncio.sleep(0.2)
        yield wrap_agent_dialogue(dh, dialogue)
        await asyncio.sleep(0.5)

    stream = stub.Connect(send_msgs())
    await asyncio.sleep(0.5)
    stream.cancel()

    dlg_frames = [f for _, f in servicer._received if f.HasField("agent_dialogue")]
    assert len(dlg_frames) >= 1

    msg = dlg_frames[0].agent_dialogue.messages[0]
    assert msg.intent == dialogue_pb2.DIALOGUE_INTENT_SUBGOAL_COMPLETE
    assert msg.HasField("subgoal_complete")
    sg = msg.subgoal_complete
    assert sg.machine_id == mid
    assert sg.subgoal_description == "10m³のうち4m³掘削完了"
    assert sg.progress_percent == pytest.approx(40.0)


@pytest.mark.asyncio
async def test_handover_request_roundtrip(servicer, server, channel):
    """HandoverRequest round-trips correctly through gRPC."""
    stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)
    mid = "hr-test-01"

    manifest = make_test_manifest(mid)
    mh = make_header(sender_id=mid, receiver_id="site-agent-01")

    handover_req = dialogue_pb2.HandoverRequest(
        machine_id=mid,
        task_id="task-hr-01",
        reason="作業エンベロープ内に人間検出",
        severity=events_pb2.HANDOVER_SEVERITY_OPERATOR_REQUIRED,
        recommended_action="テレオペレーションに切替",
    )
    dialogue = _make_dialogue_with_content(
        mid, "task-hr-01",
        dialogue_pb2.DIALOGUE_INTENT_HANDOVER_REQUEST,
        "handover_request", handover_req,
    )
    dh = make_header(sender_id=mid, receiver_id="site-agent-01")

    async def send_msgs():
        yield wrap_capability_manifest(mh, manifest)
        await asyncio.sleep(0.2)
        yield wrap_agent_dialogue(dh, dialogue)
        await asyncio.sleep(0.5)

    stream = stub.Connect(send_msgs())
    await asyncio.sleep(0.5)
    stream.cancel()

    dlg_frames = [f for _, f in servicer._received if f.HasField("agent_dialogue")]
    assert len(dlg_frames) >= 1

    msg = dlg_frames[0].agent_dialogue.messages[0]
    assert msg.intent == dialogue_pb2.DIALOGUE_INTENT_HANDOVER_REQUEST
    assert msg.HasField("handover_request")
    hr = msg.handover_request
    assert hr.machine_id == mid
    assert hr.reason == "作業エンベロープ内に人間検出"
    assert hr.severity == events_pb2.HANDOVER_SEVERITY_OPERATOR_REQUIRED
    assert hr.recommended_action == "テレオペレーションに切替"
