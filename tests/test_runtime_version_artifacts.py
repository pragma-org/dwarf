import json
from pathlib import Path

import pytest

from scripts.runtime_install_version import resolve_requested_versions
from scripts.runtime_substrate_common import (
    allocate_node_plan,
    build_version_provenance,
    CommandResult,
    normalize_substrate,
    resolve_docker_image_for_node,
    verify_running_container_artifact,
    verify_running_node_version,
)


DIGEST = "sha256:" + "a" * 64
IMAGE_ID = "sha256:" + "b" * 64


def _substrate(image=f"ghcr.io/example/cardano-node:11.1.2@{DIGEST}"):
    return {
        "compose_mode": "docker",
        "network_magic": 42,
        "version_policy": "latest-stable",
        "version_policy_source": "explicit",
        "version_status": "unknown",
        "unknown_acknowledged": True,
        "catalog_revision": "c" * 64,
        "nodes": [
            {
                "id": "node1",
                "impl": "cardano-node",
                "version": "11.1.2",
                "role": "producer",
                "image": image,
                "source_revision": "d" * 40,
            }
        ],
        "topology": {"edges": []},
    }


def _result(command, returncode=0, stdout="", stderr=""):
    return CommandResult(command, returncode, stdout, stderr)


def test_normalize_substrate_preserves_exact_version_provenance():
    normalized = normalize_substrate(_substrate())

    assert normalized["version_policy"] == "latest-stable"
    assert normalized["version_policy_source"] == "explicit"
    assert normalized["version_status"] == "unknown"
    assert normalized["unknown_acknowledged"] is True
    assert normalized["catalog_revision"] == "c" * 64
    assert normalized["nodes"][0]["image"].endswith("@" + DIGEST)
    assert normalized["nodes"][0]["source_revision"] == "d" * 40


def test_normalize_substrate_preserves_local_compiled_artifact_identity():
    config_id = "sha256:" + "9" * 64
    executable_digest = "sha256:" + "8" * 64
    substrate = _substrate(image=config_id)
    substrate["nodes"][0].update(
        {
            "image_config_id": config_id,
            "executable_digest": executable_digest,
            "platform": "linux/amd64",
            "runtime_interface": "extracted-binary",
            "operator_registration_id": "registration-release-build-ece2067b72e048208d3dceedb8bc7b48-stock",
            "controller_installation_id": "install-selected",
            "operator_overlay_revision": "sha256:" + "7" * 64,
        }
    )

    normalized = normalize_substrate(substrate)

    assert {
        field: normalized["nodes"][0][field]
        for field in (
            "image",
            "image_config_id",
            "executable_digest",
            "platform",
            "runtime_interface",
            "operator_registration_id",
            "controller_installation_id",
            "operator_overlay_revision",
        )
    } == {
        field: substrate["nodes"][0][field]
        for field in (
            "image",
            "image_config_id",
            "executable_digest",
            "platform",
            "runtime_interface",
            "operator_registration_id",
            "controller_installation_id",
            "operator_overlay_revision",
        )
    }


def test_normalize_substrate_rejects_mismatched_local_config_identity():
    substrate = _substrate(image="sha256:" + "9" * 64)
    substrate["nodes"][0].update(
        {
            "image_config_id": "sha256:" + "a" * 64,
            "executable_digest": "sha256:" + "8" * 64,
            "platform": "linux/amd64",
            "runtime_interface": "extracted-binary",
            "operator_registration_id": "registration-release-build-selected-stock",
            "controller_installation_id": "install-selected",
            "operator_overlay_revision": "sha256:" + "7" * 64,
        }
    )

    with pytest.raises(ValueError, match="image must equal image_config_id"):
        normalize_substrate(substrate)


def test_normalize_substrate_preserves_validated_controller_ownership():
    substrate = {
        **_substrate(),
        "controller_ownership": {
            "installation_id": "install-1",
            "topology_id": "profile-a",
        },
    }

    normalized = normalize_substrate(substrate)

    assert normalized["controller_ownership"] == substrate["controller_ownership"]


def test_normalize_substrate_preserves_absolute_docker_host_runtime_root():
    substrate = {
        **_substrate(),
        "docker_host_runtime_root": "/srv/dwarf/controller-state/controller/topology/current",
    }

    normalized = normalize_substrate(substrate)

    assert normalized["docker_host_runtime_root"] == substrate["docker_host_runtime_root"]


def test_normalize_substrate_preserves_adapter_and_public_peer_contract():
    substrate = {
        **_substrate(),
        "deployment_adapter": "cardano-public-peer",
        "network": "preview",
        "network_magic": 2,
        "version_policy_source": "implicit-default",
        "config_source_dir": "/opt/dwarf/cardano-configs/preview",
        "upstream_peer_address": "preview-node.play.dev.cardano.org:3001",
        "listen_address": "127.0.0.1:39100",
    }

    normalized = normalize_substrate(substrate)

    assert normalized["deployment_adapter"] == "cardano-public-peer"
    assert normalized["network"] == "preview"
    assert normalized["network_magic"] == 2
    assert normalized["version_policy_source"] == "implicit-default"
    assert normalized["config_source_dir"] == "/opt/dwarf/cardano-configs/preview"
    assert normalized["upstream_peer_address"] == "preview-node.play.dev.cardano.org:3001"
    assert normalized["listen_address"] == "127.0.0.1:39100"

    plan = allocate_node_plan(
        normalized,
        runtime_root=Path("/tmp/dwarf-public-profile"),
        compose_project="dwarf-public-profile",
    )
    assert plan["nodes"][0]["listen_address"] == "127.0.0.1:39100"
    assert plan["upstream_peer_address"] == "preview-node.play.dev.cardano.org:3001"


def test_normalize_substrate_rejects_mismatched_public_network_magic():
    substrate = {**_substrate(), "network": "preview", "network_magic": 1}

    with pytest.raises(ValueError, match="same public network"):
        normalize_substrate(substrate)


def test_missing_docker_image_is_not_reported_as_present(tmp_path):
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return _result(command, returncode=1, stderr="No such image")

    report = resolve_requested_versions(
        substrate=_substrate(), output_dir=tmp_path, runner=runner, which=lambda _: None
    )

    assert report["satisfied"] is False
    assert report["nodes"]["node1"]["status"] == "image-missing"
    assert calls == [["docker", "image", "inspect", _substrate()["nodes"][0]["image"]]]


def test_docker_image_resolution_captures_immutable_identity(tmp_path):
    image = _substrate()["nodes"][0]["image"]
    inspect_body = [{"Id": IMAGE_ID, "RepoDigests": [f"ghcr.io/example/cardano-node@{DIGEST}"]}]

    def runner(command, **kwargs):
        return _result(command, stdout=json.dumps(inspect_body))

    report = resolve_requested_versions(
        substrate=_substrate(), output_dir=tmp_path, runner=runner, which=lambda _: None
    )
    node = report["nodes"]["node1"]

    assert report["satisfied"] is True
    assert node["status"] == "image-present"
    assert node["image_ref"] == image
    assert node["image_id"] == IMAGE_ID
    assert node["image_digest"] == DIGEST
    assert node["source_revision"] == "d" * 40
    assert report["version_policy"] == "latest-stable"
    assert report["version_status"] == "unknown"
    assert report["unknown_acknowledged"] is True


def test_pinned_digest_mismatch_fails_closed():
    image = _substrate()["nodes"][0]["image"]
    other_digest = "sha256:" + "e" * 64

    def runner(command, **kwargs):
        return _result(
            command,
            stdout=json.dumps(
                [{"Id": IMAGE_ID, "RepoDigests": [f"ghcr.io/example/cardano-node@{other_digest}"]}]
            ),
        )

    result = resolve_docker_image_for_node(_substrate()["nodes"][0], runner=runner)

    assert result["satisfied"] is False
    assert result["status"] == "image-digest-mismatch"
    assert result["requested_digest"] == DIGEST


def test_locally_built_image_may_be_pinned_by_its_inspected_image_id():
    node = dict(_substrate()["nodes"][0])
    node["image"] = f"dwarf/cardano-measurement:local@{IMAGE_ID}"

    def runner(command, **kwargs):
        return _result(
            command,
            stdout=json.dumps([{"Id": IMAGE_ID, "RepoDigests": []}]),
        )

    result = resolve_docker_image_for_node(node, runner=runner)

    assert result["satisfied"] is True
    assert result["status"] == "image-present"
    assert result["requested_digest"] is None
    assert result["requested_config_id"] == IMAGE_ID
    assert result["image_id"] == IMAGE_ID
    assert result["image_config_id"] == IMAGE_ID
    assert result["image_digest"] is None


def test_running_container_must_report_requested_node_version():
    node = _substrate()["nodes"][0]

    def good_runner(command, **kwargs):
        return _result(command, stdout="cardano-node 11.1.2 - linux-x86_64\ngit rev ddddddd\n")

    def bad_runner(command, **kwargs):
        return _result(command, stdout="cardano-node 10.7.1 - linux-x86_64\n")

    good = verify_running_node_version("candidate-node1-1", node, runner=good_runner)
    bad = verify_running_node_version("candidate-node1-1", node, runner=bad_runner)

    assert good["satisfied"] is True
    assert good["reported_version"] == "11.1.2"
    assert bad["satisfied"] is False
    assert bad["status"] == "running-version-mismatch"
    assert bad["reported_version"] == "10.7.1"


def test_local_amaru_release_may_attest_label_with_clean_source_revision():
    source_revision = "23031e0ed4423b9cb899da030d515cb69d203749"
    config_id = "sha256:" + "9" * 64
    node = {
        **_substrate(image=config_id)["nodes"][0],
        "impl": "amaru",
        "version": "10.11.20261029",
        "source_revision": source_revision,
        "image_config_id": config_id,
        "executable_digest": "sha256:" + "8" * 64,
        "platform": "linux/amd64",
        "runtime_interface": "extracted-binary",
        "operator_registration_id": "registration-release-build-selected-stock",
        "controller_installation_id": "install-selected",
        "operator_overlay_revision": "sha256:" + "7" * 64,
    }

    def runner(command, **kwargs):
        return _result(command, stdout="Amaru 10.11.0 (23031e0)\n")

    result = verify_running_node_version("candidate-amaru1-1", node, runner=runner)

    assert result["satisfied"] is True
    assert result["status"] == "running-operator-artifact-verified"
    assert result["version_match_mode"] == "operator-local-source-revision"
    assert result["reported_version"] == "10.11.0"
    assert result["reported_source_revision"] == "23031e0"
    assert result["reported_dirty"] is False


@pytest.mark.parametrize(
    "version_output",
    [
        "Amaru 10.11.0 (aaaaaaaa)",
        "Amaru 10.11.0 (23031e0+dirty)",
        "Amaru 10.11.0",
    ],
)
def test_local_amaru_release_fails_closed_without_clean_matching_revision(version_output):
    config_id = "sha256:" + "9" * 64
    node = {
        **_substrate(image=config_id)["nodes"][0],
        "impl": "amaru",
        "version": "10.11.20261029",
        "source_revision": "23031e0ed4423b9cb899da030d515cb69d203749",
        "image_config_id": config_id,
        "executable_digest": "sha256:" + "8" * 64,
        "platform": "linux/amd64",
        "runtime_interface": "extracted-binary",
        "operator_registration_id": "registration-release-build-selected-stock",
        "controller_installation_id": "install-selected",
        "operator_overlay_revision": "sha256:" + "7" * 64,
    }

    def runner(command, **kwargs):
        return _result(command, stdout=version_output)

    result = verify_running_node_version("candidate-amaru1-1", node, runner=runner)

    assert result["satisfied"] is False
    assert result["status"] == "running-version-mismatch"


def test_running_container_must_use_the_resolved_image_id():
    node = {"id": "node1", "image_id": IMAGE_ID, "image_ref": "example:11.1.2"}

    good = verify_running_container_artifact(IMAGE_ID, node)
    bad = verify_running_container_artifact("sha256:" + "f" * 64, node)

    assert good["satisfied"] is True
    assert good["status"] == "running-image-verified"
    assert bad["satisfied"] is False
    assert bad["status"] == "running-image-mismatch"


def test_version_provenance_is_bundle_safe_and_exact():
    normalized = normalize_substrate(_substrate())
    node = {
        **normalized["nodes"][0],
        "image_ref": normalized["nodes"][0]["image"],
        "image_id": IMAGE_ID,
        "image_digest": DIGEST,
        "resolved_version": "11.1.2",
        "version_identity": {
            "status": "running-version-verified",
            "reported_version": "11.1.2",
            "reported_source_revision": "d" * 40,
            "reported_dirty": False,
            "version_match_mode": "exact-version",
            "satisfied": True,
        },
    }

    provenance = build_version_provenance(normalized, [node])

    assert provenance["policy"] == "latest-stable"
    assert provenance["policy_source"] == "explicit"
    assert provenance["status"] == "unknown"
    assert provenance["unknown_acknowledged"] is True
    assert provenance["catalog_revision"] == "c" * 64
    assert provenance["nodes"][0]["image_digest"] == DIGEST
    assert provenance["nodes"][0]["reported_version"] == "11.1.2"
    assert provenance["nodes"][0]["reported_source_revision"] == "d" * 40
    assert provenance["nodes"][0]["reported_dirty"] is False
    assert provenance["nodes"][0]["version_match_mode"] == "exact-version"
    assert "password" not in json.dumps(provenance).lower()


def test_version_provenance_retains_local_compiled_identity_without_digest_conflation():
    config_id = "sha256:" + "9" * 64
    normalized = normalize_substrate(
        {
            **_substrate(image=config_id),
            "nodes": [
                {
                    **_substrate(image=config_id)["nodes"][0],
                    "image_config_id": config_id,
                    "executable_digest": "sha256:" + "8" * 64,
                    "platform": "linux/amd64",
                    "runtime_interface": "extracted-binary",
                    "operator_registration_id": "registration-release-build-ece2067b72e048208d3dceedb8bc7b48-stock",
                    "controller_installation_id": "install-selected",
                    "operator_overlay_revision": "sha256:" + "7" * 64,
                }
            ],
        }
    )
    node = {
        **normalized["nodes"][0],
        "image_ref": config_id,
        "image_id": config_id,
        "image_digest": None,
        "resolved_version": "11.1.2",
        "version_identity": {
            "status": "running-version-verified",
            "reported_version": "11.1.2",
        },
    }

    provenance = build_version_provenance(normalized, [node])["nodes"][0]

    assert provenance["image_digest"] is None
    assert provenance["image_config_id"] == config_id
    assert provenance["executable_digest"] == "sha256:" + "8" * 64
    assert provenance["operator_registration_id"].endswith("-stock")
    assert provenance["controller_installation_id"] == "install-selected"
    assert provenance["operator_overlay_revision"] == "sha256:" + "7" * 64
