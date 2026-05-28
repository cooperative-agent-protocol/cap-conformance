# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CAP Authors
"""Multi-machine scalability benchmark for CAP runtime.

Spawns N simulated machine agents, each sending heartbeats and progress events.
Measures server-side processing latency, throughput, and memory usage.

Usage:
    python -m benchmarks.bench_scalability --machines 10
    python -m benchmarks.bench_scalability --machines 50 --duration 30
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import statistics
import time
import resource as resource_mod
from typing import Any

import grpc

from cap.v0.core import runtime_pb2, runtime_pb2_grpc, common_pb2, machine_agent_pb2, events_pb2
from cap_sdk.server import CapRuntimeServicer, serve
from cap_sdk.frame import make_header, wrap_capability_manifest, wrap_heartbeat, wrap_progress_event

logger = logging.getLogger(__name__)


class BenchmarkResults:
    def __init__(self):
        self.latencies_ms: list[float] = []
        self.messages_received: int = 0
        self.start_time: float = 0.0
        self.end_time: float = 0.0
        self.peak_memory_mb: float = 0.0

    @property
    def throughput(self) -> float:
        elapsed = self.end_time - self.start_time
        if elapsed <= 0:
            return 0.0
        return self.messages_received / elapsed

    @property
    def p50_ms(self) -> float:
        return statistics.median(self.latencies_ms) if self.latencies_ms else 0.0

    @property
    def p95_ms(self) -> float:
        if not self.latencies_ms:
            return 0.0
        sorted_lat = sorted(self.latencies_ms)
        idx = int(len(sorted_lat) * 0.95)
        return sorted_lat[min(idx, len(sorted_lat) - 1)]

    @property
    def p99_ms(self) -> float:
        if not self.latencies_ms:
            return 0.0
        sorted_lat = sorted(self.latencies_ms)
        idx = int(len(sorted_lat) * 0.99)
        return sorted_lat[min(idx, len(sorted_lat) - 1)]

    def report(self, n_machines: int) -> str:
        lines = [
            f"=== CAP Scalability Benchmark: {n_machines} machines ===",
            f"Messages received: {self.messages_received}",
            f"Duration: {self.end_time - self.start_time:.2f}s",
            f"Throughput: {self.throughput:.1f} msgs/sec",
            f"Latency p50: {self.p50_ms:.2f}ms",
            f"Latency p95: {self.p95_ms:.2f}ms",
            f"Latency p99: {self.p99_ms:.2f}ms",
            f"Peak memory: {self.peak_memory_mb:.1f}MB",
        ]
        return "\n".join(lines)


async def run_benchmark(n_machines: int, duration_sec: float) -> BenchmarkResults:
    results = BenchmarkResults()
    receive_times: dict[str, float] = {}

    async def on_frame(machine_id: str, frame: runtime_pb2.CapFrame):
        results.messages_received += 1
        msg_id = frame.header.message_id
        now = time.monotonic()

        # Record arrival time for latency calculation
        if msg_id in receive_times:
            latency = (now - receive_times[msg_id]) * 1000
            results.latencies_ms.append(latency)

        return None

    servicer = CapRuntimeServicer(on_frame=on_frame)
    server_obj = await serve(servicer, port=0)

    # Get actual port
    # Since we can't easily get the port from grpc.aio.server with port=0,
    # use a fixed port for benchmarking
    port = 50100
    server_obj2 = grpc.aio.server()
    runtime_pb2_grpc.add_CapRuntimeServiceServicer_to_server(servicer, server_obj2)
    server_obj2.add_insecure_port(f"[::]:{port}")
    await server_obj2.start()
    await server_obj.stop(grace=0)

    async def simulate_machine(machine_id: str, hb_interval: float = 1.0):
        try:
            async with grpc.aio.insecure_channel(f"localhost:{port}") as ch:
                stub = runtime_pb2_grpc.CapRuntimeServiceStub(ch)

                async def request_stream():
                    # Send manifest
                    header = make_header(sender_id=machine_id, receiver_id="site-agent-01")
                    manifest = machine_agent_pb2.CapabilityManifest(
                        machine_id=machine_id,
                        machine_type=common_pb2.MACHINE_TYPE_EXCAVATOR,
                        capabilities=[machine_agent_pb2.Capability(skill="construction.excavate_batch")],
                        current_mode=common_pb2.MACHINE_MODE_SUPERVISED_AUTONOMY,
                        hal_profile="bench/fake",
                        software_version="bench-0.1",
                    )
                    req = wrap_capability_manifest(header, manifest)
                    yield req

                    # Send heartbeats
                    deadline = time.monotonic() + duration_sec
                    while time.monotonic() < deadline:
                        header = make_header(sender_id=machine_id, receiver_id="site-agent-01")
                        hb = machine_agent_pb2.Heartbeat(
                            machine_id=machine_id,
                            current_mode=common_pb2.MACHINE_MODE_SUPERVISED_AUTONOMY,
                            healthy=True,
                            fuel_or_battery_percent=85.0,
                        )
                        req = wrap_heartbeat(header, hb)
                        receive_times[req.frame.header.message_id] = time.monotonic()
                        yield req
                        await asyncio.sleep(hb_interval)

                stream = stub.Connect(request_stream())
                async for _ in stream:
                    pass
        except Exception as e:
            logger.debug("Machine %s ended: %s", machine_id, e)

    results.start_time = time.monotonic()

    tasks = [
        asyncio.create_task(simulate_machine(f"bench-{i:03d}", hb_interval=1.0))
        for i in range(n_machines)
    ]

    # Wait for duration + grace period
    await asyncio.sleep(duration_sec + 2)

    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    results.end_time = time.monotonic()

    # Memory usage
    usage = resource_mod.getrusage(resource_mod.RUSAGE_SELF)
    results.peak_memory_mb = usage.ru_maxrss / (1024 * 1024)  # macOS reports bytes

    await server_obj2.stop(grace=0)
    return results


async def main():
    parser = argparse.ArgumentParser(description="CAP scalability benchmark")
    parser.add_argument("--machines", type=int, default=10)
    parser.add_argument("--duration", type=float, default=10.0)
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)

    print(f"Starting benchmark: {args.machines} machines, {args.duration}s duration...")
    results = await run_benchmark(args.machines, args.duration)
    print(results.report(args.machines))


if __name__ == "__main__":
    asyncio.run(main())
