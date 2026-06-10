# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CAP Authors
"""Security conformance tests for CAP (Ch09).

Tests mTLS certificate validation, CN matching, and RBAC enforcement.
Conformance Level 2 (TLS) and Level 3 (mTLS + RBAC).
"""

from __future__ import annotations

import asyncio
import tempfile
import uuid
from pathlib import Path

import grpc
import pytest
import pytest_asyncio

from cap.v0.core import (
    runtime_pb2,
    runtime_pb2_grpc,
    common_pb2,
    machine_agent_pb2,
    site_agent_pb2,
)
from cap_sdk.server import CapRuntimeServicer, serve_secure
from cap_sdk.frame import make_header, wrap_capability_manifest, wrap_heartbeat
from cap_sdk.security.cert_gen import (
    generate_site_ca,
    generate_server_cert,
    generate_machine_cert,
    write_pem,
)
from cap_sdk.security.rbac import RBACEnforcer, Role

from .conftest import make_test_manifest, make_test_heartbeat


# ─── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def cert_dir():
    """Generate a temporary certificate set for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        d = Path(tmpdir)
        ca_cert, ca_key = generate_site_ca("test-site")
        write_pem(ca_cert, ca_key, d / "ca.pem", d / "ca-key.pem")

        srv_cert, srv_key = generate_server_cert(
            ca_cert, ca_key, "site-agent-test", hostnames=["localhost"]
        )
        write_pem(srv_cert, srv_key, d / "server.pem", d / "server-key.pem")

        # Valid machine cert
        m_cert, m_key = generate_machine_cert(ca_cert, ca_key, "test-machine-01")
        write_pem(m_cert, m_key, d / "machine-01.pem", d / "machine-01-key.pem")

        # Machine cert with different CN (for mismatch test)
        m2_cert, m2_key = generate_machine_cert(ca_cert, ca_key, "wrong-machine-id")
        write_pem(m2_cert, m2_key, d / "wrong-machine.pem", d / "wrong-machine-key.pem")

        yield d


@pytest_asyncio.fixture
async def mtls_server(cert_dir):
    """Start a gRPC server with mTLS and RBAC enforcement."""
    received = []

    async def on_frame(machine_id, frame):
        received.append((machine_id, frame))
        return None

    servicer = CapRuntimeServicer(on_frame=on_frame, enforce_rbac=True)
    servicer._received = received

    server = await serve_secure(
        servicer,
        port=0,
        server_cert_path=cert_dir / "server.pem",
        server_key_path=cert_dir / "server-key.pem",
        ca_cert_path=cert_dir / "ca.pem",
        require_client_cert=True,
    )

    # Extract actual port from server
    # grpc.aio.server doesn't expose port directly when using port=0,
    # so we use a known port for testing
    yield server, servicer, received
    await server.stop(grace=0)


# ─── RBAC Unit Tests ──────────────────────────────────────────────────


class TestRBACEnforcer:
    """Test RBAC enforcement logic (Ch09 §9.3.2)."""

    def _make_frame_with_body(self, body_field: str) -> runtime_pb2.CapFrame:
        """Create a CapFrame with the given body field set."""
        header = make_header(sender_id="test", receiver_id="site")
        frame = runtime_pb2.CapFrame(header=header)

        if body_field == "capability_manifest":
            frame.capability_manifest.CopyFrom(make_test_manifest())
        elif body_field == "heartbeat":
            frame.heartbeat.CopyFrom(make_test_heartbeat())
        elif body_field == "work_order":
            frame.work_order.CopyFrom(site_agent_pb2.WorkOrder(
                task_id="test-task", skill="construction.excavate_batch",
            ))
        elif body_field == "progress_event":
            from cap.v0.core import events_pb2
            frame.progress_event.CopyFrom(events_pb2.ProgressEvent(
                task_id="test-task", state=common_pb2.TASK_STATE_RUNNING,
            ))
        elif body_field == "mode_command":
            frame.mode_command.CopyFrom(site_agent_pb2.ModeCommand(
                machine_id="test",
                requested_mode=common_pb2.MACHINE_MODE_TELEOP,
            ))
        elif body_field == "reservation_request":
            frame.reservation_request.CopyFrom(site_agent_pb2.ReservationRequest(
                reservation_id="res-01", resource_id="zone-01", holder_id="test",
            ))
        return frame

    def test_machine_agent_can_send_manifest(self):
        enforcer = RBACEnforcer()
        frame = self._make_frame_with_body("capability_manifest")
        decision = enforcer.check(Role.MACHINE_AGENT, frame)
        assert decision.allowed

    def test_machine_agent_can_send_heartbeat(self):
        enforcer = RBACEnforcer()
        frame = self._make_frame_with_body("heartbeat")
        decision = enforcer.check(Role.MACHINE_AGENT, frame)
        assert decision.allowed

    def test_machine_agent_cannot_send_work_order(self):
        enforcer = RBACEnforcer()
        frame = self._make_frame_with_body("work_order")
        decision = enforcer.check(Role.MACHINE_AGENT, frame)
        assert not decision.allowed
        assert "cannot send 'work_order'" in decision.reason

    def test_observer_cannot_send_anything(self):
        enforcer = RBACEnforcer()
        for body_field in ["capability_manifest", "heartbeat", "work_order", "progress_event"]:
            frame = self._make_frame_with_body(body_field)
            decision = enforcer.check(Role.OBSERVER, frame)
            assert not decision.allowed

    def test_site_admin_can_send_work_order(self):
        enforcer = RBACEnforcer()
        frame = self._make_frame_with_body("work_order")
        decision = enforcer.check(Role.SITE_ADMIN, frame)
        assert decision.allowed

    def test_operator_cannot_send_autonomy_mode_command(self):
        """Ch09 §9.3.2 footnote 1: operator cannot authorize AUTONOMY."""
        enforcer = RBACEnforcer()
        header = make_header(sender_id="op", receiver_id="site")
        frame = runtime_pb2.CapFrame(
            header=header,
            mode_command=site_agent_pb2.ModeCommand(
                machine_id="test",
                requested_mode=common_pb2.MACHINE_MODE_AUTONOMY,
            ),
        )
        decision = enforcer.check(Role.OPERATOR, frame)
        assert not decision.allowed
        assert "AUTONOMY" in decision.reason

    def test_operator_can_send_teleop_mode_command(self):
        enforcer = RBACEnforcer()
        frame = self._make_frame_with_body("mode_command")
        decision = enforcer.check(Role.OPERATOR, frame)
        assert decision.allowed

    def test_machine_agent_can_send_reservation_request(self):
        enforcer = RBACEnforcer()
        frame = self._make_frame_with_body("reservation_request")
        decision = enforcer.check(Role.MACHINE_AGENT, frame)
        assert decision.allowed

    def test_operator_cannot_send_reservation_request(self):
        enforcer = RBACEnforcer()
        frame = self._make_frame_with_body("reservation_request")
        decision = enforcer.check(Role.OPERATOR, frame)
        assert not decision.allowed

    def test_empty_body_denied(self):
        enforcer = RBACEnforcer()
        frame = runtime_pb2.CapFrame(header=make_header(sender_id="x", receiver_id="y"))
        decision = enforcer.check(Role.SITE_ADMIN, frame)
        assert not decision.allowed
        assert "empty" in decision.reason


# ─── Certificate Generation Tests ─────────────────────────────────────


class TestCertificateGeneration:
    """Test certificate generation (Ch09 §9.2.1)."""

    def test_site_ca_is_ca(self, cert_dir):
        from cryptography.x509 import load_pem_x509_certificate
        ca_pem = (cert_dir / "ca.pem").read_bytes()
        ca = load_pem_x509_certificate(ca_pem)
        bc = ca.extensions.get_extension_for_class(
            __import__("cryptography.x509", fromlist=["BasicConstraints"]).BasicConstraints
        )
        assert bc.value.ca is True

    def test_machine_cert_cn_matches_machine_id(self, cert_dir):
        from cryptography.x509 import load_pem_x509_certificate
        from cryptography.x509.oid import NameOID
        cert_pem = (cert_dir / "machine-01.pem").read_bytes()
        cert = load_pem_x509_certificate(cert_pem)
        cn = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
        assert cn == "test-machine-01"

    def test_server_cert_has_san(self, cert_dir):
        from cryptography.x509 import load_pem_x509_certificate, SubjectAlternativeName, DNSName
        cert_pem = (cert_dir / "server.pem").read_bytes()
        cert = load_pem_x509_certificate(cert_pem)
        san = cert.extensions.get_extension_for_class(SubjectAlternativeName)
        dns_names = san.value.get_values_for_type(DNSName)
        assert "localhost" in dns_names

    def test_machine_cert_has_client_auth_eku(self, cert_dir):
        from cryptography.x509 import load_pem_x509_certificate, ExtendedKeyUsage
        from cryptography.x509.oid import ExtendedKeyUsageOID
        cert_pem = (cert_dir / "machine-01.pem").read_bytes()
        cert = load_pem_x509_certificate(cert_pem)
        eku = cert.extensions.get_extension_for_class(ExtendedKeyUsage)
        assert ExtendedKeyUsageOID.CLIENT_AUTH in eku.value
