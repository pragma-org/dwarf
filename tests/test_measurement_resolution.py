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


def _local_identity(**updates):
    identity = {
        "implementation": "amaru",
        "version": "10.11.20260912-local-20261013",
        "source_revision": None,
        "mode": "stock",
        "identity_kind": "operator-local-config",
        "image_reference": "sha256:" + "2" * 64,
        "image_digest": None,
        "image_config_id": "sha256:" + "2" * 64,
        "executable_digest": "sha256:" + "3" * 64,
        "platform": "linux/amd64",
        "operator_registration_id": "registration-local-amaru",
        "operator_overlay_revision": "sha256:" + "4" * 64,
        "controller_installation_id": "install-test",
        "version_catalog_revision": "5" * 64,
    }
    identity.update(updates)
    return identity


def _experimental_consent(scenario, *, identity=None, capabilities=None):
    with pytest.raises(MeasurementResolutionError) as raised:
        resolve_measurements(
            scenario,
            target_identity=identity or _local_identity(),
            capabilities=capabilities or {"dwarf-workload-events"},
        )
    return raised.value.resolution["experimental_consent_required"]


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


def test_deployment_preview_recovers_legacy_import_executable_from_exact_record(
    tmp_path,
):
    from profile_manager.operator_version_decisions import OperatorVersionDecisionLedger

    registration_id = "registration-0123456789abcdef01234567"
    config_id = "sha256:" + "4" * 64
    registration = {
        "registration_id": registration_id,
        "implementation": "cardano-node",
        "version": "11.1.2-local",
        "image_reference": config_id,
        "manifest_digest": None,
        "config_image_id": config_id,
        "platform": "linux/amd64",
        "runtime_interface": None,
        "controller_installation_id": "install-test",
        "inspected_at": "2026-10-13T12:00:00Z",
        "provenance": {
            "source_revision": None,
            "public": None,
            "qualification": "unverified",
        },
    }
    ledger = OperatorVersionDecisionLedger(tmp_path)
    ledger.append_registration(
        registration, expected_revision=ledger.read()["revision"]
    )
    job = tmp_path / "version-imports" / "import-0123456789abcdef01234567"
    job.mkdir(parents=True)
    (job / "request.json").write_text(
        json.dumps(
            {
                "schema_version": "v1",
                "import_id": "import-0123456789abcdef01234567",
                "execution_id": "execution-" + "1" * 32,
                "implementation": "cardano-node",
                "version": "11.1.2-local",
                "platform": "linux/amd64",
                    "upload_sha256": "3" * 64,
                "executable_sha256": "3" * 64,
                "executable_size": 123,
                "created_at": "2026-10-13T12:00:00Z",
                "expires_at": "2026-10-13T13:00:00Z",
                "state": "registered",
                "registration_id": registration_id,
                "config_image_id": config_id,
                "completed_at": "2026-10-13T12:01:00Z",
                "audit_receipt": "controller/controller-audit/audit-test.json",
                    "declared_source_revision": None,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    profile = json.loads(
        Path(
            "dwarf/profiles/profile-s-cardano-measurement-stock-control/profile.yaml"
        ).read_text()
    )

    def retained_status(operation, parameters, timeout, idempotency_key=None):
        assert operation == "execution.status"
        assert parameters == {"execution_id": "execution-" + "1" * 32}
        assert timeout == 10
        return {
            "execution_id": parameters["execution_id"],
            "state": "applied",
            "audit_receipt": "controller/controller-audit/audit-test.json",
            "failure": None,
            "result": {
                "status": "packaged",
                "implementation": "cardano-node",
                "version": "11.1.2-local",
                "config_image_id": config_id,
                "manifest_references": [],
                "platform": "linux/amd64",
                "runtime_interface": None,
                "upload_sha256": "sha256:" + "3" * 64,
                "executable_sha256": "sha256:" + "3" * 64,
                "declared_source_revision": None,
                "provenance": "operator-upload",
                "qualification": "unverified",
                "compatibility": "unknown-until-run",
                "controller_installation_id": "install-test",
                "packaged_at": "2026-10-13T12:00:00Z",
            },
        }

    preview = build_deployment_version_preview(
        profile,
        operator_registration_id=registration_id,
        operator_state_dir=tmp_path,
        request_controller=retained_status,
    )
    identity = build_measurement_target_identity(
        preview, implementation="cardano-node", mode="stock"
    )

    assert identity["identity_kind"] == "operator-local-config"
    assert identity["executable_digest"] == "sha256:" + "3" * 64
    assert identity["operator_overlay_revision"] == ledger.read()["revision"]
    assert preview["resolved"]["cardano-node"]["operator_package_proof"] == {
        "status": "verified",
        "registration_id": registration_id,
        "config_image_id": config_id,
        "executable_digest": "sha256:" + "3" * 64,
        "platform": "linux/amd64",
        "runtime_interface": None,
        "controller_installation_id": "install-test",
    }
    assert "executable_digest" not in ledger.read()["registrations"][0]


def test_manually_inspected_local_image_without_retained_import_has_no_measurement_identity(
    tmp_path,
):
    from profile_manager.operator_version_decisions import OperatorVersionDecisionLedger

    registration_id = "registration-manual-local-cardano"
    config_id = "sha256:" + "4" * 64
    ledger = OperatorVersionDecisionLedger(tmp_path)
    ledger.append_registration(
        {
            "registration_id": registration_id,
            "implementation": "cardano-node",
            "version": "11.1.2-local",
            "image_reference": config_id,
            "manifest_digest": None,
            "config_image_id": config_id,
            "platform": "linux/amd64",
            "runtime_interface": None,
            "controller_installation_id": "install-test",
            "inspected_at": "2026-10-13T12:00:00Z",
            "provenance": {
                "source_revision": None,
                "public": None,
                "qualification": "unverified",
            },
        },
        expected_revision=ledger.read()["revision"],
    )
    profile = json.loads(
        Path(
            "dwarf/profiles/profile-s-cardano-measurement-stock-control/profile.yaml"
        ).read_text()
    )
    preview = build_deployment_version_preview(
        profile,
        operator_registration_id=registration_id,
        operator_state_dir=tmp_path,
    )

    with pytest.raises(DeploymentVersionGateError, match="executable digest"):
        build_measurement_target_identity(
            preview, implementation="cardano-node", mode="stock"
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


def test_local_source_unknown_passive_tap_requires_exact_experimental_consent():
    scenario = _scenario(
        profile="none",
        measurements=[{"id": "amaru-external-workload-accounting", "enabled": True}],
    )

    with pytest.raises(MeasurementResolutionError, match="experimental consent") as raised:
        resolve_measurements(
            scenario,
            target_identity=_local_identity(),
            capabilities={"dwarf-workload-events"},
        )

    candidate = raised.value.resolution["experimental"][0]
    assert candidate["id"] == "amaru-external-workload-accounting"
    assert candidate["compatibility"]["state"] == "unknown"
    assert candidate["capability"]["state"] == "available"
    assert raised.value.resolution["resolved"] == []

    expected = candidate_resolution = raised.value.resolution[
        "experimental_consent_required"
    ]
    assert expected["tap_ids"] == ["amaru-external-workload-accounting"]
    assert expected["context_digest"].startswith("sha256:")

    resolution = resolve_measurements(
        scenario,
        target_identity=_local_identity(),
        capabilities={"dwarf-workload-events"},
        experimental_consent=candidate_resolution,
    )

    resolved = resolution["resolved"][0]
    assert resolved["id"] == "amaru-external-workload-accounting"
    assert resolved["compatibility"]["state"] == "unknown"
    assert resolved["capability"]["state"] == "available"
    assert resolved["experimental_consent"] is True
    assert resolution["experimental_consent"]["tap_ids"] == [
        "amaru-external-workload-accounting"
    ]
    assert resolution["experimental_consent"]["target_identity"]["image_config_id"] == (
        "sha256:" + "2" * 64
    )


def test_local_arbitrary_declared_source_remains_unknown_and_consentable():
    scenario = _scenario(
        profile="none",
        measurements=[{"id": "amaru-external-workload-accounting", "enabled": True}],
    )

    identity = _local_identity(source_revision="f" * 40)
    consent = _experimental_consent(scenario, identity=identity)
    resolution = resolve_measurements(
        scenario,
        target_identity=identity,
        capabilities={"dwarf-workload-events"},
        experimental_consent=consent,
    )

    assert resolution["resolved"][0]["compatibility"] == {
        "state": "unknown",
        "reason": "operator-declared source revision has no confirmed tap contract",
    }


def test_local_stock_telemetry_capability_is_unknown_until_output_is_validated():
    scenario = _scenario(
        profile="none",
        measurements=[{"id": "amaru-stock-network", "enabled": True}],
    )
    capabilities = {"amaru-network-traces", "amaru-protocol-metrics"}
    consent = _experimental_consent(
        scenario, identity=_local_identity(), capabilities=capabilities
    )

    resolution = resolve_measurements(
        scenario,
        target_identity=_local_identity(),
        capabilities=capabilities,
        experimental_consent=consent,
    )

    assert resolution["resolved"][0]["capability"] == {
        "state": "unknown",
        "reason": "stock telemetry is configured but output has not been validated for this binary",
    }


def test_local_explicit_known_incompatible_evidence_still_blocks_consent():
    scenario = _scenario(
        profile="none",
        measurements=[{"id": "amaru-external-workload-accounting", "enabled": True}],
    )
    identity = _local_identity(
        known_incompatible_measurements=["amaru-external-workload-accounting"]
    )

    with pytest.raises(MeasurementResolutionError, match="known-incompatible") as raised:
        resolve_measurements(
            scenario,
            target_identity=identity,
            capabilities={"dwarf-workload-events"},
            experimental_consent={
                "tap_ids": ["amaru-external-workload-accounting"],
                "context_digest": "sha256:" + "9" * 64,
            },
        )

    assert raised.value.resolution["incompatible"][0]["id"] == (
        "amaru-external-workload-accounting"
    )


def test_local_patched_tap_cannot_consent_around_missing_instrumentation():
    scenario = _scenario(
        profile="none",
        measurements=[{"id": "amaru-patched-protocol-decode", "enabled": True}],
    )

    with pytest.raises(MeasurementResolutionError, match="missing capabilities") as raised:
        resolve_measurements(
            scenario,
            target_identity=_local_identity(),
            capabilities=set(),
            experimental_consent={
                "tap_ids": ["amaru-patched-protocol-decode"],
                "context_digest": "sha256:" + "9" * 64,
            },
        )

    unavailable = raised.value.resolution["unavailable"][0]
    assert unavailable["id"] == "amaru-patched-protocol-decode"
    assert unavailable["capability"]["state"] == "unavailable"


def test_local_verified_hook_can_be_tried_without_claiming_compatibility():
    scenario = _scenario(
        profile="none",
        measurements=[{"id": "amaru-patched-protocol-decode", "enabled": True}],
    )
    capabilities = {
        "amaru-measurement-patch-protocol-decode",
        "amaru-patch-identity",
    }

    consent = _experimental_consent(
        scenario,
        identity=_local_identity(verified_instrumentation={
            "capabilities": sorted(capabilities),
            "evidence_sha256": "sha256:" + "7" * 64,
            "controller_installation_id": "install-test",
            "config_image_id": "sha256:" + "2" * 64,
        }),
        capabilities=capabilities,
    )
    resolution = resolve_measurements(
        scenario,
        target_identity=_local_identity(verified_instrumentation={
            "capabilities": sorted(capabilities),
            "evidence_sha256": "sha256:" + "7" * 64,
            "controller_installation_id": "install-test",
            "config_image_id": "sha256:" + "2" * 64,
        }),
        capabilities=capabilities,
        experimental_consent=consent,
    )

    resolved = resolution["resolved"][0]
    assert resolved["compatibility"]["state"] == "unknown"
    assert resolved["capability"] == {
        "state": "unknown",
        "reason": "required instrumentation is present but unqualified for this tap",
    }
    assert resolved["experimental_consent"] is True


def test_same_tap_consent_is_stale_after_local_image_identity_changes():
    scenario = _scenario(
        profile="none",
        measurements=[{"id": "amaru-external-workload-accounting", "enabled": True}],
    )
    consent = _experimental_consent(scenario)

    with pytest.raises(MeasurementResolutionError, match="context is stale") as raised:
        resolve_measurements(
            scenario,
            target_identity=_local_identity(
                image_reference="sha256:" + "6" * 64,
                image_config_id="sha256:" + "6" * 64,
            ),
            capabilities={"dwarf-workload-events"},
            experimental_consent=consent,
        )

    required = raised.value.resolution["experimental_consent_required"]
    assert required["tap_ids"] == consent["tap_ids"]
    assert required["context_digest"] != consent["context_digest"]

    with pytest.raises(MeasurementResolutionError, match="context is stale") as overlay:
        resolve_measurements(
            scenario,
            target_identity=_local_identity(
                operator_overlay_revision="sha256:" + "8" * 64,
            ),
            capabilities={"dwarf-workload-events"},
            experimental_consent=consent,
        )
    assert overlay.value.resolution["experimental_consent_required"][
        "context_digest"
    ] != consent["context_digest"]
