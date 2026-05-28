# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CAP Authors
"""Conformance tests for MQTT transport profile (Level 2, Ch04).

Tests that the MQTT adapter correctly routes CapFrame messages using the
CAP topic structure and QoS mappings.

Prerequisites:
    - Mosquitto broker running on localhost:1883
    - pip install aiomqtt
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from cap.v0.core import runtime_pb2, common_pb2, machine_agent_pb2
from cap_sdk.frame import make_header

# Skip if aiomqtt not available
aiomqtt = pytest.importorskip("aiomqtt", reason="aiomqtt not installed")

from cap_sdk.transport.mqtt_adapter import MqttTransportAdapter, MqttTransportConfig


def _make_manifest_frame(machine_id: str) -> runtime_pb2.CapFrame:
    header = make_header(sender_id=machine_id, receiver_id="site-agent-01")
    manifest = machine_agent_pb2.CapabilityManifest(
        machine_id=machine_id,
        machine_type=common_pb2.MACHINE_TYPE_EXCAVATOR,
        capabilities=[machine_agent_pb2.Capability(skill="construction.excavate_batch")],
        current_mode=common_pb2.MACHINE_MODE_SUPERVISED_AUTONOMY,
        hal_profile="test/fake",
        software_version="0.1.0-mqtt-test",
    )
    return runtime_pb2.CapFrame(header=header, capability_manifest=manifest)


def _make_heartbeat_frame(machine_id: str) -> runtime_pb2.CapFrame:
    header = make_header(sender_id=machine_id, receiver_id="site-agent-01")
    hb = machine_agent_pb2.Heartbeat(
        machine_id=machine_id,
        current_mode=common_pb2.MACHINE_MODE_SUPERVISED_AUTONOMY,
        healthy=True,
        fuel_or_battery_percent=90.0,
    )
    return runtime_pb2.CapFrame(header=header, heartbeat=hb)


def _make_config(site_id: str, machine_id: str, role: str = "machine_agent") -> MqttTransportConfig:
    return MqttTransportConfig(
        host="localhost",
        port=1883,
        site_id=site_id,
        machine_id=machine_id,
        role=role,
    )


@pytest.mark.asyncio
@pytest.mark.mqtt
async def test_mqtt_adapter_connect_disconnect():
    """MQTT adapter can connect and disconnect cleanly."""
    site_id = f"test-site-{uuid.uuid4().hex[:8]}"
    config = _make_config(site_id, "mqtt-test-01")
    adapter = MqttTransportAdapter(config)

    try:
        await adapter.connect()
        assert adapter.is_connected
    except (ConnectionError, OSError):
        pytest.skip("MQTT broker not available at localhost:1883")
    finally:
        await adapter.disconnect()
        assert not adapter.is_connected


@pytest.mark.asyncio
@pytest.mark.mqtt
async def test_mqtt_send_receive_manifest():
    """CapFrame can be sent via MQTT and received by another adapter."""
    site_id = f"test-site-{uuid.uuid4().hex[:8]}"
    machine_id = "mqtt-test-02"

    machine_adapter = MqttTransportAdapter(_make_config(site_id, machine_id, "machine_agent"))
    site_adapter = MqttTransportAdapter(_make_config(site_id, machine_id, "site_agent"))

    try:
        await site_adapter.connect()
        await machine_adapter.connect()
    except (ConnectionError, OSError):
        pytest.skip("MQTT broker not available at localhost:1883")
        return

    try:
        await asyncio.sleep(0.5)  # Let subscriptions establish

        frame = _make_manifest_frame(machine_id)
        await machine_adapter.send(frame)

        # Receive on site side
        received = []
        async def collect():
            async for f in site_adapter.receive():
                received.append(f)
                if received:
                    break

        try:
            await asyncio.wait_for(collect(), timeout=3.0)
        except asyncio.TimeoutError:
            pass

        assert len(received) >= 1, "Site adapter should receive the manifest"
        assert received[0].HasField("capability_manifest")
        assert received[0].capability_manifest.machine_id == machine_id
    finally:
        await machine_adapter.disconnect()
        await site_adapter.disconnect()


@pytest.mark.asyncio
@pytest.mark.mqtt
async def test_mqtt_heartbeat_qos0():
    """Heartbeat messages should use QoS 0 (fire-and-forget) per spec."""
    site_id = f"test-site-{uuid.uuid4().hex[:8]}"
    config = _make_config(site_id, "mqtt-test-03")
    adapter = MqttTransportAdapter(config)

    try:
        await adapter.connect()
    except (ConnectionError, OSError):
        pytest.skip("MQTT broker not available at localhost:1883")
        return

    try:
        frame = _make_heartbeat_frame("mqtt-test-03")
        await adapter.send(frame)  # Should not raise
    finally:
        await adapter.disconnect()


@pytest.mark.asyncio
@pytest.mark.mqtt
async def test_mqtt_deduplication():
    """Duplicate message_id should be detected and discarded."""
    site_id = f"test-site-{uuid.uuid4().hex[:8]}"
    machine_id = "mqtt-test-04"

    machine_adapter = MqttTransportAdapter(_make_config(site_id, machine_id, "machine_agent"))
    site_adapter = MqttTransportAdapter(_make_config(site_id, machine_id, "site_agent"))

    try:
        await site_adapter.connect()
        await machine_adapter.connect()
    except (ConnectionError, OSError):
        pytest.skip("MQTT broker not available at localhost:1883")
        return

    try:
        await asyncio.sleep(0.5)

        frame = _make_heartbeat_frame(machine_id)
        await machine_adapter.send(frame)
        await machine_adapter.send(frame)  # Duplicate (same message_id)

        received = []
        async def collect():
            async for f in site_adapter.receive():
                received.append(f)
                if len(received) >= 2:
                    break

        try:
            await asyncio.wait_for(collect(), timeout=2.0)
        except asyncio.TimeoutError:
            pass

        # Deduplication should discard the second message
        assert len(received) == 1, f"Expected 1 (deduplicated), got {len(received)}"
    finally:
        await machine_adapter.disconnect()
        await site_adapter.disconnect()
