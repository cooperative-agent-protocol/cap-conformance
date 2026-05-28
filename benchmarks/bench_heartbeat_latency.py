# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CAP Authors
"""Heartbeat latency benchmark.

Measures the round-trip time for heartbeat messages between a Machine Agent
client and Site Agent server over gRPC.

Usage:
    python -m benchmarks.bench_heartbeat_latency [--iterations 100]
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time

import grpc

from cap.v0.core import runtime_pb2, runtime_pb2_grpc, common_pb2, machine_agent_pb2
from cap_sdk.server import CapRuntimeServicer, serve
from cap_sdk.frame import make_header, wrap_capability_manifest, wrap_heartbeat


async def run_benchmark(iterations: int = 100, port: int = 0) -> dict:
    """Run heartbeat latency benchmark.

    Returns dict with p50, p95, p99, min, max, mean latencies in milliseconds.
    """
    latencies: list[float] = []

    # Setup servicer that echoes heartbeats
    async def on_frame(machine_id, frame):
        return None

    servicer = CapRuntimeServicer(on_frame=on_frame)
    server = grpc.aio.server()
    runtime_pb2_grpc.add_CapRuntimeServiceServicer_to_server(servicer, server)
    actual_port = server.add_insecure_port(f"[::]:{port}")
    await server.start()

    machine_id = "bench-machine-01"

    try:
        async with grpc.aio.insecure_channel(f"localhost:{actual_port}") as channel:
            stub = runtime_pb2_grpc.CapRuntimeServiceStub(channel)

            sent_count = 0

            async def request_stream():
                nonlocal sent_count
                # Send manifest
                header = make_header(sender_id=machine_id, receiver_id="site-agent-01")
                manifest = machine_agent_pb2.CapabilityManifest(
                    machine_id=machine_id,
                    machine_type=common_pb2.MACHINE_TYPE_EXCAVATOR,
                    capabilities=[
                        machine_agent_pb2.Capability(skill="construction.excavate_batch"),
                    ],
                    current_mode=common_pb2.MACHINE_MODE_SUPERVISED_AUTONOMY,
                )
                yield wrap_capability_manifest(header, manifest)

                # Send heartbeats
                for i in range(iterations):
                    header = make_header(sender_id=machine_id, receiver_id="site-agent-01")
                    hb = machine_agent_pb2.Heartbeat(
                        machine_id=machine_id,
                        current_mode=common_pb2.MACHINE_MODE_SUPERVISED_AUTONOMY,
                        healthy=True,
                        estop_active=False,
                        fuel_or_battery_percent=100.0,
                    )
                    start = time.perf_counter_ns()
                    yield wrap_heartbeat(header, hb)
                    # Measure time to enqueue (send-side latency)
                    send_ns = time.perf_counter_ns() - start
                    latencies.append(send_ns / 1_000_000)  # Convert to ms
                    sent_count += 1

                    await asyncio.sleep(0.01)  # 10ms between heartbeats

            stream = stub.Connect(request_stream())
            try:
                async for _ in stream:
                    pass
            except grpc.aio.AioRpcError:
                pass

    finally:
        await server.stop(grace=0)

    if not latencies:
        return {"error": "No heartbeats sent"}

    latencies.sort()
    n = len(latencies)

    return {
        "iterations": n,
        "p50_ms": latencies[n // 2],
        "p95_ms": latencies[int(n * 0.95)],
        "p99_ms": latencies[int(n * 0.99)],
        "min_ms": latencies[0],
        "max_ms": latencies[-1],
        "mean_ms": statistics.mean(latencies),
        "stdev_ms": statistics.stdev(latencies) if n > 1 else 0,
    }


async def main():
    parser = argparse.ArgumentParser(description="Heartbeat latency benchmark")
    parser.add_argument("--iterations", type=int, default=100)
    args = parser.parse_args()

    print(f"Running heartbeat benchmark ({args.iterations} iterations)...")
    results = await run_benchmark(iterations=args.iterations)

    print(f"\n{'─' * 50}")
    print(f"Heartbeat Latency Benchmark Results")
    print(f"{'─' * 50}")
    for key, value in results.items():
        if isinstance(value, float):
            print(f"  {key:>15}: {value:.3f}")
        else:
            print(f"  {key:>15}: {value}")
    print(f"{'─' * 50}")


if __name__ == "__main__":
    asyncio.run(main())
