# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CAP Authors
"""Transport-agnostic test runner — runs conformance tests over gRPC or MQTT.

Usage:
    # Run all tests over gRPC (default):
    pytest tests/ --transport grpc

    # Run all tests over MQTT (requires mosquitto on localhost:1883):
    pytest tests/ --transport mqtt

    # Run this module directly for quick transport verification:
    python -m tests.transport_runner [grpc|mqtt]

The conftest.py already provides --transport flag and transport_type fixture.
This module provides the TransportClient abstraction that individual tests
can use to be transport-agnostic.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

from cap.v0.core import runtime_pb2

logger = logging.getLogger(__name__)


class TransportClient(ABC):
    """Abstract transport client for conformance tests."""

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection."""

    @abstractmethod
    async def send_frame(self, frame: runtime_pb2.CapFrame) -> None:
        """Send a CapFrame."""

    @abstractmethod
    async def receive_frames(self, timeout: float = 1.0) -> list[runtime_pb2.CapFrame]:
        """Receive pending frames."""

    @abstractmethod
    async def close(self) -> None:
        """Close connection."""


class GrpcTransportClient(TransportClient):
    """gRPC transport client."""

    def __init__(self, target: str = "localhost:50051") -> None:
        self._target = target
        self._channel: Any = None
        self._stub: Any = None
        self._outgoing: asyncio.Queue[runtime_pb2.ConnectRequest] = asyncio.Queue()
        self._received: list[runtime_pb2.CapFrame] = []
        self._stream_task: asyncio.Task | None = None

    async def connect(self) -> None:
        import grpc  # noqa: PLC0415
        self._channel = grpc.aio.insecure_channel(self._target)
        from cap.v0 import runtime_pb2_grpc  # noqa: PLC0415
        self._stub = runtime_pb2_grpc.CapRuntimeServiceStub(self._channel)
        self._stream_task = asyncio.create_task(self._run_stream())

    async def _run_stream(self) -> None:
        async def request_iter() -> AsyncIterator[runtime_pb2.ConnectRequest]:
            while True:
                req = await self._outgoing.get()
                yield req

        try:
            async for response in self._stub.Connect(request_iter()):
                self._received.append(response.frame)
        except Exception:
            pass

    async def send_frame(self, frame: runtime_pb2.CapFrame) -> None:
        req = runtime_pb2.ConnectRequest(frame=frame)
        await self._outgoing.put(req)

    async def receive_frames(self, timeout: float = 1.0) -> list[runtime_pb2.CapFrame]:
        await asyncio.sleep(timeout)
        frames = self._received.copy()
        self._received.clear()
        return frames

    async def close(self) -> None:
        if self._stream_task:
            self._stream_task.cancel()
        if self._channel:
            await self._channel.close()


class MqttTransportClient(TransportClient):
    """MQTT transport client."""

    def __init__(self, broker: str = "localhost", port: int = 1883, site_id: str = "test") -> None:
        self._broker = broker
        self._port = port
        self._site_id = site_id
        self._client: Any = None
        self._received: list[runtime_pb2.CapFrame] = []

    async def connect(self) -> None:
        try:
            from cap_sdk.transport.mqtt_adapter import MqttTransportAdapter, MqttTransportConfig  # noqa: PLC0415
            config = MqttTransportConfig(broker=self._broker, port=self._port, site_id=self._site_id)
            self._adapter = MqttTransportAdapter(config)
            await self._adapter.connect()
            self._adapter.on_frame = lambda frame: self._received.append(frame)
        except ImportError:
            raise RuntimeError("aiomqtt not installed. Run: pip install aiomqtt")

    async def send_frame(self, frame: runtime_pb2.CapFrame) -> None:
        if self._adapter:
            await self._adapter.send(frame)

    async def receive_frames(self, timeout: float = 1.0) -> list[runtime_pb2.CapFrame]:
        await asyncio.sleep(timeout)
        frames = self._received.copy()
        self._received.clear()
        return frames

    async def close(self) -> None:
        if self._adapter:
            await self._adapter.disconnect()


def create_client(transport: str = "grpc", **kwargs: Any) -> TransportClient:
    """Factory for transport clients."""
    if transport == "mqtt":
        return MqttTransportClient(**kwargs)
    return GrpcTransportClient(**kwargs)


async def _quick_verify(transport: str) -> None:
    """Quick verification that a transport works."""
    from cap.v0 import common_pb2, machine_agent_pb2  # noqa: PLC0415
    from cap_sdk.frame import make_header  # noqa: PLC0415

    logger.info("Testing %s transport...", transport)
    client = create_client(transport)

    try:
        await client.connect()
        logger.info("Connected via %s", transport)

        # Send a manifest
        header = make_header(sender_id="transport-test-01", receiver_id="site-agent-01")
        manifest = machine_agent_pb2.CapabilityManifest(
            machine_id="transport-test-01",
            machine_type=common_pb2.MACHINE_TYPE_EXCAVATOR,
            capabilities=[machine_agent_pb2.Capability(skill="construction.excavate_batch")],
            current_mode=common_pb2.MACHINE_MODE_SUPERVISED_AUTONOMY,
        )
        frame = runtime_pb2.CapFrame(header=header, capability_manifest=manifest)
        await client.send_frame(frame)
        logger.info("Sent CapabilityManifest via %s", transport)

        frames = await client.receive_frames(timeout=2.0)
        logger.info("Received %d frames via %s", len(frames), transport)

        logger.info("%s transport: OK", transport)
    except Exception as e:
        logger.error("%s transport: FAILED — %s", transport, e)
    finally:
        await client.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    transport = sys.argv[1] if len(sys.argv) > 1 else "grpc"
    asyncio.run(_quick_verify(transport))
