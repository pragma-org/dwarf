"""F137: bootstrap safety and version downgrade are measured, never constants.

- runtime_handshake_version_negotiation_pressure returned unsafe_defaults_detected False,
  expected_genesis_hash_verified True and version_negotiation_downgrade_seen False as constants,
  and counted ANY reply (even MsgAcceptVersion) as a refusal.
- runtime_bootstrap_assumption_probe never wrote unsafe_defaults_detected and wrote the text
  "not-observable-static" for the downgrade, which the assertion read as True.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from scripts import runtime_exposure_probe as probe
from profile_manager import primitives


def _reply(kind_byte):
    payload = bytes([0x83, kind_byte, 0x0a, 0xa0]) if kind_byte == 1 else bytes([0x82, kind_byte, 0x80])
    return (0).to_bytes(4, "big") + (0x8000).to_bytes(2, "big") + len(payload).to_bytes(2, "big") + payload


@pytest.mark.parametrize("reply,downgrade,rejected", [(_reply(1), True, False), (_reply(2), False, True), (b"", False, True)])
def test_downgrade_is_an_accepted_unsupported_version(monkeypatch, tmp_path, reply, downgrade, rejected):
    class Sock:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def settimeout(self, t): pass
        def sendall(self, d): pass
        def recv(self, n): return reply
    monkeypatch.setattr(probe.socket, "create_connection", lambda *a, **k: Sock())
    monkeypatch.setattr(probe, "_runtime_wire_endpoint", lambda *a, **k: ("10.0.0.2", 3001))
    monkeypatch.setattr(probe, "resolve_target_process", lambda p, n: {"port": 3001})
    out = probe._probe_version_downgrade(runtime_metadata_path=tmp_path / "r.json", target_node="node1", target_host=None)
    assert out["version_negotiation_downgrade_seen"] is downgrade and out["unsupported_version_rejected"] is rejected


def test_handshake_pressure_writes_no_constants(monkeypatch):
    monkeypatch.setattr(probe, "_probe_version_downgrade", lambda **k: {"response_kind": "refused",
                        "version_negotiation_downgrade_seen": False, "unsupported_version_rejected": True})
    monkeypatch.setattr(probe, "resolve_target_process", lambda p, n: {"port": 3001})
    out = probe._run_handshake_version_negotiation_pressure(metadata={}, config={"runtime_metadata_path": "r", "target_node": "node1"})
    assert "unsafe_defaults_detected" not in out and "expected_genesis_hash_verified" not in out
    assert out["version_negotiation_downgrade_seen"] is False


def test_devnet_required_flags_are_not_unsafe_defaults():
    flags = {"ExperimentalProtocolsEnabled": True, "TestShelleyHardForkAtEpoch": True, "TestAllegraHardForkAtEpoch": True}
    unsafe, required = probe._classify_unsafe_flags(flags, private_network=True)
    assert unsafe == ["ExperimentalProtocolsEnabled"]
    assert required == ["TestAllegraHardForkAtEpoch", "TestShelleyHardForkAtEpoch"]
    unsafe_public, required_public = probe._classify_unsafe_flags(flags, private_network=False)
    assert unsafe_public == sorted(flags) and required_public == []


def _evaluate(result, **params):
    event = {"phase": "load", "event": "completed", "primitive": "runtime_bootstrap_assumption_probe",
             "payload": {"outcome": "ok", "result": result}}
    return primitives.BootstrapAssumptionsSafe(params=params).evaluate(SimpleNamespace(events=[event], run_dir="/x"))


def test_assertion_requires_each_check_to_be_measured():
    ok = {"unsafe_defaults_detected": False, "expected_genesis_hash_verified": True, "version_negotiation_downgrade_seen": False}
    assert _evaluate(ok)["result"] == "pass"
    assert _evaluate({**ok, "version_negotiation_downgrade_seen": "not-observable-static"})["result"] == "fail"
    assert _evaluate({"version_negotiation_downgrade_seen": False})["result"] == "fail"


def test_required_checks_narrow_the_assertion():
    event_result = {"version_negotiation_downgrade_seen": False}
    out = _evaluate(event_result, required_checks=["version_negotiation"])
    assert out["result"] == "pass"
