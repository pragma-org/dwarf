import json
from dataclasses import asdict

from profile_manager.deployment_versions import build_deployment_version_preview
from profile_manager.profiles import find_profile, versioned_substrate_for_profile
from scripts.runtime_compose_substrate import _enable_cardano_measurement_traces


def test_cardano_measurement_profile_is_exact_opt_in_and_preserves_profile_identity():
    profile = find_profile("profile-s-cardano-measurement-stock-control")
    assert profile.cardano_measurement_traces is True
    assert profile.measurement_target_mode == "stock"
    assert profile.version_policy == "exact"
    assert profile.cardano_version == "11.1.2"
    preview = build_deployment_version_preview(asdict(profile))
    substrate = versioned_substrate_for_profile(profile, preview)
    assert substrate["profile_id"] == profile.id
    assert substrate["cardano_measurement_traces"] is True


def test_measurement_trace_config_enables_only_audited_machine_namespaces(tmp_path):
    path = tmp_path / "configuration.yaml"
    original = {
        "Protocol": "Cardano",
        "TraceOptions": {"Net.PeerSelection": {"severity": "Silence"}},
        "unrelated": {"preserved": True},
    }
    path.write_text(json.dumps(original))
    result = _enable_cardano_measurement_traces(path)
    body = json.loads(path.read_text())
    assert result["enabled"] is True
    assert body["unrelated"] == {"preserved": True}
    options = body["TraceOptions"]
    required = {
        "Net.Handshake.Remote",
        "ChainSync.Client",
        "ChainSync.ServerHeader",
        "BlockFetch.Client",
        "BlockFetch.Server",
        "TxSubmission.TxInbound",
        "TxSubmission.TxOutbound",
        "Mempool",
        "KeepAlive.Remote",
        "ChainDB",
        "Forge.Loop",
        "LedgerMetrics",
        "Resources",
    }
    assert required <= set(options)
    assert options["Net.PeerSelection"] == {"severity": "Silence"}
    assert all(options[name]["severity"] == "Info" for name in required)
    assert all(options[name]["detail"] == "DDetailed" for name in required)
    assert result["namespaces"] == sorted(required)
