import json
from pathlib import Path

from profile_manager.deployment_versions import build_deployment_version_preview
from profile_manager.measurement_targets import measurement_target_record_path
from profile_manager.profiles import Profile, deploy_command, versioned_substrate_for_profile
from scripts.runtime_substrate_common import allocate_node_plan, normalize_substrate


REVISION = "fef83fed01d7926f3de83b3b917be5a4a48768b5"
PATCH_SET = "7a948067c6b957b277400675cf95e32864ed8d92cd130fbadb673775249b5cc1"
DIGEST = "sha256:" + "a" * 64


def _profile():
    return Profile.from_dict({
        "id": "profile-t-cardano-measurement-patched",
        "label": "Cardano-node 11.1.2 — patched measurement target",
        "haskell_count": 3,
        "amaru_count": 0,
        "network_magic": 42,
        "peer_sharing": False,
        "version_policy": "exact",
        "cardano_version": "11.1.2",
        "topology_pattern": "local-mesh",
        "shared_genesis": True,
        "measurement_target_mode": "patched",
        "measurement_patch_revision": REVISION,
        "measurement_patch_set_sha256": PATCH_SET,
        "cardano_measurement_traces": True,
    })


def _record():
    return {
        "schema_version": 1,
        "implementation": "cardano-node",
        "mode": "patched",
        "version": "11.1.2",
        "source_revision": REVISION,
        "patch_set_sha256": PATCH_SET,
        "image_reference": "dwarf/cardano-measurement@" + DIGEST,
        "image_digest": DIGEST,
        "executable_digest": "sha256:" + "b" * 64,
        "build_result_sha256": "sha256:" + "c" * 64,
        "runtime_probe_log_sha256": "sha256:" + "d" * 64,
    }


def test_profile_catalog_retains_exact_patched_cardano_intent():
    body = json.loads(Path(
        "dwarf/profiles/profile-t-cardano-measurement-patched/profile.yaml"
    ).read_text())
    assert body["cardano_version"] == "11.1.2"
    assert body["measurement_target_mode"] == "patched"
    assert body["measurement_patch_revision"] == REVISION
    assert body["measurement_patch_set_sha256"] == PATCH_SET
    assert body["cardano_measurement_traces"] is True


def test_patched_profile_substitutes_only_cardano_images(tmp_path, monkeypatch):
    record = _record()
    path = measurement_target_record_path(
        "cardano-node", REVISION, PATCH_SET, registry_root=tmp_path
    )
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(record) + "\n")
    monkeypatch.setenv("ADA2_DWARF_MEASUREMENT_TARGET_REGISTRY", str(tmp_path))
    profile = _profile()
    preview = build_deployment_version_preview(profile.__dict__)

    substrate = versioned_substrate_for_profile(profile, preview)

    assert len(substrate["nodes"]) == 3
    assert substrate["cardano_measurement_traces"] is True
    assert all(node["impl"] == "cardano-node" for node in substrate["nodes"])
    assert all(node["target_mode"] == "patched" for node in substrate["nodes"])
    assert all(node["image"] == record["image_reference"] for node in substrate["nodes"])
    assert all(node["patch_set_sha256"] == PATCH_SET for node in substrate["nodes"])

    normalized = normalize_substrate(substrate)
    assert all(node["target_mode"] == "patched" for node in normalized["nodes"])
    assert all(node["image_digest"] == DIGEST for node in normalized["nodes"])
    assert all(node["patch_set_sha256"] == PATCH_SET for node in normalized["nodes"])

    plan = allocate_node_plan(
        substrate,
        runtime_root=tmp_path / "runtime",
        compose_project="dwarf-profile-t-cardano-measurement-patched",
    )
    assert all(node["target_mode"] == "patched" for node in plan["nodes"])
    assert all(node["executable_digest"] == record["executable_digest"] for node in plan["nodes"])


def test_patched_cardano_deploy_probes_the_cardano_image_without_amaru_wrapper(
    tmp_path, monkeypatch
):
    record = _record()
    path = measurement_target_record_path(
        "cardano-node", REVISION, PATCH_SET, registry_root=tmp_path
    )
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(record) + "\n")
    monkeypatch.setenv("ADA2_DWARF_MEASUREMENT_TARGET_REGISTRY", str(tmp_path))
    profile = _profile()

    command = deploy_command(
        profile,
        version_preview=build_deployment_version_preview(profile.__dict__),
    )

    assert "docker run --rm --entrypoint /usr/local/bin/cardano-node" in command
    assert "DWARF patched-target Cardano-node compatibility probe" in command
    assert "/usr/local/bin/amaru" not in command
    assert "Patched Amaru image" not in command
