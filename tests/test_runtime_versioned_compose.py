import hashlib
import json
import os
import shutil
import socket
import subprocess
import uuid
from pathlib import Path

import pytest

from scripts import runtime_compose_substrate as compose
from scripts import runtime_amaru_bootstrap_synth as bootstrap
from scripts import runtime_substrate_common as common


def test_amaru_bootstrap_assets_are_bundled_and_loader_is_digest_pinned():
    assert (bootstrap.AMARU_TESTNET_DIR / "cardano-loader.sh").is_file()
    assert (bootstrap.AMARU_TESTNET_DIR / "amaru-loader.sh").is_file()
    assert "@sha256:" in bootstrap.DEFAULT_LOADER_BASE_IMAGE
    assert "@sha256:" in bootstrap.DEFAULT_BOOTSTRAP_PRODUCER_IMAGE


def test_cardano_only_short_kes_records_effective_generated_genesis(tmp_path):
    genesis_path = tmp_path / "shelley-genesis.json"
    (tmp_path / "byron-genesis.json").write_text(
        json.dumps({"startTime": 0}), encoding="utf-8"
    )
    genesis_path.write_text(
        json.dumps(
            {
                "slotsPerKESPeriod": 129600,
                "maxKESEvolutions": 62,
                "networkMagic": 42,
            }
        ),
        encoding="utf-8",
    )
    refresh = compose._refresh_cardano_testnet_start_times(env_root=tmp_path)
    assert refresh["changed"] is True

    evidence = compose._apply_kes_genesis_override_to_shelley(
        genesis_path=genesis_path,
        override={"slots_per_kes_period": 100, "max_kes_evolutions": 15},
    )

    effective = json.loads(genesis_path.read_text(encoding="utf-8"))
    assert effective["slotsPerKESPeriod"] == 100
    assert effective["maxKESEvolutions"] == 15
    assert evidence["window_slots"] == 1500
    assert evidence["effective_shelley_genesis_path"] == str(genesis_path)
    assert len(evidence["effective_shelley_genesis_sha256"]) == 64
    assert evidence["effective_shelley_genesis_sha256"] == hashlib.sha256(
        genesis_path.read_bytes()
    ).hexdigest()


def test_staged_loader_replaces_only_reviewed_active_guardrails_after_pinned_copy(
    tmp_path,
):
    genesis_path = tmp_path / "conway-genesis.json"
    original = "fa24fb305126805cf2164c161d852a0e7330cf988f1fe558cf7d4a64"
    genesis_path.write_text(
        json.dumps(
            {
                "constitution": {
                    "anchor": {"url": "", "dataHash": "0" * 64},
                },
                "govActionDeposit": 100_000_000_000,
                "minFeeRefScriptCostPerByte": 15,
            }
        ),
        encoding="utf-8",
    )

    original_bytes = genesis_path.read_bytes()
    request = compose._prepare_governance_guardrails_fixture_request(
        fixture={
            "name": "always-succeeds-v3",
            "expected_active_script_hash": original,
        },
    )

    assert genesis_path.read_bytes() == original_bytes
    assert request["expected_source_script_hash"] == original
    assert request["effective_script_hash"] == (
        "186e32faa80a26810392fda6d559c7ed4721a65ce1c9d4ef3e1c87b4"
    )
    configs = tmp_path / "configs"
    pinned_genesis = configs / "1/configs/conway-genesis.json"
    pinned_genesis.parent.mkdir(parents=True)
    pinned_genesis.write_text(
        json.dumps(
            {
                "constitution": {
                    "anchor": {"url": None, "dataHash": None},
                    "script": original,
                },
                "govActionDeposit": 100_000_000_000,
                "minFeeRefScriptCostPerByte": 15,
            }
        ),
        encoding="utf-8",
    )
    request_path = tmp_path / "governance-guardrails.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    evidence_path = tmp_path / "governance-guardrails-applied.json"
    scripts = tmp_path / "scripts"
    bootstrap._stage_loader_scripts(scripts)
    subprocess.run(
        [
            "bash",
            str(scripts / "apply-governance-guardrails.sh"),
            str(request_path),
            str(evidence_path),
            str(configs),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    body = json.loads(pinned_genesis.read_text(encoding="utf-8"))
    assert body["constitution"]["script"] == request["effective_script_hash"]
    assert body["govActionDeposit"] == 100_000_000_000
    assert body["minFeeRefScriptCostPerByte"] == 15
    assert evidence["conway_genesis_sha256"] == hashlib.sha256(
        pinned_genesis.read_bytes()
    ).hexdigest()
    assert evidence["config_count"] == 1
    validated = bootstrap._validated_governance_guardrails_evidence(
        {
            "governance_guardrails_request": str(request_path),
            "generated_root": str(tmp_path),
            "config_roots": {"1": str(configs / "1")},
        }
    )
    assert validated == evidence

    assert evidence["prior_script_hash"] == original


def test_staged_legacy_loader_keeps_its_supported_header_import_contract(tmp_path):
    scripts = tmp_path / "scripts"

    bootstrap._stage_loader_scripts(scripts)

    body = (scripts / "amaru-loader.sh").read_text(encoding="utf-8")
    assert "amaru import-headers --network ${NETWORK_NAME}" in body
    assert "--config-dir ${BASEDIR}" in body
    assert "--header-file" not in body

    cardano_body = (scripts / "cardano-loader.sh").read_text(encoding="utf-8")
    assert 'cp -fr /data/p${POOL_ID}-config/configs/* /configs/${POOL_ID}/' in cardano_body


def test_staged_loader_funds_generated_credential_after_pinned_config_copy(tmp_path):
    scripts = tmp_path / "scripts"
    configs = tmp_path / "configs"
    generated = tmp_path / "generated"
    request = tmp_path / "workload-funding.json"
    evidence = generated / "workload-funding-applied.json"
    genesis = configs / "1" / "configs" / "shelley-genesis.json"
    genesis.parent.mkdir(parents=True)
    generated.mkdir()
    genesis.write_text(
        json.dumps(
            {
                "initialFunds": {"60" + "1" * 56: 120_000_000_000},
                "maxLovelaceSupply": 45_000_000_000_000_000,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    request.write_text(
        json.dumps(
            {
                "address": "addr_test1funded",
                "address_base16": "60" + "2" * 56,
                "verification_key": "utxo-keys/utxo1/utxo.vkey",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    bootstrap._stage_loader_scripts(scripts)
    subprocess.run(
        [
            "bash",
            str(scripts / "apply-workload-funding.sh"),
            str(request),
            str(evidence),
            str(configs),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    body = json.loads(genesis.read_text(encoding="utf-8"))
    retained = json.loads(evidence.read_text(encoding="utf-8"))
    assert body["initialFunds"]["60" + "2" * 56] == 120_000_000_000
    assert retained["address"] == "addr_test1funded"
    assert retained["lovelace"] == 120_000_000_000
    assert retained["shelley_genesis_sha256"] == hashlib.sha256(
        genesis.read_bytes()
    ).hexdigest()

    loader = (scripts / "cardano-loader.sh").read_text(encoding="utf-8")
    assert loader.index('copy_config "$pool_ix"') < loader.index(
        "/data/apply-workload-funding.sh"
    ) < loader.index("/data/apply-governance-guardrails.sh") < loader.index(
        "db-synthesizer"
    )
    assert "BOOTSTRAP_TARGET_EPOCH=3" in loader
    assert "BOOTSTRAP_TARGET_EPOCH_FRACTION_NUMERATOR=3" in loader
    assert "BOOTSTRAP_TARGET_EPOCH_FRACTION_DENOMINATOR=4" in loader
    normalized_loader = " ".join(loader.split())
    assert (
        "epoch_length * BOOTSTRAP_TARGET_EPOCH_FRACTION_NUMERATOR "
        "/ BOOTSTRAP_TARGET_EPOCH_FRACTION_DENOMINATOR"
    ) in normalized_loader
    assert "epoch_length / 2" not in loader
    assert 'db-synthesizer' in loader and '-s "${SYNTHESIS_SLOT}"' in loader
    assert "86400 * 4" not in loader


def test_staged_loader_consolidates_pinned_stake_onto_single_materialized_producer(tmp_path):
    scripts = tmp_path / "scripts"
    configs = tmp_path / "configs"
    generated = tmp_path / "generated"
    pool_id = "14f3abc153c13b3d8681e0e0bbde70594ac27f70d156a3727aac184a"
    other_pool_ids = [str(index) * 56 for index in range(2, 6)]
    genesis = configs / "1" / "configs" / "shelley-genesis.json"
    cold_key = configs / "1" / "keys" / "cold.vkey"
    genesis.parent.mkdir(parents=True)
    cold_key.parent.mkdir(parents=True)
    generated.mkdir()
    cold_key.write_text(
        json.dumps(
            {
                "type": "StakePoolVerificationKey_ed25519",
                "cborHex": "5820e22abfb2eeefddd5f5cff46683b68cf9e6c366ad063d3903db14b8968c13f88a",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    genesis.write_text(
        json.dumps(
            {
                "initialFunds": {"60" + "a" * 56: 120_000_000_000},
                "staking": {
                    "pools": {
                        candidate: {"poolId": candidate, "publicKey": candidate}
                        for candidate in [pool_id, *other_pool_ids]
                    },
                    "stake": {
                        f"credential-{index}": candidate
                        for index, candidate in enumerate(
                            [pool_id, *other_pool_ids], start=1
                        )
                    },
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    bootstrap._stage_loader_scripts(scripts)
    subprocess.run(
        [
            "bash",
            str(scripts / "apply-single-producer-stake.sh"),
            str(generated / "single-producer-stake-applied.json"),
            str(configs),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    body = json.loads(genesis.read_text(encoding="utf-8"))
    evidence = json.loads(
        (generated / "single-producer-stake-applied.json").read_text(
            encoding="utf-8"
        )
    )
    assert body["staking"]["pools"] == {
        pool_id: {"poolId": pool_id, "publicKey": pool_id}
    }
    assert set(body["staking"]["stake"].values()) == {pool_id}
    assert evidence == {
        "config_count": 1,
        "delegation_count": 5,
        "materialized_pool_id": pool_id,
        "prior_pool_count": 5,
        "retained_pool_count": 1,
    }
    loader = (scripts / "cardano-loader.sh").read_text(encoding="utf-8")
    assert loader.index('copy_config "$pool_ix"') < loader.index(
        "/data/apply-single-producer-stake.sh"
    ) < loader.index("/data/apply-workload-funding.sh")


def test_versioned_compose_nodes_are_discoverable_as_dwarf_managed():
    node = {
        "id": "node1",
        "impl": "cardano-node",
        "listen_address": "127.0.0.1:33001",
        "host_slot_index": 1,
        "image": "ghcr.io/intersectmbo/cardano-node:11.1.2@sha256:" + "a" * 64,
    }

    body = compose._docker_compose_body(
        compose_project="dwarf-profile-versioned-local",
        nodes=[node],
        network_name="testnet_42",
    )

    labels = body["services"]["node1"]["labels"]
    assert labels["ada2.managed"] == "dwarf"
    assert labels["ada2.profile"] == "dwarf-profile-versioned-local"
    assert labels["ada2.service"] == "node1"


def test_versioned_compose_nodes_run_as_the_runtime_tree_owner():
    node = {
        "id": "node1",
        "impl": "cardano-node",
        "listen_address": "127.0.0.1:33001",
        "host_slot_index": 1,
        "image": "ghcr.io/intersectmbo/cardano-node:11.1.2@sha256:" + "a" * 64,
    }

    body = compose._docker_compose_body(
        compose_project="dwarf-profile-versioned-local",
        nodes=[node],
        network_name="testnet_42",
        runtime_user="1001:1001",
    )

    assert body["services"]["node1"]["user"] == "1001:1001"


def test_runtime_user_matches_the_bind_mount_owner(tmp_path):
    stat = tmp_path.stat()

    assert compose._runtime_user(tmp_path) == f"{stat.st_uid}:{stat.st_gid}"


def test_bundle_runtime_view_keeps_the_live_short_socket_path(tmp_path):
    runtime_root = tmp_path / "r"
    output_dir = tmp_path / ("long-output-directory-" * 5)
    socket_path = runtime_root / "socket" / "node1.sock"
    socket_path.parent.mkdir(parents=True)
    socket_path.touch()
    metadata_path = runtime_root / "runtime.json"
    metadata = {
        "haskell_nodes": [
            {"id": "node1", "impl": "cardano-node", "socket_path": str(socket_path)}
        ],
        "amaru_nodes": [],
    }

    bundled = compose._emit_bundle_runtime_view(
        output_dir=output_dir,
        metadata_path=metadata_path,
        metadata=metadata,
    )
    body = json.loads(bundled.read_text())

    assert body["nodes"][0]["socket_path"] == str(socket_path)


def test_controller_owned_compose_labels_nodes_and_network_for_exact_inventory():
    node = {
        "id": "node1",
        "impl": "cardano-node",
        "version": "11.1.2",
        "listen_address": "127.0.0.1:33001",
        "host_slot_index": 1,
        "image": "ghcr.io/intersectmbo/cardano-node:11.1.2@sha256:" + "a" * 64,
    }

    body = compose._docker_compose_body(
        compose_project="dwarf-profile-controller-local",
        nodes=[node],
        network_name="testnet_42",
        network_magic=42,
        controller_ownership={
            "installation_id": "install-1",
            "topology_id": "profile-a",
        },
    )

    labels = body["services"]["node1"]["labels"]
    assert labels["io.dwarf.managed"] == "true"
    assert labels["io.dwarf.installation"] == "install-1"
    assert labels["io.dwarf.topology"] == "profile-a"
    assert labels["io.dwarf.component"] == "node"
    assert labels["io.dwarf.node_id"] == "node1"
    assert labels["io.dwarf.implementation"] == "cardano-node"
    assert labels["io.dwarf.version"] == "11.1.2"
    assert labels["io.dwarf.network_magic"] == "42"
    assert body["networks"]["default"]["labels"]["io.dwarf.component"] == "network"


def test_controller_owned_compose_uses_exact_host_visible_runtime_mounts():
    node = {
        "id": "node1",
        "impl": "cardano-node",
        "version": "11.1.2",
        "listen_address": "127.0.0.1:33001",
        "host_slot_index": 1,
        "image": "ghcr.io/intersectmbo/cardano-node:11.1.2@sha256:" + "a" * 64,
    }
    host_root = "/srv/dwarf/controller-state/controller/topology-runtimes/profile-a/current"

    service = compose._docker_compose_body(
        compose_project="dwarf-profile-controller-local",
        nodes=[node],
        network_name="testnet_42",
        controller_ownership={"installation_id": "install-1", "topology_id": "profile-a"},
        docker_host_runtime_root=host_root,
    )["services"]["node1"]

    assert service["volumes"] == [f"{host_root}/env:/env", f"{host_root}/logs:/logs"]
    assert all(not volume.startswith("./") for volume in service["volumes"])


def test_allocated_plan_preserves_controller_ownership_and_host_runtime_root(tmp_path):
    host_root = "/srv/dwarf/controller-state/controller/topology-runtimes/profile-a/current"
    plan = compose.allocate_node_plan(
        {
            "profile_id": "profile-a",
            "compose_mode": "docker",
            "network": "preview",
            "controller_ownership": {
                "installation_id": "install-1",
                "topology_id": "profile-a",
            },
            "docker_host_runtime_root": host_root,
            "nodes": [
                {
                    "id": "node1",
                    "impl": "cardano-node",
                    "version": "11.1.2",
                    "role": "producer",
                    "image": "ghcr.io/intersectmbo/cardano-node:11.1.2@sha256:"
                    + "a" * 64,
                }
            ],
            "topology": {"edges": []},
        },
        runtime_root=tmp_path,
        compose_project="dwarf-profile-controller-local",
    )

    assert plan["controller_ownership"] == {
        "installation_id": "install-1",
        "topology_id": "profile-a",
    }
    assert plan["docker_host_runtime_root"] == host_root
    assert plan["nodes"][0]["health_probe"] == "path-only"


def test_allocated_host_mapped_plan_uses_path_health_without_topology_ownership(tmp_path):
    host_root = "/srv/dwarf/controller-state/controller/scenario-runtimes/run-1"
    plan = compose.allocate_node_plan(
        {
            "profile_id": "run-1",
            "compose_mode": "docker",
            "network": "testnet_42",
            "docker_host_runtime_root": host_root,
            "nodes": [
                {
                    "id": "node1",
                    "impl": "cardano-node",
                    "version": "10.7.1",
                    "role": "honest",
                    "image": "dwarf/cardano-node:10.7.1",
                }
            ],
            "topology": {"edges": []},
        },
        runtime_root=tmp_path,
        compose_project="dwarf-substrate-run-1",
    )

    assert plan["controller_ownership"] is None
    assert plan["docker_host_runtime_root"] == host_root
    assert plan["nodes"][0]["health_probe"] == "path-only"


def test_controller_path_health_does_not_probe_container_local_host_loopback(
    monkeypatch, tmp_path
):
    socket_path = Path("/tmp") / f"dwarf-health-{uuid.uuid4().hex}.sock"
    monkeypatch.setattr(common, "is_port_open", lambda *_: False)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as unix_socket:
            unix_socket.bind(str(socket_path))
            statuses = common.wait_for_nodes_healthy(
                [
                    {
                        "id": "node1",
                        "impl": "cardano-node",
                        "listen_address": "127.0.0.1:39100",
                        "socket_path": str(socket_path),
                        "health_probe": "path-only",
                    }
                ],
                timeout_seconds=0.01,
            )
    finally:
        socket_path.unlink(missing_ok=True)

    assert statuses == [
        {"id": "node1", "healthy": True, "listen_address": "127.0.0.1:39100"}
    ]


def test_controller_path_health_rejects_regular_file(tmp_path):
    socket_path = tmp_path / "stale-node.sock"
    socket_path.touch()

    statuses = common.wait_for_nodes_healthy(
        [
            {
                "id": "node1",
                "impl": "cardano-node",
                "listen_address": "127.0.0.1:39100",
                "socket_path": str(socket_path),
                "health_probe": "path-only",
            }
        ],
        timeout_seconds=0.01,
    )

    assert statuses[0]["healthy"] is False


def test_public_cardano_compose_rejects_socket_without_chain_progress(
    monkeypatch, tmp_path
):
    source = tmp_path / "preview-config"
    source.mkdir()
    for name, body in {
        "config.json": '{"NetworkMagic": 2}\n',
        "topology.json": '{"bootstrapPeers": [], "localRoots": []}\n',
        "byron-genesis.json": "{}\n",
        "shelley-genesis.json": "{}\n",
        "alonzo-genesis.json": "{}\n",
        "conway-genesis.json": "{}\n",
    }.items():
        (source / name).write_text(body, encoding="utf-8")

    runtime_root = tmp_path / "runtime"
    output_dir = tmp_path / "output"
    plan = compose.allocate_node_plan(
        {
            "profile_id": "profile-public-proof",
            "compose_mode": "docker",
            "network": "preview",
            "config_source_dir": str(source),
            "upstream_peer_address": "preview-node.play.dev.cardano.org:3001",
            "nodes": [
                {
                    "id": "node1",
                    "impl": "cardano-node",
                    "version": "11.1.2",
                    "role": "producer",
                    "image": "ghcr.io/intersectmbo/cardano-node:11.1.2@sha256:"
                    + "a" * 64,
                }
            ],
            "topology": {"edges": []},
        },
        runtime_root=runtime_root,
        compose_project="dwarf-profile-public-proof",
    )
    plan["nodes"][0]["resolved_binary"] = "docker-image:cardano-node"
    plan["nodes"][0]["resolved_version"] = "11.1.2"

    monkeypatch.setattr(
        compose,
        "run_command",
        lambda *args, **kwargs: common.CommandResult([], 0, "", ""),
    )
    monkeypatch.setattr(
        compose,
        "_inspect_docker_node",
        lambda **kwargs: {
            "container_name": "dwarf-profile-public-proof-node1-1",
            "container_id": "container-1",
            "container_image_id": "sha256:" + "b" * 64,
            "container_network": "dwarf-profile-public-proof-net",
            "container_ip": "172.20.0.2",
            "container_listen_address": "node1:3001",
            "container_socket_path": "/env/socket/node1/sock",
        },
    )
    monkeypatch.setattr(
        compose,
        "verify_running_container_artifact",
        lambda *args, **kwargs: {
            "satisfied": True,
            "expected_image_id": "sha256:" + "b" * 64,
            "container_image_id": "sha256:" + "b" * 64,
        },
    )
    monkeypatch.setattr(
        compose,
        "verify_running_node_version",
        lambda *args, **kwargs: {
            "satisfied": True,
            "requested_version": "11.1.2",
            "reported_version": "11.1.2",
        },
    )

    def stale_socket_health(nodes, **kwargs):
        socket_path = Path(nodes[0]["socket_path"])
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        socket_path.touch()
        return [{"id": "node1", "healthy": True}]

    chain_gate_calls = []

    def no_chain_progress(**kwargs):
        chain_gate_calls.append(kwargs)
        return {"ready": False, "ready_node_count": 0, "timed_out": True}

    monkeypatch.setattr(compose, "wait_for_nodes_healthy", stale_socket_health)
    monkeypatch.setattr(compose, "_wait_for_cardano_node_block", no_chain_progress)

    with pytest.raises(RuntimeError, match="did not produce a real block"):
        compose._compose_substrate_docker(
            plan=plan,
            output_dir=output_dir,
            runtime_root=runtime_root,
            compose_project="dwarf-profile-public-proof",
            healthy_timeout_seconds=0.01,
            support={},
        )

    assert len(chain_gate_calls) == 1
    assert chain_gate_calls[0]["network_magic"] == 2


def test_socket_backed_compose_resolves_controller_mounts_on_docker_host(tmp_path):
    """Prove a Compose file outside the runtime root mounts the exact host root."""

    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is unavailable")
    image = "dwarf/framework:current"
    if subprocess.run(
        ["docker", "image", "inspect", image], capture_output=True, check=False
    ).returncode:
        pytest.skip(f"local socket integration image is unavailable: {image}")
    project = "dwarf-host-path-" + uuid.uuid4().hex[:12]
    host_root = tmp_path / "host-visible-runtime"
    controller_root = tmp_path / "controller-view"
    (host_root / "env").mkdir(parents=True)
    (host_root / "logs").mkdir(parents=True)
    controller_root.mkdir()
    (host_root / "env" / "marker.txt").write_text("host-path-ok", encoding="utf-8")
    node = {
        "id": "node1",
        "impl": "cardano-node",
        "version": "test",
        "listen_address": "127.0.0.1:39999",
        "host_slot_index": 1,
        "image": "example.invalid/node@sha256:" + "a" * 64,
    }
    body = compose._docker_compose_body(
        compose_project=project,
        nodes=[node],
        network_name="testnet_42",
        controller_ownership={"installation_id": "socket-test", "topology_id": "profile-a"},
        docker_host_runtime_root=str(host_root),
    )
    service = body["services"]["node1"]
    service.update(
        {
            "image": image,
            "entrypoint": ["python3", "-c"],
            "command": [
                "from pathlib import Path; "
                "Path('/logs/proof.txt').write_text(Path('/env/marker.txt').read_text())"
            ],
            "ports": [],
            "user": "0:0",
        }
    )
    compose_path = controller_root / "docker-compose.json"
    compose_path.write_text(json.dumps(body), encoding="utf-8")
    base = [
        "docker", "compose", "--project-name", project, "--file", str(compose_path)
    ]
    try:
        completed = subprocess.run(
            [*base, "up", "--abort-on-container-exit", "--exit-code-from", "node1"],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        assert completed.returncode == 0, completed.stderr or completed.stdout
        assert (host_root / "logs" / "proof.txt").read_text() == "host-path-ok"
        assert not (controller_root / "logs" / "proof.txt").exists()
    finally:
        subprocess.run(
            [*base, "down", "--remove-orphans"],
            capture_output=True,
            check=False,
            timeout=30,
        )


def test_patched_cardano_service_writes_opt_in_measurement_traces_to_retained_logs():
    node = {
        "id": "node1",
        "impl": "cardano-node",
        "listen_address": "127.0.0.1:33001",
        "host_slot_index": 1,
        "target_mode": "patched",
        "image": "dwarf/cardano-measurement@sha256:" + "a" * 64,
    }

    service = compose._docker_compose_body(
        compose_project="dwarf-profile-t-cardano-measurement-patched",
        nodes=[node],
        network_name="testnet_42",
    )["services"]["node1"]

    assert service["environment"] == {
        "DWARF_CARDANO_MEASUREMENT_TRACE": "/logs/node1/cardano-measurement.ndjson",
        "DWARF_CARDANO_PLUTUS_TRACE": "/logs/node1/cardano-plutus.ndjson",
    }
    assert "./logs:/logs" in service["volumes"]


def test_custom_testnet_bootstrap_uses_immutable_support_tool_not_target_binary(monkeypatch, tmp_path):
    selected = "ghcr.io/pragma-org/amaru:v10.11.20260903@sha256:" + "b" * 64
    calls = []

    def fake_synthesize(**kwargs):
        calls.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(compose, "synthesize_amaru_bootstrap", fake_synthesize)

    plan = {
        "nodes": [
            {"id": "bootstrap-cardano", "impl": "cardano-node"},
            {"id": "amaru1", "impl": "amaru", "image": selected},
        ]
    }
    result = compose._synthesize_amaru_bootstrap_for_custom_testnet(
        runtime_root=tmp_path,
        plan=plan,
    )

    assert result == {"ok": True}
    assert calls[0]["loader_image"] == bootstrap.DEFAULT_LOADER_BASE_IMAGE
    assert "amaru_image" not in calls[0]
    assert plan["nodes"][0]["omit_byron_credentials"] is True


def test_packaged_cardano_testnet_create_env_uses_the_1112_cli_contract():
    command = compose._packaged_cardano_testnet_create_env_command(
        output_dir=Path("/runtime/env"),
        pool_count=2,
        network_magic=4243,
    )

    assert command == [
        "cardano-testnet",
        "create-env",
        "--output",
        "/runtime/env",
        "--num-pool-nodes",
        "2",
        "--testnet-magic",
        "4243",
    ]
    assert "--node-logging-format" not in command


def test_generated_cardano_testnet_config_is_compatible_with_10_x_nodes(tmp_path):
    config_path = tmp_path / "configuration.yaml"
    config_path.write_text(
        json.dumps({"Protocol": "Cardano", "TraceOptions": {}}) + "\n",
        encoding="utf-8",
    )

    result = compose._ensure_cardano_testnet_config_compatibility(
        config_path=config_path,
        nodes=[
            {"id": "node1", "impl": "cardano-node", "version": "10.7.1"},
            {"id": "node2", "impl": "cardano-node", "version": "11.1.2"},
        ],
    )

    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["TurnOnLogMetrics"] is True
    assert result == {
        "changed": True,
        "required_by_versions": ["10.7.1"],
        "turn_on_log_metrics": True,
    }


def test_generated_cardano_testnet_genesis_materializes_legacy_stake_for_10_x_nodes(tmp_path):
    genesis_path = tmp_path / "shelley-genesis.json"
    genesis_path.write_text(
        json.dumps(
            {
                "initialFunds": {},
                "staking": {"pools": {}, "stake": {}},
                "extraConfig": {
                    "initialFunds": {"data": {"address-1": 42}},
                    "stakeCredentials": {"data": {"credential-1": "pool-1"}},
                    "stakePools": {
                        "data": {
                            "pool-1": {
                                "poolId": "pool-1",
                                "accountAddress": {"network": "Testnet"},
                                "vrf": "vrf-1",
                            }
                        }
                    },
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    extra_config_before = json.loads(genesis_path.read_text(encoding="utf-8"))["extraConfig"]

    changed = compose._rewrite_cardano_testnet_genesis_for_older_cardano_nodes(
        genesis_path=genesis_path,
        nodes=[{"id": "node1", "impl": "cardano-node", "version": "10.7.1"}],
    )

    genesis = json.loads(genesis_path.read_text(encoding="utf-8"))
    assert changed is True
    assert genesis["extraConfig"] == extra_config_before
    assert genesis["initialFunds"] == {"address-1": 42}
    assert genesis["staking"]["stake"] == {"credential-1": "pool-1"}
    assert genesis["staking"]["pools"]["pool-1"] == {
        "poolId": "pool-1",
        "publicKey": "pool-1",
        "accountAddress": {"network": "Testnet"},
        "rewardAccount": {"network": "Testnet"},
        "vrf": "vrf-1",
    }


def test_generated_private_chain_stages_the_emitted_utxo_identity(tmp_path, monkeypatch):
    env_root = tmp_path / "env"
    for index in range(1, 4):
        key_root = env_root / "utxo-keys" / f"utxo{index}"
        key_root.mkdir(parents=True)
        (key_root / "utxo.vkey").write_text("public verification key\n", encoding="utf-8")
        (key_root / "utxo.skey").write_text("private fixture placeholder\n", encoding="utf-8")
    genesis_path = env_root / "shelley-genesis.json"
    genesis_path.write_text(
        json.dumps(
            {
                "initialFunds": {"60" + "1" * 56: 120_000_000_000},
                "maxLovelaceSupply": 45_000_000_000_000_000,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        if "initial-addr" in command:
            index = int(Path(command[command.index("--verification-key-file") + 1]).parent.name[-1])
            return common.CommandResult(command, 0, f"addr_test1funded{index}\n", "")
        if "address" in command and "info" in command:
            index = int(command[-1][-1])
            return common.CommandResult(
                command,
                0,
                json.dumps({"base16": "60" + str(index) * 56, "type": "payment"}),
                "",
            )
        raise AssertionError(command)

    monkeypatch.setattr(compose, "run_command", fake_run)

    before = genesis_path.read_bytes()
    request = compose._prepare_generated_workload_funding(
        env_root=env_root,
        cardano_cli="/opt/cardano-cli",
        network_magic=42,
    )

    assert genesis_path.read_bytes() == before
    assert request == {
        "id": "utxo1",
        "address": "addr_test1funded1",
        "address_base16": "60" + "1" * 56,
        "verification_key": "utxo-keys/utxo1/utxo.vkey",
        "credentials": [
            {
                "id": f"utxo{index}",
                "address": f"addr_test1funded{index}",
                "address_base16": "60" + str(index) * 56,
                "verification_key": f"utxo-keys/utxo{index}/utxo.vkey",
            }
            for index in range(1, 4)
        ],
    }
    assert json.loads(
        (env_root / "generated-workload-funding.json").read_text(encoding="utf-8")
    ) == request
    assert "utxo.skey" not in json.dumps(request)
    assert any("initial-addr" in command for command in commands)


def test_synthetic_chain_cardano_service_omits_unrelated_byron_credentials():
    node = {
        "id": "bootstrap-cardano",
        "impl": "cardano-node",
        "listen_address": "127.0.0.1:37511",
        "host_slot_index": 1,
        "omit_byron_credentials": True,
        "image": "ghcr.io/intersectmbo/cardano-node:10.7.1@sha256:" + "a" * 64,
    }

    service = compose._docker_compose_body(
        compose_project="dwarf-profile-versioned-amaru",
        nodes=[node],
        network_name="testnet_42",
    )["services"]["bootstrap-cardano"]
    command = service["command"][0]

    assert "--shelley-kes-key" in command
    assert "--shelley-vrf-key" in command
    assert "--shelley-operational-certificate" in command
    assert "--byron-delegation-certificate" not in command
    assert "--byron-signing-key" not in command


def test_amaru_runtime_progress_requires_intersection_and_convergence():
    text = """
INFO build_ledger tip.hash=aaa tip.slot=259181
INFO intersect found peer=bootstrap-cardano:3001 current=259181.aaa highest=345560.bbb
INFO adopted tip tip.slot=259288 tip.hash=ccc tip.block_height=2636 max_block_height=2636
INFO adopted tip tip.slot=345560 tip.hash=bbb tip.block_height=3500 max_block_height=3500
"""

    parsed = compose._parse_amaru_runtime_progress(text)

    assert parsed["bootstrap_slot"] == 259181
    assert parsed["highest_peer_slot"] == 345560
    assert parsed["latest_adopted_slot"] == 345560
    assert parsed["advanced"] is True
    assert parsed["converged"] is True
    assert parsed["ready"] is True


def test_amaru_runtime_progress_parses_current_format_against_initial_peer_tip():
    text = """
2026-10-15T20:28:14Z INFO amaru::consensus: chainsync.intersect_found peer="node2:3001" current="[259129, h'aaa', 2579]" highest="[345447, h'bbb', 3463]"
2026-10-15T20:28:15Z INFO amaru::consensus: tip.adopt slot=259216 header_hash="ccc" block_height=2580 max_block_height=2580 suppressed=0
2026-10-15T20:33:31Z INFO amaru::consensus: chainsync.intersect_found peer="node2:3001" current="[345447, h'bbb', 3463]" highest="[345977, h'ddd', 3464]"
2026-10-15T20:33:31Z DEBUG amaru::consensus: tip.adopt slot=345447 header_hash="bbb" block_height=3463 max_block_height=3463 suppressed=883
"""

    parsed = compose._parse_amaru_runtime_progress(text)

    assert parsed["bootstrap_slot"] == 259129
    assert parsed["highest_peer_slot"] == 345447
    assert parsed["convergence_target"] == "initial-advertised-peer-tip"
    assert parsed["latest_adopted_slot"] == 345447
    assert parsed["intersection_found"] is True
    assert parsed["advanced"] is True
    assert parsed["converged"] is True
    assert parsed["ready"] is True


def test_amaru_runtime_progress_does_not_treat_intersection_as_adoption():
    text = """
INFO amaru::consensus: chainsync.intersect_found peer="node2:3001" current="[259129, h'aaa', 2579]" highest="[345447, h'bbb', 3463]"
INFO amaru::consensus: tip.adopt slot=259216 header_hash="ccc" block_height=2580 max_block_height=2580 suppressed=0
INFO amaru::consensus: chainsync.intersect_found peer="node2:3001" current="[345447, h'bbb', 3463]" highest="[345977, h'ddd', 3464]"
"""

    parsed = compose._parse_amaru_runtime_progress(text)

    assert parsed["latest_adopted_slot"] == 259216
    assert parsed["converged"] is False
    assert parsed["ready"] is False


def test_amaru_runtime_progress_blocks_network_handshake_decode_failure():
    text = """
INFO amaru::consensus: chainsync.intersect_found peer="node2:3001" current="[259129, h'aaa', 2579]" highest="[345447, h'bbb', 3463]"
INFO amaru::consensus: chainsync.intersect_found peer="node2:3001" current="[345447, h'bbb', 3463]" highest="[345977, h'ddd', 3464]"
ERROR amaru_pure_stage::types: failed to decode message from network err=decode error: expected array length 4 for label 0, got: Some(5)
"""

    parsed = compose._parse_amaru_runtime_progress(text)

    assert parsed["blocking_signals"] == ["handshake-decode-error"]
    assert parsed["ready"] is False


def test_amaru_runtime_progress_recovers_after_handshake_decode_and_chain_progress():
    text = """
INFO amaru::consensus: chainsync.intersect_found peer="node2:3001" current="[259129, h'aaa', 2579]" highest="[345447, h'bbb', 3463]"
ERROR amaru_pure_stage::types: failed to decode message from network err=decode error: expected array length 4 for label 0, got: Some(5)
INFO amaru::protocols: manager.peer.handshake_completed peer="node2:3001" conn_id=1 full_duplex=true
INFO amaru::consensus: tip.adopt slot=345447 header_hash="ccc" block_height=3463 max_block_height=3463 suppressed=0
"""

    parsed = compose._parse_amaru_runtime_progress(text)

    assert parsed["observed_signals"] == ["handshake-decode-error"]
    assert parsed["blocking_signals"] == []
    assert parsed["advanced"] is True
    assert parsed["converged"] is True
    assert parsed["ready"] is True


def test_amaru_runtime_progress_rejects_one_block_then_vrf_failure():
    text = """
INFO build_ledger tip.hash=aaa tip.slot=259181
INFO intersect found peer=bootstrap-cardano:3001 current=259181.aaa highest=345560.bbb
INFO adopted tip tip.slot=259288 tip.hash=ccc tip.block_height=2636 max_block_height=2636
ERROR Failed to validate header at 345740.ddd: Invalid VRF proof: VerificationFailed
"""

    parsed = compose._parse_amaru_runtime_progress(text)

    assert parsed["advanced"] is True
    assert parsed["converged"] is False
    assert parsed["ready"] is False
    assert parsed["blocking_signals"] == ["invalid-vrf-proof"]


def test_versioned_amaru_service_migrates_bootstrap_state_before_run():
    node = {
        "id": "amaru1",
        "impl": "amaru",
        "listen_address": "127.0.0.1:35001",
        "host_slot_index": 2,
        "chain_dir": "/tmp/chain.testnet_42.db",
        "ledger_dir": "/tmp/ledger.testnet_42.db",
        "container_peer_addresses": ["bootstrap-cardano:3001"],
        "image": "ghcr.io/pragma-org/amaru:v10.11.20260730@sha256:" + "c" * 64,
    }

    body = compose._docker_compose_body(
        compose_project="dwarf-profile-versioned-amaru",
        nodes=[node],
        network_name="testnet_42",
    )

    service = body["services"]["amaru1"]
    assert service["environment"]["AMARU_MIGRATE_CHAIN_DB"] == "true"
    assert service["environment"]["AMARU_LOG"] == (
        "error,amaru=info,amaru_pure_stage=warn,amaru::consensus=debug"
    )
    assert "AMARU_TRACE" not in service["environment"]
    assert "exec amaru run" in service["command"][0]
    assert '--era-history "/amaru/amaru1/era-history.json"' in service["command"][0]
    assert service["entrypoint"] == ["/bin/sh", "-ec"]


def test_public_amaru_service_uses_declared_external_peer_and_bootstraps_real_network():
    node = {
        "id": "amaru1",
        "impl": "amaru",
        "listen_address": "127.0.0.1:39000",
        "host_slot_index": 1,
        "chain_dir": "/tmp/chain.preview.db",
        "ledger_dir": "/tmp/ledger.preview.db",
        "fallback_peer_addresses": ["preview-node.play.dev.cardano.org:3001"],
        "image": "ghcr.io/pragma-org/amaru:v10.11.20260912@sha256:" + "c" * 64,
    }

    service = compose._docker_compose_body(
        compose_project="dwarf-profile-public-amaru",
        nodes=[node],
        network_name="preview",
    )["services"]["amaru1"]
    command = service["command"][0]

    assert "mkdir -p /amaru/amaru1 /logs/amaru1; cd /amaru/amaru1" in command
    assert "amaru bootstrap" in command
    assert '--network "preview"' in command
    assert '--peer-address "preview-node.play.dev.cardano.org:3001"' in command
    assert service["image"].endswith("@sha256:" + "c" * 64)


def test_public_assets_can_be_staged_from_preserved_profile_configuration(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    required = {
        "config.json": '{"NetworkMagic": 2}\n',
        "topology.json": '{"bootstrapPeers": []}\n',
        "byron-genesis.json": "{}\n",
        "shelley-genesis.json": "{}\n",
        "alonzo-genesis.json": "{}\n",
        "conway-genesis.json": "{}\n",
    }
    for name, body in required.items():
        (source / name).write_text(body, encoding="utf-8")

    assets = compose._stage_public_network_assets(
        network_name="preview",
        destination=tmp_path / "staged",
        config_source_dir=str(source),
    )

    assert set(required).issubset(assets)
    assert Path(assets["config.json"]).read_text(encoding="utf-8") == required["config.json"]
    assert all(Path(path).parent == tmp_path / "staged" for path in assets.values())


def test_public_upstream_is_retained_as_genesis_mode_local_root(tmp_path):
    template, access_points = compose._public_topology_with_upstream(
        {
            "bootstrapPeers": [],
            "localRoots": [],
            "useLedgerAfterSlot": 119231973,
        },
        "preview-node.play.dev.cardano.org:3001",
    )
    node = {
        "id": "node1",
        "peer_addresses": [],
    }
    topology_path = tmp_path / "topology.json"

    compose._rewrite_haskell_topology(
        node,
        topo_path=topology_path,
        template=template,
        access_points=access_points,
    )

    body = json.loads(topology_path.read_text(encoding="utf-8"))
    expected = {"address": "preview-node.play.dev.cardano.org", "port": 3001}
    assert body["bootstrapPeers"] == [expected]
    assert body["localRoots"] == [
        {
            "accessPoints": [expected],
            "advertise": False,
            "behindFirewall": False,
            "diffusionMode": "InitiatorAndResponder",
            "hotValency": 1,
            "trustable": True,
            "warmValency": 1,
        }
    ]
    assert body["useLedgerAfterSlot"] == 119231973


def test_bootstrap_state_copies_generated_era_history_to_each_amaru_target(tmp_path):
    generated = tmp_path / "generated" / "testnet_42" / "snapshots"
    generated.mkdir(parents=True)
    legacy_history = {
        "stability_window": 1200,
        "eras": [
            {
                "start": {"time_ms": index * 1000, "slot": index, "epoch": index},
                "end": None,
                "params": {"epoch_size_slots": 400, "slot_length": 500},
            }
            for index in range(7)
        ],
    }
    (generated / "history.100.abc.json").write_text(
        json.dumps(legacy_history) + "\n", encoding="utf-8"
    )
    staged = tmp_path / "staged" / "1"
    staged.mkdir(parents=True)
    (staged / "ledger.db").mkdir()
    target = tmp_path / "target" / "amaru1"

    bootstrap._apply_staged_state(
        {
            "network_name": "testnet_42",
            "generated_root": str(tmp_path / "generated"),
            "amaru_state_roots": {"1": str(staged)},
            "target_amaru_state_roots": {"1": str(target)},
        }
    )

    upgraded = json.loads((target / "era-history.json").read_text(encoding="utf-8"))
    assert [era["params"]["era_name"] for era in upgraded["eras"]] == [
        "Byron",
        "Shelley",
        "Allegra",
        "Mary",
        "Alonzo",
        "Babbage",
        "Conway",
    ]
    assert upgraded["eras"][2]["start"]["time"] == 2
    assert "time_ms" not in upgraded["eras"][2]["start"]


def test_bootstrap_producer_command_uses_synthesized_cardano_db_and_exact_image(tmp_path):
    layout = {
        "network_name": "testnet_42",
        "workspace_root": str(tmp_path / "workspace"),
        "config_roots": {"1": str(tmp_path / "configs" / "1")},
        "cardano_state_roots": {"1": str(tmp_path / "state" / "1")},
    }

    command = bootstrap.bootstrap_producer_command(layout=layout)

    assert bootstrap.DEFAULT_BOOTSTRAP_PRODUCER_IMAGE in command
    assert command[command.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"
    assert f"{layout['cardano_state_roots']['1']}:/cardano/state" in command
    assert f"{layout['cardano_state_roots']['1']}:/cardano/state:ro" not in command
    assert f"{layout['config_roots']['1']}:/cardano/config:ro" in command
    assert "/cardano/state" in command
    assert "/cardano/config/configs" in command
    assert "/bundle" in command
    assert "testnet_42" in command


def test_bootstrap_write_commands_use_the_executing_host_identity(monkeypatch, tmp_path):
    monkeypatch.setattr(bootstrap.os, "getuid", lambda: 1001)
    monkeypatch.setattr(bootstrap.os, "getgid", lambda: 121)
    layout = {
        "network_name": "testnet_42",
        "workspace_root": str(tmp_path / "workspace"),
        "generated_root": str(tmp_path / "workspace" / "generated"),
        "workload_funding_request": str(tmp_path / "workspace" / "workload-funding.json"),
        "governance_guardrails_request": None,
        "config_roots": {"1": str(tmp_path / "configs" / "1")},
        "cardano_state_roots": {"1": str(tmp_path / "state" / "1")},
        "amaru_state_roots": {"1": str(tmp_path / "amaru-state" / "1")},
    }

    cardano_command, amaru_command = bootstrap.loader_commands(layout=layout)
    producer_command = bootstrap.bootstrap_producer_command(layout=layout)

    for command in (cardano_command, amaru_command, producer_command):
        assert command[command.index("--user") + 1] == "1001:121"


def test_bootstrap_commands_translate_controller_paths_for_host_docker(tmp_path):
    runtime_root = tmp_path / "controller-runtime"
    workspace_root = runtime_root / "amaru-bootstrap-loader"
    host_root = "/srv/dwarf/controller-state/controller/topology/current"
    layout = {
        "runtime_root": str(runtime_root),
        "docker_host_runtime_root": host_root,
        "network_name": "testnet_42",
        "workspace_root": str(workspace_root),
        "generated_root": str(workspace_root / "generated"),
        "workload_funding_request": str(workspace_root / "workload-funding.json"),
        "config_roots": {"1": str(workspace_root / "configs" / "1")},
        "cardano_state_roots": {"1": str(workspace_root / "cardano-state" / "1")},
        "amaru_state_roots": {"1": str(workspace_root / "amaru-state" / "1")},
    }

    cardano_command, amaru_command = bootstrap.loader_commands(layout=layout)
    producer_command = bootstrap.bootstrap_producer_command(layout=layout)

    for command in (cardano_command, amaru_command, producer_command):
        bind_sources = [
            value.split(":", 1)[0]
            for index, value in enumerate(command)
            if index > 0 and command[index - 1] == "-v"
        ]
        assert bind_sources
        assert all(source.startswith(host_root + "/") for source in bind_sources)
        assert all(not source.startswith(str(runtime_root)) for source in bind_sources)


def test_producer_bundle_maps_named_databases_into_each_runtime_target(tmp_path):
    bundle = tmp_path / "workspace" / "producer-bundle" / "testnet_42"
    (bundle / "ledger.testnet_42.db").mkdir(parents=True)
    (bundle / "chain.testnet_42.db").mkdir()
    (bundle / "ledger.testnet_42.db" / "ledger").write_text("ok", encoding="utf-8")
    (bundle / "chain.testnet_42.db" / "chain").write_text("ok", encoding="utf-8")
    (bundle / "era-history.json").write_text('{"eras": []}\n', encoding="utf-8")
    target = tmp_path / "target" / "amaru1"
    staged_cardano = tmp_path / "staged-cardano" / "1"
    staged_cardano.mkdir(parents=True)
    (staged_cardano / "immutable").mkdir()
    (staged_cardano / "immutable" / "00000.chunk").write_text("chain", encoding="utf-8")
    staged_config = tmp_path / "staged-config" / "1"
    (staged_config / "configs").mkdir(parents=True)
    (staged_config / "configs" / "config.json").write_text(
        '{"Protocol": "Cardano", "RequiresNetworkMagic": "RequiresNoMagic"}\n',
        encoding="utf-8",
    )
    (staged_config / "configs" / "shelley-genesis.json").write_text(
        '{"systemStart": "2026-09-14T00:00:00Z"}\n', encoding="utf-8"
    )
    (staged_config / "configs" / "dijkstra-genesis.json").write_text(
        '{"constitution": {}}\n', encoding="utf-8"
    )
    (staged_config / "configs" / "topology.json").write_text(
        '{"localRoots": [{"accessPoints": [{"address": "p1.example"}]}]}\n',
        encoding="utf-8",
    )
    (staged_config / "keys").mkdir()
    (staged_config / "keys" / "byron-delegation.cert").write_text(
        "producer-credential\n", encoding="utf-8"
    )
    target_cardano = tmp_path / "target-cardano" / "node1" / "db"
    target_config = tmp_path / "runtime-env"
    target_config.mkdir()
    (target_config / "configuration.yaml").write_text('{"Protocol": "Cardano"}\n', encoding="utf-8")
    (target_config / "shelley-genesis.json").write_text(
        '{"systemStart": "2026-09-18T00:00:00Z"}\n', encoding="utf-8"
    )
    (target_config / "topology.json").write_text('{"localRoots": []}\n', encoding="utf-8")
    target_keys = target_config / "pools-keys" / "pool1"
    target_keys.mkdir(parents=True)
    (target_keys / "byron-delegation.cert").write_text("fresh-credential\n", encoding="utf-8")
    layout = {
        "network_name": "testnet_42",
        "workspace_root": str(tmp_path / "workspace"),
        "config_roots": {"1": str(staged_config)},
        "target_config_root": str(target_config),
        "target_cardano_key_roots": {"1": str(target_keys)},
        "cardano_state_roots": {"1": str(staged_cardano)},
        "target_cardano_state_roots": {"1": str(target_cardano)},
        "target_amaru_state_roots": {"1": str(target)},
    }

    bootstrap.apply_producer_bundle(layout)

    assert (target / "ledger.db" / "ledger").read_text(encoding="utf-8") == "ok"
    assert (target / "chain.db" / "chain").read_text(encoding="utf-8") == "ok"
    assert (target / "era-history.json").is_file()
    assert (target_cardano / "immutable" / "00000.chunk").read_text(encoding="utf-8") == "chain"
    runtime_configuration = json.loads(
        (target_config / "configuration.yaml").read_text(encoding="utf-8")
    )
    assert runtime_configuration["RequiresNetworkMagic"] == "RequiresNoMagic"
    assert runtime_configuration["DijkstraGenesisFile"] == "dijkstra-genesis.json"
    assert json.loads((target_config / "shelley-genesis.json").read_text(encoding="utf-8"))["systemStart"] == "2026-09-14T00:00:00Z"
    assert json.loads((target_config / "topology.json").read_text(encoding="utf-8"))["localRoots"] == []
    assert (target_keys / "byron-delegation.cert").read_text(encoding="utf-8") == "producer-credential\n"
