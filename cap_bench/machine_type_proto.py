# Copyright 2026 CAP Authors
# SPDX-License-Identifier: Apache-2.0
"""machine_type enum adapter for cap-conformance ``MachineSpec.type``.

cap-conformance ``MachineSpec`` (schema.py) is a *scenario instance*
declaration — id / pose / fuel / payload — distinct from the
physical-spec ``cap.v0.MachineSpec``. They overlap on the
``machine_type`` axis only, so this adapter binds the conformance
Literal to the proto enum without merging the schemas.

``MACHINE_TYPE_CARRIER`` is included so the conformance catalog and
the SSOT proto can interchange records without lossy conversion.

The mapping is kept in its own module so tests can pin the
conversion without instantiating a full ``MachineSpec`` payload.
"""

from __future__ import annotations

from typing import Literal

from cap.v0.core import common_pb2

# Adapter contract: the cap-conformance ``MachineSpec.type``
# Literal is the canonical text label set; the proto enum carries
# the wire integer values. Both directions of the mapping should
# be pinned by a unit test in any consumer.
ConformanceMachineType = Literal[
    "excavator", "dump_truck", "bulldozer", "carrier", "roller",
]

_LITERAL_TO_PROTO: dict[str, int] = {
    "excavator":  common_pb2.MACHINE_TYPE_EXCAVATOR,
    "dump_truck": common_pb2.MACHINE_TYPE_DUMP_TRUCK,
    "bulldozer":  common_pb2.MACHINE_TYPE_DOZER,
    "carrier":    common_pb2.MACHINE_TYPE_CARRIER,
    "roller":     common_pb2.MACHINE_TYPE_ROLLER,
}

_PROTO_TO_LITERAL: dict[int, str] = {
    proto: label for label, proto in _LITERAL_TO_PROTO.items()
}


def machine_type_to_proto(label: ConformanceMachineType) -> int:
    """Convert a conformance Literal to its proto enum integer.

    Raises ``KeyError`` when the input falls outside the contract
    (defensive: scenario YAMLs sometimes carry typos like
    ``"dump-truck"`` that ought to fail loudly rather than
    silently round-trip as UNSPECIFIED)."""
    return _LITERAL_TO_PROTO[label]


def machine_type_from_proto(value: int) -> str:
    """Convert a proto enum value back to the conformance Literal.

    Returns an empty string for UNSPECIFIED. Unknown integer values
    (e.g. a future proto enum value the conformance schema doesn't
    recognise yet) also collapse to empty — defensive forward-compat.
    The caller can detect the unknown case by checking against the
    explicit Literal."""
    if value == common_pb2.MACHINE_TYPE_UNSPECIFIED:
        return ""
    return _PROTO_TO_LITERAL.get(value, "")
