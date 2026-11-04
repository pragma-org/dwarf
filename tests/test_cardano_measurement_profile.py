import json
from pathlib import Path
from dataclasses import asdict

from profile_manager.deployment_versions import build_deployment_version_preview
from profile_manager.profiles import find_profile, versioned_substrate_for_profile
from scripts.runtime_compose_substrate import _enable_cardano_measurement_traces
from scripts.runtime_substrate_common import allocate_node_plan, normalize_substrate


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
    # KeepAlive AddSample (the only round-trip sample) is a Debug-level
    # message in cardano-node 11.1.x; every other audited namespace is Info.
    assert options["KeepAlive.Remote"]["severity"] == "Debug"
    assert all(
        options[name]["severity"] == "Info" for name in required - {"KeepAlive.Remote"}
    )
    assert all(options[name]["detail"] == "DDetailed" for name in required)
    assert result["namespaces"] == sorted(required)


def test_runtime_normalization_preserves_profile_and_opt_in_measurement_traces():
    profile = find_profile("profile-s-cardano-measurement-stock-control")
    preview = build_deployment_version_preview(profile.__dict__)
    substrate = versioned_substrate_for_profile(profile, preview)

    normalized = normalize_substrate(substrate)

    assert normalized["profile_id"] == profile.id
    assert normalized["cardano_measurement_traces"] is True
    assert normalized["amaru_json_traces"] is False

    plan = allocate_node_plan(
        normalized,
        runtime_root=Path("/tmp/dwarf-cardano-measurement-control"),
        compose_project="dwarf-profile-s-cardano-measurement-stock-control",
        base_haskell_port=33001,
        base_amaru_port=43001,
    )
    assert plan["profile_id"] == profile.id
    assert plan["cardano_measurement_traces"] is True
    assert plan["amaru_json_traces"] is False


def test_operator_latest_cardano_profile_opts_into_measurement_traces():
    profile = find_profile("profile-n-cardano-latest-confirmed")
    assert profile.cardano_measurement_traces is True
    document = json.loads(
        Path("dwarf/profiles/profile-n-cardano-latest-confirmed/profile.yaml").read_text()
    )
    assert document["cardano_measurement_traces"] is True
    amaru = json.loads(
        Path("dwarf/profiles/profile-o-amaru-target-latest-confirmed/profile.yaml").read_text()
    )
    assert amaru["amaru_json_traces"] is True


def test_normalization_carries_operator_registration_for_instrumented_builds():
    registration = {
        "registration_id": "registration-release-build-dcc7214165f84520844800adb34668e6-instrumented",
        "implementation": "cardano-node",
        "source_build": {"variant": "instrumented", "execution_id": "release-build-dcc7214165f84520844800adb34668e6"},
    }
    substrate = {
        "profile_id": "profile-n-cardano-latest-confirmed",
        "nodes": [{
            "id": "node1", "impl": "cardano-node", "version": "11.1.2", "role": "producer",
            "image": "sha256:" + "d" * 64, "source_revision": "fef83fed01d7926f3de83b3b917be5a4a48768b5",
            "operator_registration": registration,
        }],
        "topology": {"edges": []},
    }

    normalized = normalize_substrate(substrate)
    node = normalized["nodes"][0]
    assert node["operator_registration"] == registration
    assert node["target_mode"] == "stock"

    plan = allocate_node_plan(
        normalized, runtime_root=Path("/tmp/dwarf-operator-instrumented"),
        compose_project="dwarf-profile-n-cardano-latest-confirmed",
        base_haskell_port=33001, base_amaru_port=43001,
    )
    assert plan["nodes"][0]["operator_registration"] == registration


def test_cardano_workload_scenario_validates_strictly_with_the_peer_entry():
    import subprocess, sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "dwarf"))
    from scripts import validate_scenarios
    path = root / "dwarf/scenarios/cardano-measurement-workload-stock-portable.yaml"
    schema_fail, schema_msgs = validate_scenarios.check_schema([path])
    assert schema_fail == 0, schema_msgs
    _checked, semantic_fail, semantic_msgs = validate_scenarios.check_semantic([path])
    assert semantic_fail == 0, semantic_msgs
