import json
from dataclasses import replace

import pytest

from profile_manager.cli import deployment_timeout_seconds
from profile_manager.deployment_versions import build_deployment_version_preview
from profile_manager.profiles import (
    Profile,
    deployment_adapter_for_profile,
    deploy_command,
    remove_command,
    versioned_substrate_for_profile,
)
from scripts.runtime_substrate_common import build_version_provenance, normalize_substrate


def _profile(**changes):
    base = Profile(
        id="versioned-local",
        label="Versioned local devnet",
        node_type="cardano-node",
        node_count=2,
        amaru_node_count=0,
        network_magic=42,
        peer_sharing=False,
        remote_runtime_root="/opt/dwarf/cardano-profiles/versioned-local",
        compose_project="dwarf-profile-versioned-local",
        topology_pattern="local-mesh",
        shared_genesis=True,
        version_policy="latest-confirmed",
    )
    return replace(base, **changes)


def _preview(profile):
    return build_deployment_version_preview(profile.__dict__)


def test_confirmed_cardano_profile_freezes_every_node_to_exact_oci_artifact():
    profile = _profile()

    substrate = versioned_substrate_for_profile(profile, _preview(profile))

    assert substrate["compose_mode"] == "docker"
    assert substrate["version_policy"] == "latest-confirmed"
    assert substrate["version_status"] == "confirmed"
    assert len(substrate["nodes"]) == 2
    assert {node["impl"] for node in substrate["nodes"]} == {"cardano-node"}
    assert {node["version"] for node in substrate["nodes"]} == {"11.1.2"}
    assert all("@sha256:" in node["image"] for node in substrate["nodes"])
    assert all(len(node["source_revision"]) == 40 for node in substrate["nodes"])
    assert len(substrate["topology"]["edges"]) == 2


def test_amaru_target_profile_declares_its_required_cardano_bootstrap_source():
    profile = _profile(
        node_type="amaru",
        node_count=0,
        amaru_node_count=2,
        version_policy="exact",
        amaru_version="10.11.20260903",
    )
    preview = _preview(profile)

    substrate = versioned_substrate_for_profile(profile, preview)

    assert substrate["scope"] == "amaru-only"
    assert substrate["target_node_count"] == 2
    assert substrate["support_node_count"] == 1
    assert substrate["nodes"][0]["id"] == "bootstrap-cardano"
    assert substrate["nodes"][0]["impl"] == "cardano-node"
    assert substrate["nodes"][0]["supporting"] is True
    assert {node["version"] for node in substrate["nodes"][1:]} == {"10.11.20260903"}
    assert all(node["impl"] == "amaru" for node in substrate["nodes"][1:])

    normalized = normalize_substrate(substrate)
    provenance = build_version_provenance(normalized, normalized["nodes"])
    assert provenance["scope"] == "amaru-only"
    assert provenance["target_node_count"] == 2
    assert provenance["support_node_count"] == 1
    assert provenance["nodes"][0]["supporting"] is True


def test_versioned_cardano_deploy_uses_exact_images_and_runtime_compose_adapter():
    profile = _profile()
    preview = _preview(profile)

    command = deploy_command(profile, version_preview=preview)

    exact_image = preview["resolved"]["cardano-node"]["artifacts"][0]
    assert exact_image["reference"] + "@" + exact_image["digest"] in command
    assert "runtime_compose_substrate.py" in command
    assert "docker pull" in command
    assert "/home/dwarf/.local/bin/cardano-node run" not in command
    assert "ADA2_DWARF_ROOT" in command
    assert json.dumps(preview["catalog_revision"]) in command


def test_versioned_deploy_can_pin_the_remote_installed_dwarf_root():
    profile = _profile()

    command = deploy_command(
        profile,
        version_preview=_preview(profile),
        remote_dwarf_root="/srv/dwarf/dwarf",
    )

    assert "dwarf_root=/srv/dwarf/dwarf" in command
    assert 'dwarf_root="${ADA2_DWARF_ROOT:-}"' not in command


def test_versioned_amaru_deploy_uses_live_producer_control_adapter():
    profile = _profile(
        node_type="amaru",
        node_count=0,
        amaru_node_count=1,
        version_policy="exact",
        amaru_version="10.11.20260730",
    )
    preview = _preview(profile)

    command = deploy_command(profile, version_preview=preview)

    assert "runtime_amaru_control_substrate.py" in command
    assert "runtime_compose_substrate.py" not in command
    assert '"lifecycle": "cardano_amaru_relay_bootstrap_control"' in command
    assert '"scope": "amaru-only"' in command
    assert '"profile_id": "versioned-local"' in command
    assert '"supporting_cardano_version": "10.7.1"' in command
    assert "docker pull" in command


def test_plutus_v2_profile_propagates_pinned_model_to_additive_runtime():
    profile = _profile(
        id="profile-w-amaru-measurement-plutus-v2",
        node_type="amaru",
        node_count=0,
        amaru_node_count=1,
        version_policy="exact",
        amaru_version="10.11.20260912",
        plutus_v2_genesis=True,
        plutus_v2_cost_model_path="corpora/cardano-measurement/plutus-v2-cost-model-protocol-v10.json",
        plutus_v2_cost_model_sha256="675a27a3c1f2f9b32954c67c1f0ad21479713eef5513386638d78e05f5e277cc",
    )

    substrate = versioned_substrate_for_profile(profile, _preview(profile))
    command = deploy_command(
        profile,
        version_preview=_preview(profile),
        remote_dwarf_root="/home/nigel/dwarf-pragma/dwarf",
    )

    assert substrate["plutus_v2_genesis"] is True
    assert substrate["plutus_v2_cost_model_sha256"] == profile.plutus_v2_cost_model_sha256
    assert '"plutus_v2_genesis": true' in command
    assert "/home/nigel/dwarf-pragma/dwarf/corpora/cardano-measurement/plutus-v2-cost-model-protocol-v10.json" in command


def test_kes_genesis_override_propagates_only_when_set():
    base = dict(
        id="profile-opcert-aged-kes",
        node_type="mixed",
        node_count=2,
        amaru_node_count=1,
        version_policy="exact",
        cardano_version="11.1.2",
        amaru_version="10.11.20260918",
    )
    aged = _profile(
        **base,
        kes_genesis_override={"slots_per_kes_period": 100, "max_kes_evolutions": 12},
    )
    default = _profile(**base)

    assert versioned_substrate_for_profile(aged, _preview(aged))[
        "kes_genesis_override"
    ] == {"slots_per_kes_period": 100, "max_kes_evolutions": 12}
    assert (
        versioned_substrate_for_profile(default, _preview(default))[
            "kes_genesis_override"
        ]
        is None
    )

    aged_command = deploy_command(aged, version_preview=_preview(aged))
    default_command = deploy_command(default, version_preview=_preview(default))
    assert '"slots_per_kes_period": 100' in aged_command
    assert '"max_kes_evolutions": 12' in aged_command
    assert '"kes_genesis_override": null' in default_command
    assert '"slots_per_kes_period"' not in default_command


def test_experimental_protocols_override_propagates_only_when_set():
    base = dict(
        id="profile-v16-repro",
        node_type="amaru",
        node_count=0,
        amaru_node_count=1,
        version_policy="exact",
        amaru_version="10.11.20260912",
    )
    pinned = _profile(**base, cardano_experimental_protocols=True)
    default = _profile(**base)

    pinned_command = deploy_command(pinned, version_preview=_preview(pinned))
    default_command = deploy_command(default, version_preview=_preview(default))

    assert versioned_substrate_for_profile(pinned, _preview(pinned))[
        "cardano_experimental_protocols"
    ] is True
    assert '"cardano_experimental_protocols": true' in pinned_command
    assert '"cardano_experimental_protocols": true' not in default_command
    assert '"cardano_experimental_protocols": null' in default_command


def test_versioned_mixed_deploy_uses_live_producer_control_adapter():
    profile = _profile(
        node_type="mixed",
        node_count=2,
        amaru_node_count=1,
        version_policy="latest-confirmed",
    )
    preview = _preview(profile)

    command = deploy_command(profile, version_preview=preview)

    assert "runtime_amaru_control_substrate.py" in command
    assert "runtime_compose_substrate.py" not in command
    assert '"scope": "mixed"' in command
    assert '"lifecycle": "cardano_amaru_relay_bootstrap_control"' in command


def test_deploy_command_retains_selected_catalog_evidence_without_exceeding_exec_limit():
    profile = _profile(
        node_type="amaru",
        node_count=0,
        amaru_node_count=1,
        version_policy="exact",
        amaru_version="10.11.20260912",
    )
    preview = _preview(profile)

    substrate = versioned_substrate_for_profile(profile, preview)
    command = deploy_command(profile, version_preview=preview)

    snapshot = substrate["catalog_snapshot"]
    assert snapshot["catalog_revision"] == preview["catalog_revision"]
    assert {item["implementation"] for item in snapshot["selected_releases"]} == {
        "amaru",
        "cardano-node",
    }
    assert len(command.encode("utf-8")) < 128 * 1024


def test_version_policy_does_not_select_or_replace_deployment_adapter():
    profiles = {
        "generated-cardano": _profile(topology_pattern="local-mesh", shared_genesis=True),
        "local-cardano": _profile(topology_pattern=None, shared_genesis=False),
        "public-cardano": _profile(
            node_count=1,
            topology_pattern=None,
            shared_genesis=False,
            config_source_dir="/opt/dwarf/cardano-configs/preview",
            upstream_peer_address="preview-node.play.dev.cardano.org:3001",
            public_network="preview",
        ),
        "public-amaru": _profile(
            node_type="amaru",
            node_count=0,
            amaru_node_count=1,
            topology_pattern=None,
            shared_genesis=False,
            amaru_network="preview",
            upstream_peer_address="preview-node.play.dev.cardano.org:3001",
            public_network="preview",
        ),
        "closed-amaru": _profile(
            node_type="amaru",
            node_count=0,
            amaru_node_count=1,
            topology_pattern=None,
            shared_genesis=False,
            testbed="antithesis-closed",
        ),
        "mixed": _profile(node_type="mixed", node_count=2, amaru_node_count=1),
    }
    expected = {
        "generated-cardano": "generated-cardano-local",
        "local-cardano": "cardano-compose-local",
        "public-cardano": "cardano-public-peer",
        "public-amaru": "amaru-public-peer",
        "closed-amaru": "amaru-control",
        "mixed": "amaru-control",
    }

    for name, profile in profiles.items():
        assert deployment_adapter_for_profile(profile) == expected[name]
        assert deployment_adapter_for_profile(
            replace(profile, version_policy="exact")
        ) == expected[name]
        assert deployment_adapter_for_profile(
            replace(profile, version_policy="latest-stable")
        ) == expected[name]


def test_public_cardano_keeps_public_peer_configuration_with_exact_artifact():
    profile = _profile(
        node_count=1,
        topology_pattern=None,
        shared_genesis=False,
        config_source_dir="/opt/dwarf/cardano-configs/preview",
        upstream_peer_address="preview-node.play.dev.cardano.org:3001",
        public_network="preview",
        listen_address="127.0.0.1:39100",
    )
    preview = _preview(profile)

    command = deploy_command(profile, version_preview=preview)

    assert "runtime_compose_substrate.py" in command
    assert '"deployment_adapter": "cardano-public-peer"' in command
    assert '"network": "preview"' in command
    assert '"config_source_dir": "/opt/dwarf/cardano-configs/preview"' in command
    assert '"upstream_peer_address": "preview-node.play.dev.cardano.org:3001"' in command
    assert '"listen_address": "127.0.0.1:39100"' in command
    assert "/home/dwarf/.local/bin/cardano-node" not in command


def test_public_amaru_keeps_external_peer_without_inventing_local_producer():
    profile = _profile(
        node_type="amaru",
        node_count=0,
        amaru_node_count=1,
        topology_pattern=None,
        shared_genesis=False,
        amaru_network="preview",
        upstream_peer_address="preview-node.play.dev.cardano.org:3001",
        public_network="preview",
        listen_address="127.0.0.1:39000",
    )
    preview = _preview(profile)

    substrate = versioned_substrate_for_profile(profile, preview)
    command = deploy_command(profile, version_preview=preview)

    assert [node["impl"] for node in substrate["nodes"]] == ["amaru"]
    assert substrate["support_node_count"] == 0
    assert "runtime_compose_substrate.py" in command
    assert "runtime_amaru_control_substrate.py" not in command
    assert '"deployment_adapter": "amaru-public-peer"' in command
    assert '"upstream_peer_address": "preview-node.play.dev.cardano.org:3001"' in command
    assert "/home/dwarf/amaru-verification/target/debug/amaru" not in command


def test_omitted_policy_profile_never_emits_ambient_or_mutable_node_execution():
    profile = replace(
        _profile(topology_pattern="local-mesh", shared_genesis=True),
        version_policy="latest-confirmed",
        version_policy_source="implicit-default",
    )

    command = deploy_command(profile)

    assert "/home/dwarf/.local/bin/cardano-node" not in command
    assert "/home/dwarf/amaru-verification/target/debug/amaru" not in command
    assert "cardano-node:latest" not in command
    assert "@sha256:" in command


def test_removed_legacy_policy_cannot_reach_ambient_execution_path():
    profile = _profile(version_policy="legacy")

    with pytest.raises(Exception, match="version_policy"):
        deploy_command(profile)


def test_remove_uses_retained_compose_file_and_archives_all_profile_roots():
    command = remove_command("/opt/dwarf/cardano-profiles")

    assert 'docker compose -f "$config_path" --project-name "$project" down' in command
    assert "find \"$base_path\" -mindepth 1 -maxdepth 1 -type d" in command
    assert "! -name archive" in command
    assert "/profile-*" not in command


def test_amaru_backed_profiles_allow_the_live_chain_bootstrap_to_finish():
    cardano = _profile(node_type="cardano-node", node_count=3, amaru_node_count=0)
    amaru = _profile(node_type="amaru", node_count=0, amaru_node_count=2)
    mixed = _profile(node_type="mixed", node_count=3, amaru_node_count=2)

    assert deployment_timeout_seconds(cardano) == 600
    assert deployment_timeout_seconds(amaru) == 1800
    assert deployment_timeout_seconds(mixed) == 1800
