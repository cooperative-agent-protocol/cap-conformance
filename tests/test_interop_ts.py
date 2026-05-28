# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CAP Authors
"""Cross-language interoperability test: Python × TypeScript.

Verifies that protobuf-serialized CapFrame messages produced by the Python
implementation can be correctly deserialized by the TypeScript implementation
(and vice versa), proving wire format compatibility across languages.

This test requires:
  - Node.js installed and available on PATH
  - npm dependencies installed in cap-reference/ts/
  - Generated TS code in cap-spec/gen/ts/ with .js extensions

The test flow:
  1. Python creates a CapFrame(CapabilityManifest) and serializes it
  2. The TypeScript interop_test.mjs script deserializes it and creates a response
  3. Python deserializes the response and validates the fields
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cap.v0.core import runtime_pb2, common_pb2, machine_agent_pb2
from cap_sdk.frame import make_header


# Locate the interop test script
_REPO_ROOT = Path(__file__).resolve().parents[2]
_TS_INTEROP_SCRIPT = _REPO_ROOT / "cap-reference" / "ts" / "interop_test.mjs"
_TS_DIR = _REPO_ROOT / "cap-reference" / "ts"
_TS_NODE_MODULES = _TS_DIR / "node_modules"

# Skip if Node.js, the interop script, or its npm dependencies are missing.
# Run ``npm install`` from cap-reference/ts/ to enable these tests.
_HAS_NODE = shutil.which("node") is not None
_HAS_SCRIPT = _TS_INTEROP_SCRIPT.exists()
_HAS_NODE_MODULES = _TS_NODE_MODULES.is_dir()

pytestmark = pytest.mark.skipif(
    not (_HAS_NODE and _HAS_SCRIPT and _HAS_NODE_MODULES),
    reason=(
        "Node.js, interop_test.mjs, or cap-reference/ts/node_modules missing. "
        "Run `cd cap-reference/ts && npm install` to enable interop tests."
    ),
)


def _run_ts_interop(input_bytes: bytes) -> tuple[bytes, str]:
    """Run the TS interop script with input bytes, return (stdout, stderr)."""
    result = subprocess.run(
        ["node", str(_TS_INTEROP_SCRIPT)],
        input=input_bytes,
        capture_output=True,
        timeout=30,
        cwd=str(_TS_DIR),
    )
    if result.returncode != 0:
        pytest.fail(
            f"TS interop script failed (exit {result.returncode}):\n"
            f"stderr: {result.stderr.decode()}"
        )
    return result.stdout, result.stderr.decode()


def test_manifest_roundtrip():
    """Python CapabilityManifest → TS deserialization → TS WorkOrderAck → Python validation.

    Proves that the protobuf wire format is compatible across Python and TypeScript.
    """
    # 1. Create a CapFrame with CapabilityManifest in Python
    header = make_header(sender_id="site-agent-01", receiver_id="excavator-01")
    manifest = machine_agent_pb2.CapabilityManifest(
        machine_id="excavator-01",
        machine_type=common_pb2.MACHINE_TYPE_EXCAVATOR,
        capabilities=[
            machine_agent_pb2.Capability(
                skill="construction.excavate_batch",
                limits=[
                    common_pb2.KeyValue(key="max_reach_m", value="9.9"),
                    common_pb2.KeyValue(key="bucket_capacity_m3", value="0.8"),
                ],
            ),
            machine_agent_pb2.Capability(skill="load_truck"),
        ],
        current_mode=common_pb2.MACHINE_MODE_SUPERVISED_AUTONOMY,
        hal_profile="komatsu/pc200-11",
        software_version="0.1.0-interop-test",
        metadata=[
            common_pb2.KeyValue(key="serial_number", value="PC200-11-12345"),
        ],
    )
    frame = runtime_pb2.CapFrame(header=header)
    frame.capability_manifest.CopyFrom(manifest)

    # 2. Serialize and send to TS
    input_bytes = frame.SerializeToString()
    assert len(input_bytes) > 0, "Serialized frame should not be empty"

    output_bytes, stderr = _run_ts_interop(input_bytes)

    # 3. Verify TS processed it (check stderr logs)
    assert "[TS] PASS" in stderr, f"TS did not report PASS. stderr:\n{stderr}"
    assert "excavator-01" in stderr, "TS should have logged machine_id"
    assert "construction.excavate_batch" in stderr, "TS should have logged skill"

    # 4. Deserialize TS response in Python
    assert len(output_bytes) > 0, "TS should have written response bytes"
    response_frame = runtime_pb2.CapFrame()
    response_frame.ParseFromString(output_bytes)

    # 5. Validate the response
    assert response_frame.HasField("work_order_ack"), (
        f"Expected work_order_ack, got body: {response_frame.WhichOneof('body')}"
    )

    ack = response_frame.work_order_ack
    assert ack.task_id == "interop-test-task"
    assert ack.decision == machine_agent_pb2.WorkOrderAck.DECISION_ACCEPTED
    assert len(ack.reason) > 0, "Reason should not be empty"

    # 6. Validate response header
    assert response_frame.header.sender_id == "excavator-01"
    assert response_frame.header.receiver_id == "site-agent-01"
    assert response_frame.header.message_id == "ts-interop-response-001"


def test_manifest_with_multiple_capabilities():
    """Test that multiple capabilities with limits survive the roundtrip."""
    header = make_header(sender_id="py-test", receiver_id="ts-test")
    manifest = machine_agent_pb2.CapabilityManifest(
        machine_id="multi-skill-machine",
        machine_type=common_pb2.MACHINE_TYPE_DUMP_TRUCK,
        capabilities=[
            machine_agent_pb2.Capability(
                skill="construction.haul_route",
                limits=[common_pb2.KeyValue(key="max_payload_kg", value="20000")],
            ),
            machine_agent_pb2.Capability(skill="safe_wait"),
        ],
        current_mode=common_pb2.MACHINE_MODE_MANUAL,
        hal_profile="test/dump-truck",
        software_version="0.2.0",
    )
    frame = runtime_pb2.CapFrame(header=header)
    frame.capability_manifest.CopyFrom(manifest)

    output_bytes, stderr = _run_ts_interop(frame.SerializeToString())

    assert "[TS] PASS" in stderr
    assert "multi-skill-machine" in stderr
    assert "construction.haul_route" in stderr

    response = runtime_pb2.CapFrame()
    response.ParseFromString(output_bytes)
    assert response.HasField("work_order_ack")
    assert response.work_order_ack.decision == machine_agent_pb2.WorkOrderAck.DECISION_ACCEPTED
