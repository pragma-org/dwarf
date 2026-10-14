import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from profile_manager.profiles import amaru_control_topology_shape
from profile_manager.topology_health import classify_topology_health
from scripts import runtime_amaru_control_substrate as control

from scripts.runtime_amaru_control_substrate import (
    RuntimeControlError,
    _stage_runtime_assets,
    _gate_result,
    build_runtime_metadata,
    prepare_runtime_model,
    probe_retained_health,
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
        "networks": {
            "default": {"driver": "bridge"},
            "amaru-consumer-net": {"driver": "bridge"},
        },
    }


def _config():
    return {
        "profile_id": "profile-p-mixed-latest-confirmed",
        "scope": "mixed",
        "lifecycle": "cardano_amaru_relay_bootstrap_control",
        "compose_project": "dwarf-profile-p-mixed-latest-confirmed",
        "runtime_root": "/var/lib/dwarf/profiles/profile-p",
        "peer_sharing": False,
        "supporting_cardano_version": "10.7.1",
        "cardano_image": "ghcr.io/intersectmbo/cardano-node@sha256:" + "7" * 64,
        "amaru_version": "10.11.0",
        "amaru_image": "ghcr.io/lambdasistemi/amaru-bootstrap-producer@sha256:" + "8" * 64,
        "controller_node_services": {
            "node1": "p1",
            "node2": "p2",
            "amaru1": "amaru-relay-1",
        },
        "substrate": {
            "scope": "mixed",
            "network_magic": 42,
            "peer_sharing": False,
            "target_node_count": 3,
            "support_node_count": 0,
            "version_policy": "latest-confirmed",
            "version_policy_source": "implicit-default",
            "version_status": "confirmed",
            "deployment_adapter": "amaru-control",
            "catalog_revision": "catalog-sha",
            "catalog_snapshot": {"captured_at": "2026-09-18T00:00:00Z"},
            "controller_ownership": {
                "installation_id": "install",
                "topology_id": "profile-p-mixed-latest-confirmed",
            },
            "docker_host_runtime_root": "/srv/dwarf/controller/topology-runtimes/profile-p-mixed-latest-confirmed/current",
            "nodes": [
                {
                    "id": "node1",
                    "impl": "cardano-node",
                    "version": "10.7.1",
                    "image": "ghcr.io/intersectmbo/cardano-node@sha256:" + "7" * 64,
                    "supporting": False,
                },
                {
                    "id": "node2",
                    "impl": "cardano-node",
                    "version": "10.7.1",
                    "image": "ghcr.io/intersectmbo/cardano-node@sha256:" + "7" * 64,
                    "supporting": False,
                },
                {
                    "id": "amaru1",
                    "impl": "amaru",
                    "version": "10.11.0",
                    "image": "ghcr.io/lambdasistemi/amaru-bootstrap-producer@sha256:" + "8" * 64,
                    "supporting": False,
                },
            ],
        },
    }


def test_runtime_request_rejects_protected_or_wrong_lifecycle():
    config = _config()
    validate_runtime_request(config)

    with pytest.raises(RuntimeControlError, match="protected"):
        validate_runtime_request({**config, "compose_project": "cardano_amaru_relay_bootstrap_control"})
    with pytest.raises(RuntimeControlError, match="lifecycle"):
        validate_runtime_request({**config, "lifecycle": "offline-copy-relaunch"})


def test_runtime_request_rejects_network_magic_other_than_proven_42():
    config = _config()
    config["substrate"]["network_magic"] = 43

    with pytest.raises(RuntimeControlError, match="network magic 42"):
        validate_runtime_request(config)


@pytest.mark.parametrize(
    ("request_value", "substrate_value"),
    [(True, False), (False, True), (None, False), (False, None)],
)
def test_runtime_request_rejects_enabled_missing_or_mismatched_peer_sharing(
    request_value, substrate_value
):
    config = _config()
    config["peer_sharing"] = request_value
    config["substrate"]["peer_sharing"] = substrate_value

    with pytest.raises(RuntimeControlError, match="peer_sharing=false"):
        validate_runtime_request(config)


def test_prepare_runtime_model_is_private_managed_and_keeps_live_producer_path():
    config = _config()
    config["substrate"]["actual_topology"] = amaru_control_topology_shape(
        config["substrate"]
    )
    model = prepare_runtime_model(_baseline(), config)

    for service in model["services"].values():
        assert service["labels"]["ada2.managed"] == "dwarf"
        assert service["labels"]["ada2.profile"] == config["profile_id"]
        assert service["labels"]["io.dwarf.managed"] == "true"
        assert service["labels"]["io.dwarf.installation"] == "install"
        assert service["labels"]["io.dwarf.topology"] == config["profile_id"]
        assert service["labels"]["io.dwarf.hardening.exception"] == (
            "proven-relay-bootstrap-writable-rootfs"
        )
    assert model["services"]["p1"]["labels"]["io.dwarf.node_id"] == "node1"
    assert model["services"]["p2"]["labels"]["io.dwarf.node_id"] == "node2"
    assert model["services"]["amaru-relay-1"]["labels"]["io.dwarf.node_id"] == "amaru1"
    assert model["services"]["p3"]["labels"]["io.dwarf.component"] == "support"
    for network in model["networks"].values():
        assert network["labels"] == {
            "io.dwarf.managed": "true",
            "io.dwarf.installation": "install",
            "io.dwarf.topology": config["profile_id"],
            "io.dwarf.component": "network",
        }
    for volume in model["volumes"].values():
        assert volume["labels"] == {
            "io.dwarf.managed": "true",
            "io.dwarf.installation": "install",
            "io.dwarf.topology": config["profile_id"],
            "io.dwarf.component": "volume",
        }
    relay = model["services"]["amaru-relay-1"]
    assert relay["environment"]["AMARU_PEER"] == "p1.example:3001"
    assert relay["entrypoint"] == "amaru-relay-bootstrap"
    assert model["x-dwarf-retained-runtime"]["lifecycle"] == config["lifecycle"]
    assert model["x-dwarf-retained-runtime"]["fresh_state_required"] is True
    assert set(model["services"]) == set(
        config["substrate"]["actual_topology"]["all_services"]
    )
    assert set(model["networks"]) == set(
        config["substrate"]["actual_topology"]["network_names"]
    )


@pytest.mark.parametrize(
    "ports",
    [
        ["0.0.0.0:39001:3001"],
        ["3001"],
        [{"target": 3001}],
        ["127.0.0.1:8787:3001"],
    ],
)
def test_prepare_runtime_model_rejects_non_loopback_published_ports(ports):
    baseline = _baseline()
    baseline["services"]["p1"]["ports"] = ports

    with pytest.raises(RuntimeControlError, match="loopback"):
        prepare_runtime_model(baseline, _config())


def test_prepare_runtime_model_allows_explicit_loopback_published_port():
    baseline = _baseline()
    baseline["services"]["p1"]["ports"] = ["127.0.0.1:39001:3001"]

    model = prepare_runtime_model(baseline, _config())

    assert model["services"]["p1"]["ports"] == ["127.0.0.1:39001:3001"]


def test_prepare_runtime_model_maps_packaged_binds_to_exact_host_runtime_root(tmp_path):
    package = tmp_path / "package"
    package.mkdir()
    (package / "testnet.yaml").write_text("networkMagic: 42\n", encoding="utf-8")
    config = {
        **_config(),
        "runtime_root": str(tmp_path / "container-state" / "current"),
        "runtime_asset_root": str(
            tmp_path / "container-state" / "current" / "adapter-assets"
        ),
        "docker_host_runtime_asset_root": (
            "/srv/dwarf/controller/topology-runtimes/"
            "profile-p-mixed-latest-confirmed/current/adapter-assets"
        ),
    }
    baseline = _baseline()
    baseline["services"]["configurator"]["volumes"] = [
        f"{package / 'testnet.yaml'}:/testnet.yaml:ro"
    ]

    model = prepare_runtime_model(baseline, config, base_package=package)

    assert model["services"]["configurator"]["volumes"] == [
        "/srv/dwarf/controller/topology-runtimes/"
        "profile-p-mixed-latest-confirmed/current/adapter-assets/"
        "testnet.yaml:/testnet.yaml:ro"
    ]


def test_prepare_runtime_model_rejects_controller_relative_bind_source():
    baseline = _baseline()
    baseline["services"]["configurator"]["volumes"] = [
        "./testnet.yaml:/testnet.yaml:ro"
    ]

    with pytest.raises(RuntimeControlError, match="relative bind"):
        prepare_runtime_model(baseline, _config())


def test_fresh_retained_probe_rejects_running_but_stalled_chain(monkeypatch, tmp_path):
    runtime_root = tmp_path / "current"
    evidence = runtime_root / "evidence"
    evidence.mkdir(parents=True)
    (runtime_root / "docker-compose.json").write_text("{}\n", encoding="utf-8")
    (evidence / "deployment-report.json").write_text(
        json.dumps(
            {
                "passed": True,
                "retained": True,
                "gates": {"fresh_state": True},
                "observation": {"state": "healthy"},
            }
        ),
        encoding="utf-8",
    )
    operator_content_id = "sha256:" + "9" * 64
    config = {
        **_config(),
        "runtime_root": str(runtime_root),
        "sample_seconds": 1,
        "amaru_image": operator_content_id,
        "amaru_runtime_interface": "extracted-binary",
    }
    current = {
        "state": "healthy",
        "observation": {
            "containers": {
                "p1": {"restart_count": 0},
                "amaru-relay-1": {"restart_count": 0},
            },
            "peer_contract": {"amaru_consumer_only_amaru_upstreams": True},
        },
    }
    monkeypatch.setattr(control, "collect_and_classify", lambda **_: current)
    monkeypatch.setattr(
        control, "remember_bootstrap_evidence", lambda _observation, prior: prior
    )
    monkeypatch.setattr(control, "fresh_readiness_proven", lambda *_: False)
    monkeypatch.setattr(
        control,
        "_validated_operator_stock_content_id",
        lambda *_args, **_kwargs: operator_content_id,
    )
    observed_identity_kwargs = []

    def observe_identity(*_args, **kwargs):
        observed_identity_kwargs.append(kwargs)
        return {"matched": True}

    monkeypatch.setattr(control, "_identity_observation", observe_identity)
    monkeypatch.setattr(control, "_compose_logs", lambda *_: "")
    monkeypatch.setattr(control, "_amaru_log_text", lambda *_: "")

    result = probe_retained_health(config, output=evidence / "fresh-health.json")

    assert result["fresh_observation"] is True
    assert result["passed"] is False
    assert result["gates"]["local_reward_window_contract"] is False
    assert "local_reward_window_contract" in result["failed_gates"]
    assert result["gates"]["chain_progress"] is False
    assert result["gates"]["consumer_converged"] is False
    assert observed_identity_kwargs == [
        {
            "scope": config["scope"],
            "cardano_version": config["supporting_cardano_version"],
            "amaru_version": config["amaru_version"],
            "cardano_image": config["cardano_image"],
            "amaru_image": operator_content_id,
            "amaru_runtime_interface": "extracted-binary",
            "measurement_target_identity": None,
            "operator_stock_registration_expected": False,
            "operator_stock_content_id": operator_content_id,
        }
    ]


def test_retained_probe_does_not_fallback_when_operator_registration_invalidates(
    monkeypatch, tmp_path
):
    runtime_root = tmp_path / "runtime"
    evidence_root = runtime_root / "evidence"
    evidence_root.mkdir(parents=True)
    (runtime_root / "docker-compose.json").write_text("{}\n")
    (evidence_root / "deployment-report.json").write_text(
        json.dumps(
            {
                "passed": True,
                "retained": True,
                "gates": {
                    "fresh_state": True,
                    "local_reward_window_contract": True,
                },
                "bootstrap_evidence": {},
            }
        ),
        encoding="utf-8",
    )
    config = {
        **_config(),
        "runtime_root": str(runtime_root),
        "sample_seconds": 1,
        "measurement_target_mode": "stock",
    }
    config["substrate"] = deepcopy(config["substrate"])
    config["substrate"]["operator_registration_id"] = "registration-local-amaru"
    config["substrate"]["operator_overlay_revision"] = "sha256:" + "8" * 64
    monkeypatch.setattr(
        control,
        "collect_and_classify",
        lambda **_kwargs: {"state": "healthy", "observation": {"containers": {}}},
    )
    monkeypatch.setattr(control, "remember_bootstrap_evidence", lambda _o, prior: prior)
    monkeypatch.setattr(control, "fresh_readiness_proven", lambda *_args: True)
    monkeypatch.setattr(control, "_fresh_window_advanced", lambda *_args: True)
    monkeypatch.setattr(
        control, "_validated_operator_stock_content_id", lambda *_args, **_kwargs: None
    )
    observed_identity_kwargs = []

    def observe_identity(*_args, **kwargs):
        observed_identity_kwargs.append(kwargs)
        return {"matched": False}

    monkeypatch.setattr(control, "_identity_observation", observe_identity)
    monkeypatch.setattr(control, "_compose_logs", lambda *_args: "")
    monkeypatch.setattr(control, "_amaru_log_text", lambda *_args: "")

    result = probe_retained_health(config, output=evidence_root / "probe.json")

    assert result["gates"]["exact_identity"] is False
    assert observed_identity_kwargs[0]["operator_stock_registration_expected"] is True
    assert observed_identity_kwargs[0]["operator_stock_content_id"] is None


def test_operator_stock_content_id_requires_current_registration_chain(monkeypatch):
    from profile_manager import deployment_versions, version_discovery

    content_id = "sha256:" + "9" * 64
    revision = "sha256:" + "8" * 64
    registration = {
        "registration_id": "registration-local-amaru",
        "implementation": "amaru",
        "version": "10.11.20260912-local",
        "config_image_id": content_id,
        "platform": "linux/amd64",
        "runtime_interface": "extracted-binary",
    }
    selected = {"revision": revision, "registration": registration}
    monkeypatch.setenv("DWARF_HOST_OS", "linux")
    monkeypatch.setenv("DWARF_HOST_ARCH", "amd64")
    monkeypatch.setattr(
        version_discovery,
        "load_operator_registration",
        lambda registration_id: selected
        if registration_id == registration["registration_id"]
        else None,
    )
    monkeypatch.setattr(
        deployment_versions,
        "operator_registration_release",
        lambda value: {"implementation": value["implementation"]},
    )
    config = {
        "measurement_target_mode": "stock",
        "amaru_version": registration["version"],
        "amaru_runtime_interface": "extracted-binary",
        "substrate": {
            "operator_registration_id": registration["registration_id"],
            "operator_overlay_revision": revision,
            "catalog_snapshot": {
                "operator_registration_id": registration["registration_id"],
                "operator_overlay_revision": revision,
            },
            "nodes": [
                {
                    "id": "amaru1",
                    "impl": "amaru",
                    "supporting": False,
                    "version": registration["version"],
                    "image": content_id,
                    "amaru_runtime_interface": "extracted-binary",
                    "operator_registration": registration,
                }
            ],
        },
    }

    assert (
        control._validated_operator_stock_content_id(config, amaru_image=content_id)
        == content_id
    )

    changed_overlay = deepcopy(config)
    changed_overlay["substrate"]["operator_overlay_revision"] = "sha256:" + "7" * 64
    assert (
        control._validated_operator_stock_content_id(
            changed_overlay, amaru_image=content_id
        )
        is None
    )

    wrong_node_content = deepcopy(config)
    wrong_node_content["substrate"]["nodes"][0]["image"] = "sha256:" + "6" * 64
    assert (
        control._validated_operator_stock_content_id(
            wrong_node_content, amaru_image=content_id
        )
        is None
    )

    wrong_platform = deepcopy(selected)
    wrong_platform["registration"] = {
        **registration,
        "platform": "linux/arm64",
    }
    monkeypatch.setattr(
        version_discovery,
        "load_operator_registration",
        lambda _registration_id: wrong_platform,
    )
    assert (
        control._validated_operator_stock_content_id(config, amaru_image=content_id)
        is None
    )


def test_operator_stock_content_id_accepts_revision_only_ledger_advance(monkeypatch):
    from profile_manager import deployment_versions, version_discovery

    content_id = "sha256:" + "9" * 64
    retained_revision = "sha256:" + "8" * 64
    current_revision = "sha256:" + "7" * 64
    registration = {
        "registration_id": "registration-local-amaru",
        "implementation": "amaru",
        "version": "10.11.20260912-local",
        "config_image_id": content_id,
        "platform": "linux/amd64",
        "runtime_interface": "extracted-binary",
    }
    monkeypatch.setenv("DWARF_HOST_OS", "linux")
    monkeypatch.setenv("DWARF_HOST_ARCH", "amd64")
    monkeypatch.setattr(
        version_discovery,
        "load_operator_registration",
        lambda registration_id: {
            "revision": current_revision,
            "registration": registration,
        }
        if registration_id == registration["registration_id"]
        else None,
    )
    monkeypatch.setattr(
        deployment_versions,
        "operator_registration_release",
        lambda value: {"implementation": value["implementation"]},
    )
    config = {
        "measurement_target_mode": "stock",
        "amaru_version": registration["version"],
        "amaru_runtime_interface": "extracted-binary",
        "substrate": {
            "operator_registration_id": registration["registration_id"],
            "operator_overlay_revision": retained_revision,
            "catalog_snapshot": {
                "operator_registration_id": registration["registration_id"],
                "operator_overlay_revision": retained_revision,
            },
            "nodes": [
                {
                    "id": "amaru1",
                    "impl": "amaru",
                    "supporting": False,
                    "version": registration["version"],
                    "image": content_id,
                    "amaru_runtime_interface": "extracted-binary",
                    "operator_registration": registration,
                }
            ],
        },
    }

    assert (
        control._validated_operator_stock_content_id(config, amaru_image=content_id)
        == content_id
    )


def test_fresh_retained_probe_rejects_mature_aligned_but_frozen_window(
    monkeypatch, tmp_path
):
    required = (
        "p1",
        "p2",
        "p3",
        "relay1",
        "relay2",
        "amaru-relay-1",
        "amaru-relay-2",
        "amaru-consumer",
    )

    def classified(*, bootstrap):
        tips = {
            name: {"slot": 1601, "block": 1601, "hash": "aligned-chain"}
            for name in ("p1", "p2", "p3", "relay1", "relay2", "amaru-consumer")
        }
        relays = {
            name: {
                "current_slot": 1601,
                "highest_slot": 1601,
                "fatal_signatures": [],
                "target_slots": [399, 799, 1199] if bootstrap else [],
                "committed": bootstrap,
                "exec_started": bootstrap,
            }
            for name in ("amaru-relay-1", "amaru-relay-2")
        }
        raw = {
            "schema_version": 1,
            "topology_id": "cardano_amaru",
            "required_services": list(required),
            "containers": {
                name: {
                    "present": True,
                    "running": True,
                    "restart_count": 0,
                    "oom_killed": False,
                    "image": f"example/{name}@sha256:" + "1" * 64,
                    "image_digest": "sha256:" + "1" * 64,
                    "fatal_signatures": [],
                }
                for name in required
            },
            "peer_contract": {"amaru_consumer_only_amaru_upstreams": True},
            "samples": [
                {"tips": json.loads(json.dumps(tips)), "amaru_relays": json.loads(json.dumps(relays))},
                {"tips": json.loads(json.dumps(tips)), "amaru_relays": json.loads(json.dumps(relays))},
            ],
            "tolerances": {"slot_lag": 6, "producer_slot_spread": 6},
        }
        return {**classify_topology_health(raw), "observation": raw}

    prior_health = classified(bootstrap=True)
    frozen_health = classified(bootstrap=False)
    evidence = control.remember_bootstrap_evidence(prior_health, {})
    assert frozen_health["state"] == "healthy"
    assert control.fresh_readiness_proven(frozen_health, evidence) is True
    advancing_raw = json.loads(json.dumps(frozen_health["observation"]))
    for tip in advancing_raw["samples"][-1]["tips"].values():
        tip["slot"] = 1602
        tip["block"] = 1602
        tip["hash"] = "aligned-chain-advanced"
    for relay in advancing_raw["samples"][-1]["amaru_relays"].values():
        relay["current_slot"] = 1602
        relay["highest_slot"] = 1602
    advancing_health = {
        **classify_topology_health(advancing_raw),
        "observation": advancing_raw,
    }
    assert control.fresh_readiness_proven(advancing_health, evidence) is True
    assert control._fresh_window_advanced(advancing_health) is True

    runtime_root = tmp_path / "current"
    evidence_root = runtime_root / "evidence"
    evidence_root.mkdir(parents=True)
    (runtime_root / "docker-compose.json").write_text("{}\n", encoding="utf-8")
    (evidence_root / "deployment-report.json").write_text(
        json.dumps(
            {
                "passed": True,
                "retained": True,
                "gates": {"fresh_state": True},
                "observation": prior_health,
            }
        ),
        encoding="utf-8",
    )
    config = {**_config(), "runtime_root": str(runtime_root), "sample_seconds": 1}
    monkeypatch.setattr(control, "collect_and_classify", lambda **_: frozen_health)
    monkeypatch.setattr(
        control, "_identity_observation", lambda *_args, **_kwargs: {"matched": True}
    )
    monkeypatch.setattr(control, "_compose_logs", lambda *_: "")
    monkeypatch.setattr(control, "_amaru_log_text", lambda *_: "")

    result = probe_retained_health(
        config, output=evidence_root / "frozen-health.json"
    )

    assert result["passed"] is False
    assert result["gates"]["fresh_window_advancement"] is False


@pytest.mark.parametrize("persisted", [True, False])
def test_fresh_retained_probe_preserves_bootstrap_anchors_across_reload(
    monkeypatch, tmp_path, persisted
):
    required = (
        "p1",
        "p2",
        "p3",
        "relay1",
        "relay2",
        "amaru-relay-1",
        "amaru-relay-2",
        "amaru-consumer",
    )

    def classified(*, bootstrap, first_slot, final_slot):
        def sample(slot):
            tips = {
                name: {"slot": slot, "block": slot, "hash": f"chain-{slot}"}
                for name in (
                    "p1",
                    "p2",
                    "p3",
                    "relay1",
                    "relay2",
                    "amaru-consumer",
                )
            }
            relays = {
                name: {
                    "current_slot": slot,
                    "highest_slot": slot,
                    "fatal_signatures": [],
                    "target_slots": [399, 799, 1196] if bootstrap else [],
                    "committed": bootstrap,
                    "exec_started": bootstrap,
                }
                for name in ("amaru-relay-1", "amaru-relay-2")
            }
            return {"tips": tips, "amaru_relays": relays}

        raw = {
            "schema_version": 1,
            "topology_id": "cardano_amaru",
            "required_services": list(required),
            "containers": {
                name: {
                    "present": True,
                    "running": True,
                    "restart_count": 0,
                    "oom_killed": False,
                    "image": f"example/{name}@sha256:" + "1" * 64,
                    "image_digest": "sha256:" + "1" * 64,
                    "fatal_signatures": [],
                }
                for name in required
            },
            "peer_contract": {"amaru_consumer_only_amaru_upstreams": True},
            "samples": [sample(first_slot), sample(final_slot)],
            "tolerances": {"slot_lag": 6, "producer_slot_spread": 6},
        }
        return {**classify_topology_health(raw), "observation": raw}

    bootstrap_health = classified(
        bootstrap=True, first_slot=1195, final_slot=1196
    )
    fresh_health = classified(
        bootstrap=False, first_slot=1601, final_slot=1602
    )
    anchors = control.remember_bootstrap_evidence(bootstrap_health, {})
    runtime_root = tmp_path / "current"
    evidence = runtime_root / "evidence"
    evidence.mkdir(parents=True)
    (runtime_root / "docker-compose.json").write_text("{}\n", encoding="utf-8")
    report = {
        "passed": True,
        "retained": True,
        "gates": {
            "fresh_state": True,
            "local_reward_window_contract": True,
        },
        "observation": fresh_health,
    }
    if persisted:
        report["bootstrap_evidence"] = anchors
    else:
        (evidence / "health-001.json").write_text(
            json.dumps(bootstrap_health), encoding="utf-8"
        )
    (evidence / "deployment-report.json").write_text(
        json.dumps(report), encoding="utf-8"
    )
    config = {**_config(), "runtime_root": str(runtime_root), "sample_seconds": 1}
    monkeypatch.setattr(control, "collect_and_classify", lambda **_: fresh_health)
    monkeypatch.setattr(
        control, "_identity_observation", lambda *_args, **_kwargs: {"matched": True}
    )
    monkeypatch.setattr(control, "_compose_logs", lambda *_: "")
    monkeypatch.setattr(control, "_amaru_log_text", lambda *_: "")

    result = probe_retained_health(config, output=evidence / "fresh-health.json")

    assert result["passed"] is True
    assert result["bootstrap_evidence"] == anchors
    assert result["gates"]["chain_progress"] is True
    assert result["gates"]["consumer_converged"] is True


def test_fresh_retained_probe_rejects_symlinked_evidence_root(tmp_path):
    runtime_root = tmp_path / "current"
    external = tmp_path / "external-evidence"
    runtime_root.mkdir()
    external.mkdir()
    (runtime_root / "docker-compose.json").write_text("{}\n", encoding="utf-8")
    (external / "deployment-report.json").write_text(
        json.dumps({"passed": True, "retained": True}), encoding="utf-8"
    )
    (runtime_root / "evidence").symlink_to(external, target_is_directory=True)
    config = {**_config(), "runtime_root": str(runtime_root)}

    with pytest.raises(RuntimeControlError, match="retained runtime evidence"):
        probe_retained_health(config, output=external / "fresh-health.json")


def test_runtime_assets_are_staged_before_host_path_mapping_and_missing_assets_fail(tmp_path):
    package = control.BASE_PACKAGE
    source_testnet = (package / "testnet.yaml").read_bytes()
    source_globals = (package / "amaru-runtime" / "global-parameters.json").read_bytes()
    source_era = (package / "amaru-runtime" / "era-history.json").read_bytes()
    config = {**_config(), "runtime_root": str(tmp_path / "current")}

    staged = _stage_runtime_assets(config, base_package=package)

    asset_root = Path(staged["runtime_asset_root"])
    assert asset_root.is_dir()
    assert (asset_root / "testnet.yaml").is_file()
    assert staged["docker_host_runtime_asset_root"].endswith(
        "/current/adapter-assets"
    )
    merged = {}
    for document in yaml.safe_load_all(
        (asset_root / "testnet.yaml").read_text(encoding="utf-8")
    ):
        if document:
            merged.update(document)
    globals_doc = json.loads(
        (asset_root / "amaru-runtime" / "global-parameters.json").read_text(
            encoding="utf-8"
        )
    )
    era_doc = json.loads(
        (asset_root / "amaru-runtime" / "era-history.json").read_text(
            encoding="utf-8"
        )
    )
    assert merged["epochLength"] == 400
    assert merged["activeSlotsCoeff"] == 0.2
    assert merged["securityParam"] == 8
    assert merged["protocolConsts"]["k"] == 8
    assert merged["protocolParams"]["rho"] == 0.003
    assert merged["protocolParams"]["tau"] == 0.2
    assert globals_doc["consensus_security_param"] == 8
    assert globals_doc["epoch_length_scale_factor"] == 10
    assert era_doc["stability_window"] == 120
    assert era_doc["eras"][0]["params"]["epoch_size_slots"] == 400
    contract = staged["local_reward_window_contract"]
    assert {key: value for key, value in contract.items() if key != "artifacts"} == {
        "active_slot_coeff": "1/5",
        "active_slot_coeff_inverse": 5,
        "epoch_length": 400,
        "epoch_length_scale_factor": 10,
        "monetary_expansion": "3/1000",
        "randomness_stabilisation_window": 160,
        "reward_pulsing_window": 240,
        "security_parameter": 8,
        "slot_length_ms": 500,
        "stability_window": 120,
        "treasury_cut": "1/5",
        "verified": True,
    }
    assert set(contract["artifacts"]) == {"source_sha256", "staged_sha256"}
    assert all(
        len(digest) == 64
        for group in contract["artifacts"].values()
        for digest in group.values()
    )
    assert contract["artifacts"]["source_sha256"] != contract["artifacts"][
        "staged_sha256"
    ]
    assert (package / "testnet.yaml").read_bytes() == source_testnet
    assert (
        package / "amaru-runtime" / "global-parameters.json"
    ).read_bytes() == source_globals
    assert (package / "amaru-runtime" / "era-history.json").read_bytes() == source_era

    with pytest.raises(RuntimeControlError, match="reward-window contract"):
        control._validate_local_reward_window_contract(
            testnet_path=package / "testnet.yaml",
            global_parameters_path=(
                package / "amaru-runtime" / "global-parameters.json"
            ),
            era_history_path=package / "amaru-runtime" / "era-history.json",
        )

    missing_package = tmp_path / "missing-package"
    missing_package.mkdir()
    with pytest.raises(RuntimeControlError, match="required runtime asset is missing"):
        _stage_runtime_assets(
            {**config, "runtime_root": str(tmp_path / "missing-current")},
            base_package=missing_package,
        )
    assert not (tmp_path / "missing-current" / "adapter-assets").exists()


def test_deploy_stages_assets_before_rendering_or_preparing_runtime(
    tmp_path, monkeypatch
):
    events = []
    config = {**_config(), "runtime_root": str(tmp_path / "current")}

    def stage(current, *, base_package):
        events.append("stage")
        return current

    def render(base_package):
        events.append("render")
        return _baseline()

    def prepare(model, current, *, base_package):
        events.append("prepare")
        raise RuntimeControlError("stop before Docker")

    monkeypatch.setattr(control, "_project_is_fresh", lambda _project: True)
    monkeypatch.setattr(control, "_stage_runtime_assets", stage)
    monkeypatch.setattr(control, "_render_baseline", render)
    monkeypatch.setattr(control, "prepare_runtime_model", prepare)
    monkeypatch.setattr(
        control,
        "_run",
        lambda *_args, **_kwargs: pytest.fail("Docker must not be called"),
    )

    with pytest.raises(RuntimeControlError, match="stop before Docker"):
        control.deploy(config, base_package=tmp_path / "package")

    assert events == ["stage", "render", "prepare"]


def test_main_emits_structured_safe_project_freshness_failure(
    tmp_path, monkeypatch, capsys
):
    config = {**_config(), "runtime_root": str(tmp_path / "current")}
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(control, "_project_is_fresh", lambda _project: False)

    assert control.main(["--config", str(config_path)]) == 2

    assert json.loads(capsys.readouterr().out) == {
        "passed": False,
        "error": "runtime control preflight failed",
        "failure": {
            "code": "compose_project_not_fresh",
            "stage": "preflight",
        },
    }


@pytest.mark.parametrize(
    ("failure", "expected_error"),
    [
        (
            RuntimeControlError(
                "must not be exposed",
                code="runtime_assets_exist",
                stage="preflight",
            ),
            "runtime control preflight failed",
        ),
        (
            RuntimeControlError(
                "must not be exposed",
                code="required_runtime_asset_missing",
                stage="asset_staging",
            ),
            "runtime asset staging failed",
        ),
        (
            RuntimeControlError(
                "must not be exposed",
                code="runtime_asset_permission_denied",
                stage="asset_staging",
            ),
            "runtime asset staging failed",
        ),
        (
            RuntimeControlError(
                "must not be exposed",
                code="operator_registration_identity_invalid",
                stage="retained_request_validation",
            ),
            "operator registration validation failed",
        ),
    ],
)
def test_main_emits_only_allowlisted_early_failure_codes(
    tmp_path, monkeypatch, capsys, failure, expected_error
):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(_config()), encoding="utf-8")
    monkeypatch.setattr(
        control,
        "deploy",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
    )

    assert control.main(["--config", str(config_path)]) == 2

    assert json.loads(capsys.readouterr().out) == {
        "passed": False,
        "error": expected_error,
        "failure": {
            "code": failure.failure_code,
            "stage": failure.failure_stage,
        },
    }


def test_asset_staging_classifies_missing_and_permission_failures(tmp_path, monkeypatch):
    config = {**_config(), "runtime_root": str(tmp_path / "current")}
    missing_package = tmp_path / "missing-package"
    missing_package.mkdir()

    with pytest.raises(RuntimeControlError) as missing:
        _stage_runtime_assets(config, base_package=missing_package)
    assert missing.value.failure_code == "required_runtime_asset_missing"
    assert missing.value.failure_stage == "asset_staging"

    existing_root = tmp_path / "existing-current" / "adapter-assets"
    existing_root.mkdir(parents=True)
    with pytest.raises(RuntimeControlError) as existing:
        _stage_runtime_assets(
            {**config, "runtime_root": str(existing_root.parent)},
            base_package=control.BASE_PACKAGE,
        )
    assert existing.value.failure_code == "runtime_assets_exist"
    assert existing.value.failure_stage == "preflight"

    monkeypatch.setattr(
        Path,
        "mkdir",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            PermissionError("private path")
        ),
    )
    with pytest.raises(RuntimeControlError) as denied:
        _stage_runtime_assets(config, base_package=control.BASE_PACKAGE)
    assert str(denied.value) == "runtime asset staging permission denied"
    assert denied.value.failure_code == "runtime_asset_permission_denied"
    assert denied.value.failure_stage == "asset_staging"


def test_local_reward_window_contract_rejects_amaru_drift_and_lossy_numbers(tmp_path):
    staged = _stage_runtime_assets(
        {**_config(), "runtime_root": str(tmp_path / "current")},
        base_package=control.BASE_PACKAGE,
    )
    asset_root = Path(staged["runtime_asset_root"])
    testnet_path = asset_root / "testnet.yaml"
    globals_path = asset_root / "amaru-runtime" / "global-parameters.json"
    era_path = asset_root / "amaru-runtime" / "era-history.json"

    globals_doc = json.loads(globals_path.read_text(encoding="utf-8"))
    globals_doc["active_slot_coeff_inverse"] = 20
    globals_path.write_text(json.dumps(globals_doc), encoding="utf-8")
    with pytest.raises(RuntimeControlError, match="reward-window contract"):
        control._validate_local_reward_window_contract(
            testnet_path=testnet_path,
            global_parameters_path=globals_path,
            era_history_path=era_path,
        )

    globals_doc["active_slot_coeff_inverse"] = 5
    globals_doc["consensus_security_param"] = 8.5
    globals_path.write_text(json.dumps(globals_doc), encoding="utf-8")
    with pytest.raises(RuntimeControlError, match="exact integer"):
        control._validate_local_reward_window_contract(
            testnet_path=testnet_path,
            global_parameters_path=globals_path,
            era_history_path=era_path,
        )

    globals_doc["consensus_security_param"] = 8
    globals_path.write_text(json.dumps(globals_doc), encoding="utf-8")
    era_doc = json.loads(era_path.read_text(encoding="utf-8"))
    era_doc["eras"].append(dict(era_doc["eras"][0]))
    era_path.write_text(json.dumps(era_doc), encoding="utf-8")
    with pytest.raises(RuntimeControlError, match="one operative Conway era"):
        control._validate_local_reward_window_contract(
            testnet_path=testnet_path,
            global_parameters_path=globals_path,
            era_history_path=era_path,
        )


def test_local_reward_window_contract_rejects_alternate_tuple_and_fractional_ms(
    tmp_path,
):
    staged = _stage_runtime_assets(
        {**_config(), "runtime_root": str(tmp_path / "current")},
        base_package=control.BASE_PACKAGE,
    )
    asset_root = Path(staged["runtime_asset_root"])
    testnet_path = asset_root / "testnet.yaml"
    globals_path = asset_root / "amaru-runtime" / "global-parameters.json"
    era_path = asset_root / "amaru-runtime" / "era-history.json"

    alternate = testnet_path.read_text(encoding="utf-8")
    alternate = alternate.replace("activeSlotsCoeff: 0.2", "activeSlotsCoeff: 0.25")
    alternate = alternate.replace("  k: 8", "  k: 10")
    alternate = alternate.replace("securityParam: 8", "securityParam: 10")
    testnet_path.write_text(alternate, encoding="utf-8")
    globals_doc = json.loads(globals_path.read_text(encoding="utf-8"))
    globals_doc.update(
        {
            "active_slot_coeff_inverse": 4,
            "consensus_security_param": 10,
        }
    )
    globals_path.write_text(json.dumps(globals_doc), encoding="utf-8")

    with pytest.raises(RuntimeControlError, match="approved tuple"):
        control._validate_local_reward_window_contract(
            testnet_path=testnet_path,
            global_parameters_path=globals_path,
            era_history_path=era_path,
        )

    restored = alternate.replace("activeSlotsCoeff: 0.25", "activeSlotsCoeff: 0.2")
    restored = restored.replace("  k: 10", "  k: 8")
    restored = restored.replace("securityParam: 10", "securityParam: 8")
    restored = restored.replace("slotLength: 0.5", "slotLength: 0.5005")
    testnet_path.write_text(restored, encoding="utf-8")
    globals_doc.update(
        {
            "active_slot_coeff_inverse": 5,
            "consensus_security_param": 8,
        }
    )
    globals_path.write_text(json.dumps(globals_doc), encoding="utf-8")

    with pytest.raises(RuntimeControlError, match="exact integer slot milliseconds"):
        control._validate_local_reward_window_contract(
            testnet_path=testnet_path,
            global_parameters_path=globals_path,
            era_history_path=era_path,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda text: text.replace(
            "securityParam: 8", "securityParam: 8\nsecurityParam: 8"
        ),
        lambda text: text.replace("  rho: 0.003", "  rho: 0.003\n  rho: 0.003"),
        lambda text: text + "\n---\nsecurityParam: 8\n",
    ],
)
def test_local_reward_window_contract_rejects_ambiguous_yaml(tmp_path, mutation):
    staged = _stage_runtime_assets(
        {**_config(), "runtime_root": str(tmp_path / "current")},
        base_package=control.BASE_PACKAGE,
    )
    asset_root = Path(staged["runtime_asset_root"])
    testnet_path = asset_root / "testnet.yaml"
    testnet_path.write_text(
        mutation(testnet_path.read_text(encoding="utf-8")), encoding="utf-8"
    )

    with pytest.raises(RuntimeControlError, match="duplicate YAML key"):
        control._validate_local_reward_window_contract(
            testnet_path=testnet_path,
            global_parameters_path=(
                asset_root / "amaru-runtime" / "global-parameters.json"
            ),
            era_history_path=asset_root / "amaru-runtime" / "era-history.json",
        )


def test_amaru_control_rejects_short_kes_override_as_client_incompatible():
    config = {
        **_config(),
        "kes_genesis_override": {
        "slots_per_kes_period": 100,
        "max_kes_evolutions": 12,
        },
    }

    with pytest.raises(RuntimeControlError, match="Cardano-only runtime"):
        validate_runtime_request(config)

def test_live_reward_window_evidence_checks_generated_cardano_and_both_amaru_inputs(
    tmp_path, monkeypatch
):
    staged = _stage_runtime_assets(
        {**_config(), "runtime_root": str(tmp_path / "current")},
        base_package=control.BASE_PACKAGE,
    )
    services = (*control.CARDANO_SERVICES, *control.AMARU_RELAYS)
    containers = {service: f"container-{service}" for service in services}
    monkeypatch.setattr(control, "_project_containers", lambda _project: containers)
    genesis = {
        "epochLength": 400,
        "securityParam": 8,
        "slotLength": 0.5,
        "activeSlotsCoeff": 0.2,
        "maxLovelaceSupply": 45000000000000000,
        "protocolParams": {"rho": 0.003, "tau": 0.2},
    }
    asset_root = Path(staged["runtime_asset_root"])

    def fake_run(args, **_kwargs):
        assert args[:2] == ["docker", "cp"]
        source = args[2].split(":", 1)[1]
        destination = Path(args[3])
        if source.endswith("shelley-genesis.json"):
            destination.write_text(json.dumps(genesis), encoding="utf-8")
        elif source.endswith("global-parameters.json"):
            destination.write_bytes(
                (asset_root / "amaru-runtime" / "global-parameters.json").read_bytes()
            )
        elif source.endswith("era-history.json"):
            destination.write_bytes(
                (asset_root / "amaru-runtime" / "era-history.json").read_bytes()
            )
        else:
            pytest.fail(f"unexpected runtime evidence source: {source}")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(control, "_run", fake_run)
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()

    receipt = control.collect_local_reward_window_evidence(
        staged, project="owned-project", evidence_root=evidence_root
    )

    assert receipt["verified"] is True
    assert set(receipt["cardano_generated_genesis"]) == set(
        control.CARDANO_SERVICES
    )
    assert {
        item["slot_length_ms"]
        for item in receipt["cardano_generated_genesis"].values()
    } == {500}
    assert len(
        {
            item["normalized_sha256"]
            for item in receipt["cardano_generated_genesis"].values()
        }
    ) == 1
    assert set(receipt["amaru_runtime_inputs"]) == set(control.AMARU_RELAYS)
    assert (evidence_root / "local-reward-window" / "receipt.json").is_file()


def test_live_reward_window_evidence_streams_nested_bind_inputs_when_docker_cp_fails(
    tmp_path, monkeypatch
):
    staged = _stage_runtime_assets(
        {**_config(), "runtime_root": str(tmp_path / "current")},
        base_package=control.BASE_PACKAGE,
    )
    services = (*control.CARDANO_SERVICES, *control.AMARU_RELAYS)
    containers = {service: f"container-{service}" for service in services}
    monkeypatch.setattr(control, "_project_containers", lambda _project: containers)
    genesis = {
        "epochLength": 400,
        "securityParam": 8,
        "slotLength": 0.5,
        "activeSlotsCoeff": 0.2,
        "maxLovelaceSupply": 45000000000000000,
        "protocolParams": {"rho": 0.003, "tau": 0.2},
    }
    asset_root = Path(staged["runtime_asset_root"])
    nested_bind_services = {"relay1", "relay2", "amaru-consumer"}
    streamed: list[str] = []

    def fake_run(args, **_kwargs):
        if args[:2] == ["docker", "cp"]:
            container, source = args[2].split(":", 1)
            service = container.removeprefix("container-")
            destination = Path(args[3])
            if service in nested_bind_services and source.endswith(
                "shelley-genesis.json"
            ):
                return subprocess.CompletedProcess(
                    args,
                    1,
                    "",
                    "openat configs/configs/topology.json: read-only file system",
                )
            if source.endswith("shelley-genesis.json"):
                destination.write_text(json.dumps(genesis), encoding="utf-8")
            elif source.endswith("global-parameters.json"):
                destination.write_bytes(
                    (
                        asset_root / "amaru-runtime" / "global-parameters.json"
                    ).read_bytes()
                )
            elif source.endswith("era-history.json"):
                destination.write_bytes(
                    (asset_root / "amaru-runtime" / "era-history.json").read_bytes()
                )
            else:
                pytest.fail(f"unexpected runtime evidence source: {source}")
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:2] == ["docker", "exec"]:
            service = args[2].removeprefix("container-")
            assert service in nested_bind_services
            assert args[3:] == ["cat", "/configs/configs/shelley-genesis.json"]
            streamed.append(service)
            return subprocess.CompletedProcess(args, 0, json.dumps(genesis), "")
        pytest.fail(f"unexpected runtime evidence command: {args}")

    monkeypatch.setattr(control, "_run", fake_run)
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()

    receipt = control.collect_local_reward_window_evidence(
        staged, project="owned-project", evidence_root=evidence_root
    )

    assert set(streamed) == nested_bind_services
    assert {
        item["normalized_sha256"]
        for item in receipt["cardano_generated_genesis"].values()
    } == {next(iter(receipt["cardano_generated_genesis"].values()))["normalized_sha256"]}


def test_live_reward_window_evidence_rejects_any_generated_genesis_drift(
    tmp_path, monkeypatch
):
    staged = _stage_runtime_assets(
        {**_config(), "runtime_root": str(tmp_path / "current")},
        base_package=control.BASE_PACKAGE,
    )
    services = (*control.CARDANO_SERVICES, *control.AMARU_RELAYS)
    containers = {service: f"container-{service}" for service in services}
    monkeypatch.setattr(control, "_project_containers", lambda _project: containers)
    genesis = {
        "epochLength": 400,
        "securityParam": 8,
        "slotLength": 0.5,
        "activeSlotsCoeff": 0.2,
        "maxLovelaceSupply": 45000000000000000,
        "protocolParams": {"rho": 0.003, "tau": 0.2},
    }

    def fake_run(args, **_kwargs):
        source_spec = args[2]
        source = source_spec.split(":", 1)[1]
        destination = Path(args[3])
        if source.endswith("shelley-genesis.json"):
            body = dict(genesis)
            if source_spec.startswith("container-p2:"):
                body["maxLovelaceSupply"] += 1
            destination.write_text(json.dumps(body), encoding="utf-8")
        else:
            pytest.fail(f"unexpected runtime evidence source: {source}")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(control, "_run", fake_run)
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()

    with pytest.raises(RuntimeControlError, match="genesis digest mismatch"):
        control.collect_local_reward_window_evidence(
            staged, project="owned-project", evidence_root=evidence_root
        )

def test_patched_binary_artifact_keeps_proven_wrapper_and_uses_extractor():
    config = {
        **_config(),
        "amaru_image": "dwarf/amaru-measurement@sha256:" + "9" * 64,
        "measurement_target_mode": "patched",
        "amaru_json_traces": True,
        "amaru_runtime_interface": "extracted-binary",
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

    for service_name in ("amaru-relay-1", "amaru-relay-2"):
        relay = model["services"][service_name]
        assert relay["entrypoint"] == "amaru-relay-bootstrap"
        assert relay["environment"]["AMARU_BIN"] == "/target/amaru"
        assert relay["environment"]["AMARU_MIGRATE_CHAIN_DB"] == "true"
        assert relay["environment"]["AMARU_PEER_MIX"] == "static!1"
        assert relay["environment"]["AMARU_UPSTREAM_PEERS"] == "1"
        assert relay["environment"]["AMARU_WITH_JSON_TRACES"] == "true"
        assert relay["image"].startswith(
            "ghcr.io/lambdasistemi/amaru-bootstrap-producer@"
        )
    assert model["services"]["amaru-target-extract"]["image"] == config["amaru_image"]


def test_official_modern_stock_image_shape_matches_rendered_extractor_service():
    image = "ghcr.io/pragma-org/amaru@sha256:" + "9" * 64
    config = _config()
    config["amaru_image"] = image
    config["substrate"]["nodes"][-1]["image"] = image
    config["substrate"]["actual_topology"] = amaru_control_topology_shape(
        config["substrate"]
    )

    model = prepare_runtime_model(_baseline(), config)

    assert "amaru-target-extract" in model["services"]
    assert set(model["services"]) == set(
        config["substrate"]["actual_topology"]["all_services"]
    )


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
        "amaru_json_traces": True,
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
    assert metadata["amaru_json_traces"] is True
    assert "amaru-target-extract" in metadata["actual_topology"][
        "additional_services"
    ]

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


def test_imported_patched_request_accepts_verified_config_content_id():
    config_digest = "sha256:" + "4" * 64
    manifest_digest = "sha256:" + "9" * 64
    source_reference = "dwarf/amaru-measurement@" + manifest_digest
    identity = {
        "version": "10.11.0",
        "source_revision": "b" * 40,
        "target_mode": "patched",
        "image": config_digest,
        "image_digest": config_digest,
        "image_config_digest": config_digest,
        "image_manifest_digest": manifest_digest,
        "qualification_image_id": "sha256:" + "6" * 64,
        "source_image_reference": source_reference,
        "transfer_source_installation_id": "install-source",
        "transfer_manifest_sha256": "sha256:" + "5" * 64,
        "executable_digest": "sha256:" + "e" * 64,
        "patch_set_sha256": "7" * 64,
        "build_result_sha256": "sha256:" + "a" * 64,
    }
    config = {
        **_config(),
        "amaru_image": config_digest,
        "measurement_target_mode": "patched",
        "amaru_runtime_interface": "extracted-binary",
        "measurement_target_identity": identity,
    }

    validate_runtime_request(config)

    with pytest.raises(RuntimeControlError, match="source installation"):
        validate_runtime_request(
            {
                **config,
                "measurement_target_identity": {
                    **identity,
                    "transfer_source_installation_id": "INVALID SOURCE",
                },
            }
        )

    with pytest.raises(RuntimeControlError, match="manifest"):
        validate_runtime_request(
            {
                **config,
                "measurement_target_identity": {
                    **identity,
                    "image_manifest_digest": "sha256:" + "8" * 64,
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
    assert metadata["amaru_json_traces"] is False
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
    assert metadata["actual_topology"]["selected_nodes"] == [
        {
            "id": "node1",
            "service": "p1",
            "implementation": "cardano-node",
            "version": "10.7.1",
            "image": config["cardano_image"],
            "supporting": False,
        },
        {
            "id": "node2",
            "service": "p2",
            "implementation": "cardano-node",
            "version": "10.7.1",
            "image": config["cardano_image"],
            "supporting": False,
        },
        {
            "id": "amaru1",
            "service": "amaru-relay-1",
            "implementation": "amaru",
            "version": "10.11.0",
            "image": config["amaru_image"],
            "supporting": False,
        },
    ]
    assert "amaru-relay-2" in metadata["actual_topology"]["additional_services"]
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
        "local_reward_window_contract": True,
        "exact_identity": True,
        "required_services": True,
        "chain_progress": True,
        "fresh_window_advancement": True,
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
    assert 'test("^[0-9a-f]{64}$$")' in activator["command"][0]
    assert "PlutusV2 transaction id was malformed" in activator["command"][0]
    assert "record_stage txid-validated" in activator["command"][0]
    assert "record_stage inclusion-timeout" in activator["command"][0]
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


def test_plutus_v2_failure_retention_copies_only_safe_diagnostics(tmp_path, monkeypatch):
    copied = []

    monkeypatch.setattr(
        control,
        "_project_containers",
        lambda _project: {
            "plutus-v2-activate": "owned-activator",
            "p1": "owned-producer",
        },
    )

    def fake_run(command, *, timeout):
        assert timeout == 30
        if command[:3] == ["docker", "inspect", "--format"]:
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps(
                    {
                        "Status": "exited",
                        "ExitCode": 43,
                        "OOMKilled": False,
                        "StartedAt": "2026-10-10T23:53:00Z",
                        "FinishedAt": "2026-10-11T00:08:00Z",
                    }
                ),
                "",
            )
        if command[:3] == ["docker", "logs", "--tail"]:
            return subprocess.CompletedProcess(
                command, 0, "", "PlutusV2 update transaction was not included\n"
            )
        if command[:3] == ["docker", "exec", "owned-producer"]:
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps(
                    {
                        "slot": 1308,
                        "block": 320,
                        "epoch": 3,
                        "era": "Conway",
                        "syncProgress": "100.00",
                    }
                ),
                "",
            )
        source = command[2]
        destination = Path(command[3])
        copied.append(source)
        if source.endswith("/txid.json"):
            destination.write_text(
                json.dumps({"txhash": "a" * 64}) + "\n", encoding="utf-8"
            )
            return subprocess.CompletedProcess(command, 0, "", "")
        if source.endswith("/activation-status.json"):
            destination.write_text(
                json.dumps({"stage": "inclusion-timeout"}) + "\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 1, "", "missing")

    monkeypatch.setattr(control, "_run", fake_run)

    receipt = control.retain_plutus_v2_failure_evidence(
        project="owned-project", evidence_root=tmp_path
    )

    assert receipt["retained"] is True
    assert receipt["activator_state"]["exit_code"] == 43
    assert receipt["producer_tip"]["slot"] == 1308
    assert receipt["total_bytes"] <= receipt["budget_bytes"]
    assert [item["name"] for item in receipt["artifacts"]] == [
        "activator-state.json",
        "activator-stderr.txt",
        "producer-tip.json",
        "activation-status.json",
        "txid.json",
    ]
    assert all(len(item["sha256"]) == 64 for item in receipt["artifacts"])
    assert not any("skey" in source or "update.tx" in source for source in copied)


def test_plutus_v2_failure_retention_is_bounded_when_activator_is_absent(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(control, "_project_containers", lambda _project: {})

    receipt = control.retain_plutus_v2_failure_evidence(
        project="owned-project", evidence_root=tmp_path
    )

    assert receipt == {
        "retained": False,
        "reason": "activation-container-unavailable",
        "artifacts": [],
        "activator_state": None,
        "producer_tip": None,
        "total_bytes": 0,
        "budget_bytes": control._PLUTUS_V2_FAILURE_BUDGET_BYTES,
    }
    assert not (tmp_path / "plutus-v2-failure").exists()


def test_plutus_v2_failure_retention_discards_artifacts_over_total_budget(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(control, "_PLUTUS_V2_FAILURE_BUDGET_BYTES", 512)
    monkeypatch.setattr(
        control,
        "_project_containers",
        lambda _project: {"plutus-v2-activate": "owned-activator"},
    )

    def fake_run(command, *, timeout):
        assert timeout == 30
        if command[:3] == ["docker", "inspect", "--format"]:
            return subprocess.CompletedProcess(
                command, 0, '{"Status":"exited","ExitCode":43}', ""
            )
        if command[:3] == ["docker", "logs", "--tail"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        destination = Path(command[3])
        destination.write_bytes(b"x" * 513)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(control, "_run", fake_run)

    receipt = control.retain_plutus_v2_failure_evidence(
        project="owned-project", evidence_root=tmp_path
    )

    assert receipt["retained"] is True
    assert receipt["total_bytes"] <= 512
    assert [item["name"] for item in receipt["artifacts"]] == [
        "activator-state.json"
    ]


def test_deploy_retains_plutus_v2_failure_evidence_before_teardown(
    tmp_path, monkeypatch
):
    events = []
    config = {
        **_config(),
        "runtime_root": str(tmp_path / "current"),
        "plutus_v2_genesis": True,
    }
    monkeypatch.setattr(control, "validate_runtime_request", lambda _config: None)
    monkeypatch.setattr(control, "_project_is_fresh", lambda _project: True)
    monkeypatch.setattr(
        control,
        "_stage_runtime_assets",
        lambda current, *, base_package: current,
    )
    monkeypatch.setattr(control, "_render_baseline", lambda _base_package: _baseline())
    monkeypatch.setattr(
        control,
        "prepare_runtime_model",
        lambda model, _config, *, base_package: model,
    )

    def retain(*, project, evidence_root):
        events.append("retain")
        return {
            "retained": True,
            "reason": None,
            "artifacts": [{"name": "txid.json", "sha256": "a" * 64}],
            "activator_state": {"exit_code": 43},
            "producer_tip": {"slot": 1308},
            "total_bytes": 100,
            "budget_bytes": 262144,
        }

    def fake_run(command, *, timeout):
        if "up" in command:
            events.append("up")
            return subprocess.CompletedProcess(command, 1, "", "startup failed")
        if "down" in command:
            events.append("down")
            return subprocess.CompletedProcess(command, 0, "", "")
        pytest.fail(f"unexpected command: {command}")

    monkeypatch.setattr(control, "retain_plutus_v2_failure_evidence", retain)
    monkeypatch.setattr(control, "_run", fake_run)

    result = control.deploy(config, base_package=tmp_path)

    assert events == ["up", "retain", "down"]
    assert result["passed"] is False
    assert result["plutus_v2_failure_evidence"] == {
        "retained": True,
        "reason": None,
        "artifacts": [{"name": "txid.json", "sha256": "a" * 64}],
        "activator_state": {"exit_code": 43},
        "producer_tip": {"slot": 1308},
        "total_bytes": 100,
        "budget_bytes": 262144,
    }


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
