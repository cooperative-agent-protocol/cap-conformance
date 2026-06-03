# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CAP Authors
"""Conformance tests for SkillInvocation (RFC-0001, Core).

CAP-Core の SkillInvocation は分野非依存のアクション・エンベロープで、domain-specific な
パラメータを google.protobuf.Any で運ぶ。Core 適合の要件:
  - 任意の Domain Pack の typed param を Any で round-trip できる (type_url 保持・payload 透過)
  - Core は domain 型を import/解釈せずに運べる (type_url 文字列のみで識別可)
  - metadata(map<string,string>) は自由記述で、typed_params(型付き) と分離されている

これにより新 Domain Pack (Agriculture / Mining / Intralogistics / ...) は CAP-Core を一切
変更せず、自分の typed param message を SkillInvocation.typed_params に差し込める。
"""

from __future__ import annotations

from cap.v0.core import skill_pb2

# 例示 domain payload として Construction Domain Pack の typed param を使う
# (Core は本来この型を知らなくてよい — それを下の domain-agnostic テストで示す)。
from cap.v0.domains.construction import skills_pb2 as construction


def test_skill_invocation_round_trips_domain_typed_params_via_any():
    """Domain の typed param を SkillInvocation.typed_params(Any) で wire round-trip できる。"""
    params = construction.ExcavateBatchParams(
        target_zone_id="dig-A", target_volume_m3=9.0, max_cycles=3)
    inv = skill_pb2.SkillInvocation(
        skill_id="construction.excavate_batch", skill_name="excavate")
    inv.typed_params.Pack(params)

    inv2 = skill_pb2.SkillInvocation.FromString(inv.SerializeToString())

    assert inv2.skill_id == "construction.excavate_batch"
    assert inv2.typed_params.Is(construction.ExcavateBatchParams.DESCRIPTOR)
    out = construction.ExcavateBatchParams()
    inv2.typed_params.Unpack(out)
    assert out.target_zone_id == "dig-A"
    assert out.target_volume_m3 == 9.0
    assert out.max_cycles == 3


def test_skill_invocation_core_is_domain_agnostic():
    """Core 受信側は domain 型を知らずとも (Unpack せずとも) skill_id と type_url を読める。

    = CAP-Core は新分野の追加で変更不要。type_url で「どの domain のどの型か」を識別できる。
    """
    params = construction.HaulRouteParams(
        source_zone_id="dig-A", dump_zone_id="dump-A", total_volume_m3=9.0)
    inv = skill_pb2.SkillInvocation(skill_id="construction.haul_route")
    inv.typed_params.Pack(params)

    inv2 = skill_pb2.SkillInvocation.FromString(inv.SerializeToString())
    # domain 型を import/Unpack せずに識別 (type_url 文字列のみ)
    assert inv2.typed_params.type_url.endswith(
        "cap.v0.domains.construction.HaulRouteParams")
    assert inv2.skill_id == "construction.haul_route"


def test_skill_invocation_metadata_is_freeform_and_separate():
    """metadata は自由記述 map<string,string> で、型付き typed_params とは別物。"""
    inv = skill_pb2.SkillInvocation(skill_id="construction.dump")
    inv.typed_params.Pack(construction.DumpParams(dump_zone_id="dump-A", volume_m3=3.0))
    inv.metadata["operator_note"] = "second cycle"

    inv2 = skill_pb2.SkillInvocation.FromString(inv.SerializeToString())
    assert inv2.metadata["operator_note"] == "second cycle"
    out = construction.DumpParams()
    inv2.typed_params.Unpack(out)
    assert out.volume_m3 == 3.0
