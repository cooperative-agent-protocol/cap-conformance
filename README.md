<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 CAP Authors
-->

# CAP Conformance

> CAP (Cooperative Agent Protocol) の適合性テストスイート — Core (transport / runtime) + Construction Domain Pack v0

## テストスイート一覧

### Level 1: Core

| テストファイル | テスト数 | 内容 |
|--------------|---------|------|
| `test_handshake` | 2 | CapabilityManifest 交換、フィールド検証 |
| `test_heartbeat` | 2 | Heartbeat 受信、フィールド検証 |
| `test_work_order_lifecycle` | 3 | WorkOrder → Ack → Progress → Succeeded/Failed |
| `test_error_handling` | 4 | 不正状態遷移、ストリーム存続、UUID 検証、correlation_id |
| `test_concurrent_work_orders` | 1 | 同時 WorkOrder のDEFERRED/REJECTED |
| `test_reconnection` | 1 | 再接続時の Manifest 再送 |
| `test_timeout_cascade` | 2 | Heartbeat timeout 検知 |

### Level 2: Coordination

| テストファイル | テスト数 | 内容 |
|--------------|---------|------|
| `test_reservation` | 4 | Reservation grant/release/conflict |
| `test_handover` | 4 | HandoverEvent、SafetyEvent、FaultEvent |
| `test_mode_command` | 3 | ModeCommand 送受信、Heartbeat mode 報告 |
| `test_mqtt_transport` | 4 | MQTT connect/disconnect、send/receive、QoS、deduplication |

### Level 3: Intelligence

| テストファイル | テスト数 | 内容 |
|--------------|---------|------|
| `test_alo_lifecycle` | 3 | ALO in manifest、state update、heartbeat summary |
| `test_dialogue` | 4 | Dialogue round-trip、provenance、intent、CROSS_TASK 承認 |
| `test_dialogue_edge_cases` | 5 | TASK scope 承認、PlanProposal、model_id 検証 |

### Security

| テストファイル | テスト数 | 内容 |
|--------------|---------|------|
| `test_security` | 14 | RBAC 認可マトリクス (10)、証明書生成 (4) |

### Cross-language

| テストファイル | テスト数 | 内容 |
|--------------|---------|------|
| `test_interop_ts` | — | Python ↔ TypeScript 相互運用（要 Node.js + npm install） |

## 実行方法

```bash
# 依存インストール
pip install -e cap-reference/python
pip install pytest pytest-asyncio cryptography

# gRPC テスト実行（全テスト、未満足条件のものは自動 skip）
PYTHONPATH=cap-spec/gen/python:cap-reference/python \
  pytest cap-conformance/tests/ -v

# MQTT テスト実行（要 Mosquitto on localhost:1883）
pip install aiomqtt
PYTHONPATH=cap-spec/gen/python:cap-reference/python \
  pytest cap-conformance/tests/test_mqtt_transport.py -v

# TypeScript 相互運用テスト実行（要 Node.js）
cd cap-reference/ts && npm install && cd -
PYTHONPATH=cap-spec/gen/python:cap-reference/python \
  pytest cap-conformance/tests/test_interop_ts.py -v
```

`test_interop_ts.py` は `cap-reference/ts/node_modules` が無いと自動で
skip される。npm install せずに全テストを走らせると 3 件 skip + 残り全 pass。

## ベンチマーク

[benchmarks/RESULTS.md](benchmarks/RESULTS.md) — 10 機体同時接続、p99 = 1.29ms

```bash
PYTHONPATH=cap-spec/gen/python:cap-reference/python \
  python cap-conformance/benchmarks/bench_scalability.py --machines 10 --duration 10
```

## License

Apache License 2.0
