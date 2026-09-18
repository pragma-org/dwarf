import json
from dataclasses import replace

from profile_manager.deployment_versions import build_deployment_version_preview
from profile_manager.profiles import (
    Profile,
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


def test_versioned_deploy_uses_exact_images_and_runtime_compose_adapter():
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


def test_legacy_profile_preserves_existing_host_process_adapter():
    profile = _profile(version_policy="legacy")

    command = deploy_command(profile)

    assert "/home/dwarf/.local/bin/cardano-node" in command
    assert "runtime_compose_substrate.py" not in command


def test_remove_uses_retained_compose_file_and_archives_all_profile_roots():
    command = remove_command("/opt/dwarf/cardano-profiles")

    assert 'docker compose -f "$config_path" --project-name "$project" down' in command
    assert "find \"$base_path\" -mindepth 1 -maxdepth 1 -type d" in command
    assert "! -name archive" in command
    assert "/profile-*" not in command
