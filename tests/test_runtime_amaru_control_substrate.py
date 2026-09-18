import pytest

from scripts.runtime_amaru_control_substrate import (
    RuntimeControlError,
    build_runtime_metadata,
    prepare_runtime_model,
    validate_runtime_request,
)


def _baseline():
    cardano = {"image": "old-cardano", "container_name": "cardano"}
    amaru = {
        "image": "old-amaru",
        "container_name": "amaru",
        "command": [
            "-c",
            "exec /bin/amaru node run --peer-address relay1.example:3001",
        ],
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
            "bootstrap-producer": {"image": "bootstrap"},
            "amaru-consumer-seed": {"image": "bootstrap"},
            "amaru-relay-1": dict(amaru),
            "amaru-relay-2": {
                **amaru,
                "command": [
                    "-c",
                    "exec /bin/amaru node run --peer-address relay2.example:3001",
                ],
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
            "version_status": "confirmed",
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
    assert "p1.example:3001" in relay["command"][1]
    assert "relay1.example:3001" not in relay["command"][1]
    assert "exec /bin/amaru node run" in relay["command"][1]
    assert model["x-dwarf-retained-runtime"]["lifecycle"] == config["lifecycle"]
    assert model["x-dwarf-retained-runtime"]["fresh_state_required"] is True


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
    assert metadata["compose_file"].endswith("docker-compose.json")
    assert metadata["identity"]["matched"] is True
