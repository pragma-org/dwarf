"""F107: the observability trace check reads both cardano-node tracing systems, and profile-a
enables the peer and connection namespaces it needs.

The check read only the legacy keys (EnableLogging, minSeverity, TraceXxx). Current nodes use the
trace dispatcher (TraceOptions per namespace), and profile-a ran with "TraceOptions": {}.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import runtime_compose_substrate as compose
from scripts import runtime_observability_check as obs

NAMESPACES = ("Net.ConnectionManager", "Net.PeerSelection", "Net.Peers.LocalRoot", "Net.Peers.PublicRoot")


def test_dispatcher_config_with_debug_namespaces_passes():
    config = {"TraceOptions": {"": {"severity": "Notice"}, **{ns: {"severity": "Debug", "detail": "DDetailed"} for ns in NAMESPACES}}}
    result = obs.trace_settings(config)
    assert result["system"] == "trace-dispatcher" and result["ok"] is True and result["missing"] == []


def test_dispatcher_config_with_defaults_names_the_missing_namespaces():
    result = obs.trace_settings({"TraceOptions": {}})
    assert result["ok"] is False and result["missing"] == list(NAMESPACES)


def test_a_debug_root_covers_every_namespace():
    assert obs.trace_settings({"TraceOptions": {"": {"severity": "Debug"}}})["ok"] is True


def test_legacy_config_is_still_read():
    legacy = {"EnableLogging": True, "minSeverity": "Debug", "TraceConnectionManager": True, "TracePeerSelection": True,
              "TraceLocalRootPeers": True, "TracePublicRootPeers": True}
    result = obs.trace_settings(legacy)
    assert result["system"] == "legacy" and result["ok"] is True
    assert obs.trace_settings({**legacy, "TracePeerSelection": False})["missing"] == ["TracePeerSelection"]


def test_compose_enables_observability_namespaces_without_dropping_others(tmp_path):
    path = tmp_path / "configuration.yaml"
    path.write_text(json.dumps({"TraceOptions": {"Mempool": {"severity": "Info"}}}), encoding="utf-8")
    report = compose._enable_cardano_observability_traces(path)
    body = json.loads(path.read_text())
    assert body["TraceOptions"]["Mempool"] == {"severity": "Info"}
    for ns in NAMESPACES:
        assert body["TraceOptions"][ns] == {"severity": "Debug", "detail": "DDetailed"}
    assert report["enabled"] is True and report["namespaces"] == sorted(NAMESPACES)
    assert obs.trace_settings(body)["ok"] is True


def test_profile_a_declares_observability_traces():
    from profile_manager.profiles import find_profile
    profile = find_profile("profile-a-haskell-peersharing-disabled")
    assert profile.cardano_observability_traces is True
