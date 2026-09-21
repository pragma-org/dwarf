import json
from pathlib import Path

import pytest

from profile_manager.deployment_versions import (
    DeploymentVersionGateError,
    build_deployment_version_preview,
    build_measurement_target_identity,
)
from profile_manager.measurement_resolution import (
    MeasurementResolutionError,
    resolve_measurements,
)
from profile_manager.scenario import scenario_from_body


AMARU_VERSION = "10.11.20260912"
AMARU_REVISION = "b159172f25a9c389f82f20bca4f15e3032791638"
AMARU_DIGEST = "sha256:45d46a6ba7147bfa95d96c103820542a9e3ac3602c4c316cc0d04bbd6d71489e"


def _scenario(*, profile=None, measurements=None):
    document = {
        "spec_version": "v1",
        "id": "amaru-measurement-resolution",
        "title": "Amaru measurement resolution",
        "target": {"implementation": "amaru", "version": AMARU_VERSION},
        "runtime": "library",
        "setup": [],
        "load": [],
        "faults": [],
        "probes": [],
        "assertions": [],
        "teardown": [],
    }
    if profile is not None:
        document["measurement_profile"] = profile
    if measurements is not None:
        document["measurements"] = measurements
    return scenario_from_body((json.dumps(document) + "\n").encode())


def _identity(**updates):
    identity = {
        "implementation": "amaru",
        "version": AMARU_VERSION,
        "source_revision": AMARU_REVISION,
        "mode": "stock",
        "image_reference": f"ghcr.io/pragma-org/amaru@{AMARU_DIGEST}",
        "image_digest": AMARU_DIGEST,
        "executable_digest": None,
        "version_catalog_revision": "a" * 64,
    }
    identity.update(updates)
    return identity


def test_deployment_preview_builds_exact_stock_measurement_identity():
    profile = json.loads(
        Path("dwarf/profiles/profile-o-amaru-target-latest-confirmed/profile.yaml").read_text()
    )
    preview = build_deployment_version_preview(profile)

    identity = build_measurement_target_identity(
        preview, implementation="amaru", mode="stock"
    )

    assert identity["version"] == AMARU_VERSION
    assert identity["source_revision"] == AMARU_REVISION
    assert identity["image_digest"] == AMARU_DIGEST
    assert identity["image_reference"].endswith(f"@{AMARU_DIGEST}")
    assert len(identity["version_catalog_revision"]) == 64

    with pytest.raises(DeploymentVersionGateError, match="reference.*digest"):
        build_measurement_target_identity(
            preview,
            implementation="amaru",
            mode="stock",
            image_reference="ghcr.io/pragma-org/amaru@sha256:" + "9" * 64,
            image_digest=AMARU_DIGEST,
        )


def test_implicit_default_resolves_available_taps_and_explains_skips():
    resolution = resolve_measurements(
        _scenario(),
        target_identity=_identity(),
        capabilities={"dwarf-workload-events"},
    )

    assert resolution["profile"] == {
        "id": "amaru-security-default",
        "source": "implicit-default",
        "definition_digest": resolution["profile"]["definition_digest"],
    }
    assert [entry["id"] for entry in resolution["resolved"]] == [
        "amaru-external-workload-accounting"
    ]
    assert resolution["resolved"][0]["definition_digest"].startswith("sha256:")
    assert resolution["incompatible"] == []
    assert resolution["disabled"] == []
    assert resolution["skipped"]
    assert all("missing capabilities" in entry["reason"] for entry in resolution["skipped"])


def test_explicit_none_disables_implicit_defaults():
    resolution = resolve_measurements(
        _scenario(profile="none"),
        target_identity=_identity(),
        capabilities={"dwarf-workload-events"},
    )

    assert resolution["profile"] == {"id": "none", "source": "explicit-none"}
    assert resolution["requested"] == []
    assert resolution["resolved"] == []
    assert resolution["skipped"] == []


def test_individual_disable_is_retained_and_not_resolved():
    resolution = resolve_measurements(
        _scenario(
            measurements=[{"id": "amaru-external-workload-accounting", "enabled": False}]
        ),
        target_identity=_identity(),
        capabilities={"dwarf-workload-events"},
    )

    assert resolution["resolved"] == []
    assert resolution["disabled"][0]["id"] == "amaru-external-workload-accounting"
    assert resolution["disabled"][0]["source"] == "scenario-override"


def test_launch_override_is_applied_after_scenario_selection():
    resolution = resolve_measurements(
        _scenario(profile="none"),
        target_identity=_identity(),
        capabilities={"dwarf-workload-events"},
        run_overrides=[
            {
                "id": "amaru-external-workload-accounting",
                "enabled": True,
                "parameters": {"window_seconds": 10},
                "threshold_gate": {"enabled": False, "thresholds": []},
            }
        ],
    )

    assert resolution["resolved"][0]["source"] == "launch-override"
    assert resolution["resolved"][0]["parameters"] == {"window_seconds": 10}


def test_launch_override_cannot_smuggle_an_implicit_or_unsupported_gate():
    with pytest.raises(MeasurementResolutionError, match="enabled must be boolean"):
        resolve_measurements(
            _scenario(profile="none"),
            target_identity=_identity(),
            capabilities={"dwarf-workload-events"},
            run_overrides=[{
                "id": "amaru-external-workload-accounting",
                "enabled": True,
                "threshold_gate": {"enabled": "yes", "thresholds": []},
            }],
        )

    with pytest.raises(MeasurementResolutionError, match="does not support threshold gating"):
        resolve_measurements(
            _scenario(profile="none"),
            target_identity=_identity(
                mode="coverage",
                image_reference=None,
                image_digest=None,
                executable_digest="sha256:" + "8" * 64,
                build_result_sha256="sha256:" + "7" * 64,
                coverage_harness_sha256="6" * 64,
                engine="cargo-fuzz/libFuzzer",
                performance_authority="non-authoritative",
                non_authoritative_performance=True,
            ),
            capabilities={"amaru-coverage-build", "dwarf-coverage-collector"},
            run_overrides=[{
                "id": "amaru-coverage-production-paths",
                "enabled": True,
                "threshold_gate": {
                    "enabled": True,
                    "thresholds": [{"metric": "lines", "operator": "gte", "value": 1}],
                },
            }],
        )


@pytest.mark.parametrize(
    ("measurement_id", "identity_updates", "capabilities", "message"),
    [
        ("amaru-stock-mempool", {"version": "10.11.0"}, {"amaru-mempool-metrics", "amaru-json-traces"}, "version"),
        ("amaru-stock-mempool", {"source_revision": "c" * 40}, {"amaru-mempool-metrics", "amaru-json-traces"}, "source revision"),
        ("amaru-patched-protocol-decode", {}, {"amaru-measurement-patch-protocol-decode", "amaru-patch-identity"}, "target mode"),
        ("amaru-stock-mempool", {}, {"amaru-mempool-metrics"}, "missing capabilities"),
    ],
)
def test_explicit_incompatible_tap_fails_before_execution(
    measurement_id, identity_updates, capabilities, message
):
    scenario = _scenario(
        profile="none",
        measurements=[{"id": measurement_id, "enabled": True}],
    )

    with pytest.raises(MeasurementResolutionError, match=message) as raised:
        resolve_measurements(
            scenario,
            target_identity=_identity(**identity_updates),
            capabilities=capabilities,
        )

    assert raised.value.resolution["incompatible"][0]["id"] == measurement_id


def test_target_identity_requires_immutable_digest_before_resolution():
    with pytest.raises(MeasurementResolutionError, match="image_digest"):
        resolve_measurements(
            _scenario(profile="none"),
            target_identity=_identity(image_digest="latest"),
            capabilities=set(),
        )


def test_coverage_target_uses_immutable_executable_identity_without_fake_image():
    identity = _identity(
        mode="coverage",
        image_reference=None,
        image_digest=None,
        executable_digest="sha256:" + "e" * 64,
        build_result_sha256="sha256:" + "b" * 64,
        coverage_harness_sha256="c" * 64,
        engine="cargo-fuzz/libFuzzer",
        performance_authority="non-authoritative",
        non_authoritative_performance=True,
    )

    resolution = resolve_measurements(
        _scenario(profile="none"),
        target_identity=identity,
        capabilities=set(),
    )

    assert resolution["target_identity"]["image_digest"] is None
    assert resolution["target_identity"]["executable_digest"] == "sha256:" + "e" * 64

    identity["executable_digest"] = None
    with pytest.raises(MeasurementResolutionError, match="executable_digest"):
        resolve_measurements(
            _scenario(profile="none"),
            target_identity=identity,
            capabilities=set(),
        )
