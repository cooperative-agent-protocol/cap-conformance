# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CAP Authors
"""Conformance tests for FleetMemberState's pose uncertainty and heading reference (RFC-0002, Core).

FleetMemberState は他機の位置を流す。RFC-0002 は追加のみの 2 欄を足した:
  - position_sigma_xy_m (optional double): 送り手の推定の水平 1σ。付いていない = 報告なし (0 m ではない)
  - heading_reference (HeadingReference): pose.yaw が走行する体 (BASE) か旋回する上部 (UPPER) の向きか
Core 適合の要件:
  - 欄が無いメッセージは「報告なし」と分かる (presence なし、UNSPECIFIED)
  - 明示した 0.0 は presence ありとして往復し、「報告なし」と区別できる
  - 欄を知らない古い型は未知の欄として読み、書き直しても値を失わない (追加のみの互換)
"""

from __future__ import annotations

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

from cap.v0.core import common_pb2, world_model_pb2


def _member(**fields) -> world_model_pb2.FleetMemberState:
    m = world_model_pb2.FleetMemberState(
        machine_id="carrier-1", pose=common_pb2.Pose3D(x=1.0, y=2.0, z=0.5, yaw=0.3, frame_id="site_enu"))
    m.pose_at.FromNanoseconds(1_790_000_000_000_000_000)
    for name, value in fields.items():
        setattr(m, name, value)
    return m


def test_an_unreported_uncertainty_and_heading_read_as_not_reported():
    """古い送り手のメッセージ: σ の presence が無く、向きの種類は UNSPECIFIED (受け手は 0 m と読まない)。"""
    m = world_model_pb2.FleetMemberState.FromString(_member().SerializeToString())
    assert not m.HasField("position_sigma_xy_m")
    assert m.heading_reference == world_model_pb2.HEADING_REFERENCE_UNSPECIFIED
    assert world_model_pb2.HEADING_REFERENCE_UNSPECIFIED == 0


def test_a_reported_uncertainty_and_heading_round_trip_and_zero_is_reported():
    """報告した σ と向きの種類は往復する。明示した 0.0 も presence ありで、「報告なし」と区別できる。"""
    m = world_model_pb2.FleetMemberState.FromString(
        _member(position_sigma_xy_m=0.03, heading_reference=world_model_pb2.HEADING_REFERENCE_UPPER)
        .SerializeToString())
    assert m.HasField("position_sigma_xy_m") and m.position_sigma_xy_m == 0.03
    assert m.heading_reference == world_model_pb2.HEADING_REFERENCE_UPPER
    zero = world_model_pb2.FleetMemberState.FromString(_member(position_sigma_xy_m=0.0).SerializeToString())
    assert zero.HasField("position_sigma_xy_m") and zero.position_sigma_xy_m == 0.0
    base = world_model_pb2.FleetMemberState.FromString(
        _member(heading_reference=world_model_pb2.HEADING_REFERENCE_BASE).SerializeToString())
    assert base.heading_reference == world_model_pb2.HEADING_REFERENCE_BASE


def _old_fleet_member_type():
    """FleetMemberState as it was before RFC-0002 (fields 1-8 only), in a pool of its own."""
    new = world_model_pb2.FleetMemberState.DESCRIPTOR
    file_proto = descriptor_pb2.FileDescriptorProto()
    new.file.CopyToProto(file_proto)
    keep = []
    for msg in file_proto.message_type:
        if msg.name == "FleetMemberState":
            fields = [f for f in msg.field if f.number <= 8]
            del msg.field[:]
            msg.field.extend(fields)
            del msg.oneof_decl[:]                  # proto3 optional's synthetic oneof goes with its field
        keep.append(msg)
    pool = descriptor_pool.DescriptorPool()
    for dep in new.file.dependencies:
        dep_proto = descriptor_pb2.FileDescriptorProto()
        dep.CopyToProto(dep_proto)
        for sub in dep.dependencies:
            sub_proto = descriptor_pb2.FileDescriptorProto()
            sub.CopyToProto(sub_proto)
            try:
                pool.Add(sub_proto)
            except TypeError:
                pass                               # already added
        try:
            pool.Add(dep_proto)
        except TypeError:
            pass
    file_proto.name = "old/" + file_proto.name
    pool.Add(file_proto)
    return message_factory.GetMessageClass(pool.FindMessageTypeByName("cap.v0.core.FleetMemberState"))


def test_a_receiver_that_does_not_know_the_fields_keeps_them():
    """追加のみの互換: 欄を知らない古い型で読み (未知の欄)、書き直しても、新しい型で読めば値が残る。"""
    Old = _old_fleet_member_type()
    assert "position_sigma_xy_m" not in Old.DESCRIPTOR.fields_by_name
    wire = _member(position_sigma_xy_m=0.04, heading_reference=world_model_pb2.HEADING_REFERENCE_BASE).SerializeToString()
    old = Old.FromString(wire)
    assert old.machine_id == "carrier-1" and old.pose.yaw == 0.3
    again = world_model_pb2.FleetMemberState.FromString(old.SerializeToString())
    assert again.HasField("position_sigma_xy_m") and again.position_sigma_xy_m == 0.04
    assert again.heading_reference == world_model_pb2.HEADING_REFERENCE_BASE
