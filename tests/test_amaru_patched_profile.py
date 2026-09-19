import json

import pytest

from profile_manager.deployment_versions import build_deployment_version_preview
from profile_manager.measurement_targets import (
    MeasurementTargetError,
    measurement_target_record_path,
    resolve_patched_amaru_target,
)
from profile_manager.profiles import (
    Profile,
    deploy_command,
    deploy_dry_run_text,
    versioned_substrate_for_profile,
)


REVISION = "b159172f25a9c389f82f20bca4f15e3032791638"
PATCH_SET = "f0e1aebca9adf2713d4d9f6f8ba33f20b0d04c3b35de6127d4a1e027a68b50af"
IMAGE_ID = "sha256:" + "f" * 64
EXECUTABLE = "sha256:" + "e" * 64


def _profile():
    return Profile.from_dict(
        {
            "id": "profile-q-amaru-measurement-patched",
            "label": "Amaru measurement target — patched",
            "haskell_count": 0,
            "amaru_count": 1,
            "network_magic": 42,
            "peer_sharing": False,
            "version_policy": "exact",
            "amaru_version": "10.11.20260912",
            "topology_pattern": "local-mesh",
            "shared_genesis": True,
            "measurement_target_mode": "patched",
            "measurement_patch_revision": REVISION,
            "measurement_patch_set_sha256": PATCH_SET,
            "amaru_json_traces": True,
        }
    )


def _record():
    return {
        "schema_version": 1,
        "implementation": "amaru",
        "mode": "patched",
        "version": "10.11.20260912",
        "source_revision": REVISION,
        "patch_set_sha256": PATCH_SET,
        "image_reference": (
            "dwarf/amaru-measurement@sha256:" + "f" * 64
        ),
        "image_digest": IMAGE_ID,
        "executable_digest": EXECUTABLE,
        "build_result_sha256": "sha256:" + "b" * 64,
        "runtime_probe_image": "wrapper@sha256:" + "c" * 64,
        "runtime_probe_log_sha256": "sha256:" + "d" * 64,
    }


def _write_record(root, record=None):
    path = measurement_target_record_path(
        "amaru", REVISION, PATCH_SET, registry_root=root
    )
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(record or _record()) + "\n", encoding="utf-8")
    return path


def test_patched_profile_retains_exact_target_intent():
    profile = _profile()

    assert profile.measurement_target_mode == "patched"
    assert profile.measurement_patch_revision == REVISION
    assert profile.measurement_patch_set_sha256 == PATCH_SET
    assert profile.amaru_version == "10.11.20260912"
    assert profile.amaru_json_traces is True


def test_patched_target_registry_fails_closed_on_missing_or_mismatched_identity(tmp_path):
    profile = _profile()
    with pytest.raises(MeasurementTargetError, match="not built"):
        resolve_patched_amaru_target(profile, registry_root=tmp_path)

    bad = _record()
    bad["source_revision"] = "0" * 40
    _write_record(tmp_path, bad)
    with pytest.raises(MeasurementTargetError, match="source revision"):
        resolve_patched_amaru_target(profile, registry_root=tmp_path)


def test_patched_profile_substitutes_only_amaru_image_and_preserves_control_lifecycle(
    tmp_path, monkeypatch
):
    profile = _profile()
    _write_record(tmp_path)
    monkeypatch.setenv("ADA2_DWARF_MEASUREMENT_TARGET_REGISTRY", str(tmp_path))
    preview = build_deployment_version_preview(profile.__dict__)

    substrate = versioned_substrate_for_profile(profile, preview)
    cardano = next(node for node in substrate["nodes"] if node["impl"] == "cardano-node")
    amaru = next(node for node in substrate["nodes"] if node["impl"] == "amaru")

    assert substrate["deployment_adapter"] == "amaru-control"
    assert substrate["amaru_json_traces"] is True
    assert cardano["version"] == "10.7.1"
    assert cardano["target_mode"] == "stock"
    assert "@sha256:" in cardano["image"]
    assert amaru["version"] == "10.11.20260912"
    assert amaru["target_mode"] == "patched"
    assert amaru["image"] == _record()["image_reference"]
    assert amaru["image_digest"] == IMAGE_ID
    assert amaru["patch_set_sha256"] == PATCH_SET

    command = deploy_command(profile, version_preview=preview)
    assert "runtime_amaru_control_substrate.py" in command
    assert '"lifecycle": "cardano_amaru_relay_bootstrap_control"' in command
    assert '"measurement_target_mode": "patched"' in command
    assert '"amaru_json_traces": true' in command
    assert "docker image inspect" in command
    assert "DWARF patched-target wrapper compatibility probe" in command
    assert IMAGE_ID in command
    assert f"docker pull {amaru['image']}" not in command


def test_stock_profile_does_not_require_patched_target_registry(monkeypatch):
    monkeypatch.setenv("ADA2_DWARF_MEASUREMENT_TARGET_REGISTRY", "/missing")
    profile = _profile()
    profile = Profile.from_dict(
        {
            **profile.__dict__,
            "id": "stock-amaru",
            "measurement_target_mode": "stock",
            "measurement_patch_revision": None,
            "measurement_patch_set_sha256": None,
        }
    )
    preview = build_deployment_version_preview(profile.__dict__)

    substrate = versioned_substrate_for_profile(profile, preview)
    amaru = next(node for node in substrate["nodes"] if node["impl"] == "amaru")

    assert amaru["target_mode"] == "stock"
    assert "@sha256:" in amaru["image"]


def test_patched_profile_dry_run_describes_local_identity_verification(tmp_path, monkeypatch):
    _write_record(tmp_path)
    monkeypatch.setenv("ADA2_DWARF_MEASUREMENT_TARGET_REGISTRY", str(tmp_path))

    text = deploy_dry_run_text(_profile())

    assert "pull stock and wrapper images" in text
    assert "verify the local patched image identity" in text
    assert "pull every digest-pinned image" not in text
