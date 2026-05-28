<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 CAP Authors
-->

# CAP Performance Benchmark Results

Environment: macOS 15.3.2, Python 3.10.0, gRPC 1.78.0, Apple Silicon

## Scalability Benchmark (bench_scalability.py)

Each machine sends heartbeats at 1s intervals.

### 10 Machines, 10s duration

```
Messages received: 110
Duration: 12.00s
Throughput: 9.2 msgs/sec
Latency p50: 0.32ms
Latency p95: 1.03ms
Latency p99: 1.29ms
Peak memory: 35.7MB
```

### Analysis

- **Throughput**: 9.2 msgs/sec matches expected 10 machines × 1 heartbeat/sec = 10 msgs/sec
- **Latency**: p99 < 2ms — well within the 100ms target for task-level coordination
- **Memory**: 35.7MB for 10 machines — ~3.6MB per machine, acceptable
- **No message loss**: 110 messages received for 10 machines over ~11 heartbeat cycles

### Comparison with Ch11 Recommended Limits

| Metric | Recommended (Ch11) | Measured |
|--------|-------------------|----------|
| Max machines | 50 | 10 tested |
| Heartbeat latency | < 100ms | p99 = 1.29ms |
| Message throughput | 500 msgs/sec | 9.2 (heartbeat-limited) |
| Memory per machine | — | ~3.6MB |
