import json

import pytest

from scripts.runtime_amaru_control_substrate import (
    RuntimeControlError,
    _gate_result,
    build_runtime_metadata,
    prepare_runtime_model,
    validate_runtime_request,
    verify_plutus_v2_evidence,
)


def _baseline():
    cardano = {"image": "old-cardano", "container_name": "cardano"}
    amaru = {
        "image": "old-amaru",
        "container_name": "amaru",
        "entrypoint": "amaru-relay-bootstrap",
        "environment": {"AMARU_PEER": "p1.example:3001"},
    }
    return {
        "services": {
            "configurator": {"image": "configurator"},
            "tracer": {"image": "tracer"},
            "tracer-sidecar": {"image": "tracer-sidecar"},
            "log-tailer": {"image": "log-tailer"},
            "sidecar": {"image": "sidecar"},
            "p1": dict(cardano),
            "p2": dict(cardano),
            "p3": dict(cardano),
            "relay1": dict(cardano),
            "relay2": dict(cardano),
            "amaru-consumer": dict(cardano),
            "amaru-consumer-seed": {"image": "bootstrap"},
            "amaru-relay-1": dict(amaru),
            "amaru-relay-2": {
                **amaru,
                "environment": {"AMARU_PEER": "p2.example:3001"},
            },
        },
        "volumes": {"p1-state": {}, "amaru-state": {}},
        "networks": {"default": {"driver": "bridge"}},
    }


def _config():
    return {
        "profile_id": "profile-p-mixed-latest-confirmed",
        "scope": "mixed",
        "lifecycle": "cardano_amaru_relay_bootstrap_control",
        "compose_project": "dwarf-profile-p-mixed-latest-confirmed",
        "runtime_root": "/var/lib/dwarf/profiles/profile-p",
        "supporting_cardano_version": "10.7.1",
        "cardano_image": "ghcr.io/intersectmbo/cardano-node@sha256:" + "7" * 64,
        "amaru_version": "10.11.0",
        "amaru_image": "ghcr.io/lambdasistemi/amaru-bootstrap-producer@sha256:" + "8" * 64,
        "substrate": {
            "scope": "mixed",
            "target_node_count": 3,
            "support_node_count": 0,
            "version_policy": "latest-confirmed",
            "version_policy_source": "implicit-default",
            "version_status": "confirmed",
            "deployment_adapter": "amaru-control",
            "catalog_revision": "catalog-sha",
            "catalog_snapshot": {"captured_at": "2026-09-18T00:00:00Z"},
        },
    }


def test_runtime_request_rejects_protected_or_wrong_lifecycle():
    config = _config()
    validate_runtime_request(config)

    with pytest.raises(RuntimeControlError, match="protected"):
        validate_runtime_request({**config, "compose_project": "cardano_amaru_relay_bootstrap_control"})
    with pytest.raises(RuntimeControlError, match="lifecycle"):
        validate_runtime_request({**config, "lifecycle": "offline-copy-relaunch"})


def test_prepare_runtime_model_is_private_managed_and_keeps_live_producer_path():
    config = _config()
    model = prepare_runtime_model(_baseline(), config)

    for service in model["services"].values():
        assert service["labels"]["ada2.managed"] == "dwarf"
        assert service["labels"]["ada2.profile"] == config["profile_id"]
    relay = model["services"]["amaru-relay-1"]
    assert relay["environment"]["AMARU_PEER"] == "p1.example:3001"
    assert relay["entrypoint"] == "amaru-relay-bootstrap"
    assert model["x-dwarf-retained-runtime"]["lifecycle"] == config["lifecycle"]
    assert model["x-dwarf-retained-runtime"]["fresh_state_required"] is True


def test_patched_binary_artifact_keeps_proven_wrapper_and_uses_extractor():
    config = {
        **_config(),
        "amaru_image": "dwarf/amaru-measurement@sha256:" + "9" * 64,
        "measurement_target_mode": "patched",
        "amaru_runtime_interface": "extracted-binary",
        "amaru_json_traces": True,
        "measurement_target_identity": {
            "version": "10.11.0",
            "source_revision": "b" * 40,
            "target_mode": "patched",
            "image": "dwarf/amaru-measurement@sha256:" + "9" * 64,
            "image_digest": "sha256:" + "9" * 64,
            "executable_digest": "sha256:" + "e" * 64,
            "patch_set_sha256": "7" * 64,
            "build_result_sha256": "sha256:" + "a" * 64,
        },
    }

    model = prepare_runtime_model(_baseline(), config)

    relay = model["services"]["amaru-relay-1"]
    assert relay["entrypoint"] == "amaru-relay-bootstrap"
    assert relay["environment"]["AMARU_BIN"] == "/target/amaru"
    assert relay["environment"]["AMARU_WITH_JSON_TRACES"] == "true"
    assert relay["image"].startswith("ghcr.io/lambdasistemi/amaru-bootstrap-producer@")
    assert model["services"]["amaru-target-extract"]["image"] == config["amaru_image"]


def test_patched_request_requires_coherent_measurement_identity_and_metadata_retains_it():
    identity = {
        "version": "10.11.0",
        "source_revision": "b" * 40,
        "target_mode": "patched",
        "image": "dwarf/amaru-measurement@sha256:" + "9" * 64,
        "image_digest": "sha256:" + "9" * 64,
        "executable_digest": "sha256:" + "e" * 64,
        "patch_set_sha256": "7" * 64,
        "build_result_sha256": "sha256:" + "a" * 64,
    }
    config = {
        **_config(),
        "amaru_image": identity["image"],
        "measurement_target_mode": "patched",
        "amaru_runtime_interface": "extracted-binary",
        "measurement_target_identity": identity,
    }
    validate_runtime_request(config)

    metadata = build_runtime_metadata(
        config,
        identity={"matched": True},
        observation={"state": "healthy"},
        compose_file="/runtime/docker-compose.json",
    )
    assert metadata["measurement_target"] == identity

    with pytest.raises(RuntimeControlError, match="measurement_target_identity"):
        validate_runtime_request({**config, "measurement_target_identity": {}})
    with pytest.raises(RuntimeControlError, match="does not match amaru_image"):
        validate_runtime_request(
            {
                **config,
                "measurement_target_identity": {
                    **identity,
                    "image": "dwarf/other@sha256:" + "8" * 64,
                },
            }
        )


def test_runtime_metadata_discloses_logical_targets_and_actual_support_topology():
    config = _config()
    metadata = build_runtime_metadata(
        config,
        identity={"matched": True, "services": {"p1": {"matched": True}}},
        observation={"state": "healthy", "observation": {"containers": {"p1": {}}}},
        compose_file="/var/lib/dwarf/profiles/profile-p/docker-compose.json",
    )

    assert metadata["lifecycle"] == "cardano_amaru_relay_bootstrap_control"
    assert metadata["target_implementation"] == "mixed"
    assert metadata["logical_target_count"] == 3
    assert metadata["actual_topology"]["cardano_services"] == [
        "p1", "p2", "p3", "relay1", "relay2", "amaru-consumer"
    ]
    assert metadata["actual_topology"]["amaru_services"] == [
        "amaru-relay-1", "amaru-relay-2"
    ]
    assert metadata["actual_topology"]["bootstrap_strategy"] == (
        "per-relay-safe-snapshot-of-live-producer"
    )
    assert metadata["actual_topology"]["bootstrap_sources"] == {
        "amaru-relay-1": "p1",
        "amaru-relay-2": "p2",
    }
    assert "bootstrap_service" not in metadata["actual_topology"]
    assert metadata["compose_file"].endswith("docker-compose.json")
    assert metadata["identity"]["matched"] is True
    assert metadata["versions"]["policy_source"] == "implicit-default"
    assert metadata["versions"]["deployment_adapter"] == "amaru-control"


def test_plutus_v2_request_fails_closed_without_an_exact_pinned_model(tmp_path):
    config = {**_config(), "plutus_v2_genesis": True}

    with pytest.raises(RuntimeControlError, match="cost model path"):
        validate_runtime_request(config)

    model = tmp_path / "model.json"
    model.write_text("[1,2,3]", encoding="utf-8")
    with pytest.raises(RuntimeControlError, match="sha256"):
        validate_runtime_request(
            {
                **config,
                "plutus_v2_cost_model_path": str(model),
                "plutus_v2_cost_model_sha256": "0" * 64,
            }
        )


def test_live_plutus_v2_evidence_is_a_retention_gate():
    core = {
        "fresh_state": True,
        "exact_identity": True,
        "required_services": True,
        "chain_progress": True,
        "peer_formation": True,
        "consumer_amaru_only_path": True,
        "consumer_converged": True,
        "no_fatal_signatures": True,
        "no_restart_loop": True,
        "plutus_v2_live_parameters": False,
    }

    result = _gate_result(core)

    assert result["passed"] is False
    assert result["failed_gates"] == ["plutus_v2_live_parameters"]


def test_plutus_v2_model_is_injected_only_into_additive_runtime(tmp_path):
    model_path = tmp_path / "plutus-v2.json"
    model_path.write_text("[1,2,3]", encoding="utf-8")
    import hashlib

    digest = hashlib.sha256(model_path.read_bytes()).hexdigest()
    config = {
        **_config(),
        "plutus_v2_genesis": True,
        "plutus_v2_cost_model_path": str(model_path),
        "plutus_v2_cost_model_sha256": digest,
    }
    baseline = _baseline()
    baseline["services"]["configurator"].update(
        {
            "command": ["set -euo pipefail\n/configurator.sh\n"],
            "volumes": ["p1-configs:/configs/1"],
        }
    )

    prepared = prepare_runtime_model(baseline, config)
    configurator = prepared["services"]["configurator"]

    assert f"{model_path}:/dwarf/plutus-v2-cost-model.json:ro" in configurator["volumes"]
    assert "--slurpfile model /dwarf/plutus-v2-cost-model.json" in configurator["command"][0]
    assert "for dir in /configs/[123]" in configurator["command"][0]
    assert ".dRepVotingThresholds.ppTechnicalGroup = 0" in configurator["command"][0]
    assert ".poolVotingThresholds |= with_entries" not in configurator["command"][0]
    assert ".constitution.script = null" in configurator["command"][0]
    activator = prepared["services"]["plutus-v2-activate"]
    assert activator["image"] == config["cardano_image"]
    assert activator["depends_on"] == {"p1": {"condition": "service_started"}}
    assert "create-protocol-parameters-update" in activator["command"][0]
    assert "conway transaction build" in activator["command"][0]
    assert "hash anchor-data --url" in activator["command"][0]
    assert 'gov_deposit=$$(jq -r' in activator["command"][0]
    assert "active_epoch=$$(jq" in activator["command"][0]
    assert "active_in_epoch: $$active_epoch" in activator["command"][0]
    assert prepared["services"]["amaru-consumer-seed"]["depends_on"][
        "plutus-v2-activate"
    ] == {"condition": "service_completed_successfully"}
    unchanged = prepare_runtime_model(_baseline(), _config())
    assert not any(
        "/dwarf/plutus-v2-cost-model.json" in str(item)
        for item in unchanged["services"]["configurator"].get("volumes", [])
    )
    assert "plutus_v2_genesis" not in unchanged["x-dwarf-retained-runtime"]


def test_plutus_v2_evidence_requires_all_genesis_and_live_protocol_parameters(tmp_path):
    model = list(range(175))
    pinned = tmp_path / "pinned.json"
    pinned.write_text(json.dumps(model, separators=(",", ":")), encoding="utf-8")
    genesis_paths = []
    conway_paths = []
    for index in range(1, 4):
        path = tmp_path / f"alonzo-{index}.json"
        path.write_text(json.dumps({"costModels": {"PlutusV2": model}}), encoding="utf-8")
        genesis_paths.append(path)
        conway = tmp_path / f"conway-{index}.json"
        conway.write_text(
            json.dumps(
                {
                    "dRepVotingThresholds": {
                        "ppTechnicalGroup": 0,
                        "ppNetworkGroup": 0.67,
                    },
                    "poolVotingThresholds": {"ppSecurityGroup": 0.51},
                    "committee": {"members": {}, "threshold": 0},
                    "committeeMinSize": 0,
                    "govActionDeposit": 100000000000,
                    "constitution": {"script": None},
                }
            ),
            encoding="utf-8",
        )
        conway_paths.append(conway)
    live = tmp_path / "protocol-parameters.json"
    live.write_text(json.dumps({"costModels": {"PlutusV2": model}}), encoding="utf-8")

    evidence = verify_plutus_v2_evidence(
        pinned_model_path=pinned,
        expected_sha256=__import__("hashlib").sha256(pinned.read_bytes()).hexdigest(),
        genesis_paths=genesis_paths,
        conway_genesis_paths=conway_paths,
        live_protocol_parameters_path=live,
    )

    assert evidence["verified"] is True
    assert evidence["cost_model_entry_count"] == 175
    assert len(evidence["generated_genesis"]) == 6
    assert evidence["live_protocol_parameters"]["sha256"]

    bad = json.loads(live.read_text())
    bad["costModels"]["PlutusV2"][10] = -1
    live.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(RuntimeControlError, match="live PlutusV2 cost model"):
        verify_plutus_v2_evidence(
            pinned_model_path=pinned,
            expected_sha256=__import__("hashlib").sha256(pinned.read_bytes()).hexdigest(),
            genesis_paths=genesis_paths,
            conway_genesis_paths=conway_paths,
            live_protocol_parameters_path=live,
        )
