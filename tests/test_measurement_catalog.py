import io
import json
import tarfile
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from profile_manager import dashboard
from profile_manager.data.catalog_definitions import (
    InvalidDefinitionError,
    UnsafeDefinitionIdError,
    deterministic_catalog_archive,
    list_definitions,
    load_definition,
    save_definition,
)
from profile_manager.measurements import (
    validate_measurement_definition,
    validate_measurement_profile,
)


AMARU_VERSION = "10.11.20260912"
AMARU_REVISION = "b159172f25a9c389f82f20bca4f15e3032791638"


def _measurement(
    measurement_id: str = "amaru-stock-demo",
    *,
    collection_mode: str = "stock-telemetry",
    failure_behavior: str = "warn",
    threshold_supported: bool = True,
) -> dict:
    return {
        "schema_version": "v1",
        "id": measurement_id,
        "title": "Amaru stock demonstration measurement",
        "description": "Collects a bounded signal from the real pinned Amaru target.",
        "output_schema": "dwarf/spec/v1/measurement-result.schema.json",
        "compatibility": {
            "implementation": "amaru",
            "versions": [
                {
                    "version": AMARU_VERSION,
                    "source_revision": AMARU_REVISION,
                }
            ],
            "target_modes": ["stock"],
        },
        "collection_mode": collection_mode,
        "required_capabilities": ["amaru-json-traces"],
        "default_enabled": True,
        "overhead_class": "low",
        "collector": {
            "lifecycle": "run",
            "start": "before-workload",
            "stop": "after-recovery",
            "failure_behavior": failure_behavior,
        },
        "emitted_artifacts": ["measurements/amaru-stock-demo/result.json"],
        "correlation_identifiers": ["run_id", "workload_phase", "trace_id"],
        "threshold_gate": {
            "supported": threshold_supported,
            "default_enabled": False,
        },
    }


def _profile(measurement_id: str = "amaru-stock-demo") -> dict:
    return {
        "schema_version": "v1",
        "id": "amaru-security-demo",
        "title": "Amaru security demonstration profile",
        "description": "Attaches compatible passive measurements without gating security assertions.",
        "implementation": "amaru",
        "target_modes": ["stock"],
        "measurements": [
            {
                "id": measurement_id,
                "enabled": True,
                "parameters": {},
                "threshold_gate": {"enabled": False, "thresholds": []},
            }
        ],
    }


def _catalog_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    measurements = tmp_path / "measurements"
    profiles = tmp_path / "measurement-profiles"
    measurements.mkdir()
    profiles.mkdir()
    monkeypatch.setenv("ADA2_DWARF_MEASUREMENTS_DIR", str(measurements))
    monkeypatch.setenv("ADA2_DWARF_MEASUREMENT_PROFILES_DIR", str(profiles))
    return measurements, profiles


@pytest.mark.parametrize(
    "schema_name",
    ["measurement.schema.json", "measurement-profile.schema.json", "measurement-result.schema.json"],
)
def test_measurement_schemas_are_valid_draft_2020_12(schema_name):
    schema = json.loads(
        (Path("dwarf/spec/v1") / schema_name).read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize(
    "collection_mode",
    ["external", "stock-telemetry", "compiler-coverage", "patched-node"],
)
def test_measurement_contract_accepts_all_four_collection_modes(collection_mode):
    document = _measurement(collection_mode=collection_mode)
    document["compatibility"]["target_modes"] = {
        "external": ["stock", "patched"],
        "stock-telemetry": ["stock", "patched"],
        "compiler-coverage": ["coverage"],
        "patched-node": ["patched"],
    }[collection_mode]
    if collection_mode in {"compiler-coverage", "patched-node"}:
        document["default_enabled"] = False

    assert validate_measurement_definition(document) == document


def test_measurement_contract_requires_exact_version_revision_and_complete_runtime_fields():
    document = _measurement()
    del document["compatibility"]["versions"][0]["source_revision"]
    with pytest.raises(InvalidDefinitionError, match="source_revision"):
        validate_measurement_definition(document)

    document = _measurement()
    document["compatibility"]["versions"][0]["source_revision"] = "main"
    with pytest.raises(InvalidDefinitionError, match="source_revision"):
        validate_measurement_definition(document)

    for field in (
        "output_schema",
        "required_capabilities",
        "default_enabled",
        "overhead_class",
        "collector",
        "emitted_artifacts",
        "correlation_identifiers",
        "threshold_gate",
    ):
        document = _measurement()
        del document[field]
        with pytest.raises(InvalidDefinitionError, match=field):
            validate_measurement_definition(document)


def test_measurement_contract_keeps_security_runs_non_gating_by_default():
    document = _measurement(failure_behavior="fail-run", threshold_supported=False)
    with pytest.raises(InvalidDefinitionError, match="fail-run.*threshold"):
        validate_measurement_definition(document)

    document = _measurement(failure_behavior="fail-run", threshold_supported=True)
    document["threshold_gate"]["default_enabled"] = True
    with pytest.raises(InvalidDefinitionError, match="default_enabled"):
        validate_measurement_definition(document)


@pytest.mark.parametrize("output_schema", ["../outside.json", "dwarf/spec/v1/missing.json"])
def test_measurement_contract_requires_a_safe_existing_output_schema(output_schema):
    document = _measurement()
    document["output_schema"] = output_schema
    with pytest.raises(InvalidDefinitionError, match="output_schema"):
        validate_measurement_definition(document)


def test_measurement_profile_references_existing_measurements(tmp_path, monkeypatch):
    _catalog_fixture(tmp_path, monkeypatch)
    save_definition(
        "measurements",
        "amaru-stock-demo",
        json.dumps(_measurement()).encode(),
        create=True,
    )

    assert validate_measurement_profile(_profile()) == _profile()
    with pytest.raises(InvalidDefinitionError, match="unknown measurement"):
        validate_measurement_profile(_profile("amaru-stock-missing"))

    invalid_gate = _profile()
    invalid_gate["measurements"][0]["threshold_gate"]["enabled"] = True
    with pytest.raises(InvalidDefinitionError, match="at least one threshold"):
        validate_measurement_profile(invalid_gate)


def test_measurement_catalog_list_load_save_and_export_are_deterministic(tmp_path, monkeypatch):
    _, profiles = _catalog_fixture(tmp_path, monkeypatch)
    first = save_definition(
        "measurements",
        "amaru-stock-demo",
        json.dumps(_measurement()).encode(),
        create=True,
    )
    save_definition(
        "measurement-profiles",
        "amaru-security-demo",
        json.dumps(_profile()).encode(),
        create=True,
    )

    assert first.download_filename == "amaru-stock-demo.yaml"
    assert load_definition("measurements", "amaru-stock-demo").data["collection_mode"] == "stock-telemetry"
    assert [record.definition_id for record in list_definitions("measurements")] == ["amaru-stock-demo"]
    assert [record.definition_id for record in list_definitions("measurement-profiles")] == ["amaru-security-demo"]
    assert (profiles / "amaru-security-demo.yaml").is_file()

    first_archive = deterministic_catalog_archive("measurements")
    second_archive = deterministic_catalog_archive("measurements")
    assert first_archive == second_archive
    with tarfile.open(fileobj=io.BytesIO(first_archive), mode="r:gz") as archive:
        assert archive.getnames() == ["dwarf/measurements/amaru-stock-demo.yaml"]


@pytest.mark.parametrize("bad_id", ["../escape", "a/b", "._finder", ".DS_Store"])
def test_measurement_catalog_rejects_unsafe_ids(tmp_path, monkeypatch, bad_id):
    _catalog_fixture(tmp_path, monkeypatch)
    document = _measurement(measurement_id=bad_id)
    with pytest.raises((UnsafeDefinitionIdError, InvalidDefinitionError)):
        save_definition("measurements", bad_id, json.dumps(document).encode(), create=True)


def test_seed_catalog_is_exactly_version_pinned_and_modes_are_not_conflated():
    expected_amaru = {
        "amaru-stock-header-lifecycle",
        "amaru-stock-fork-switch",
        "amaru-stock-mempool",
        "amaru-stock-ledger-rules",
        "amaru-stock-plutus-execution",
        "amaru-stock-block-epoch",
        "amaru-stock-network",
        "amaru-stock-resources",
        "amaru-external-restart-readiness",
        "amaru-external-sync-speed",
        "amaru-external-workload-accounting",
        "amaru-coverage-production-paths",
        "amaru-patched-protocol-decode",
        "amaru-patched-blockfetch-queues",
        "amaru-patched-txsubmission-residence",
    }
    expected_cardano = {
        "cardano-stock-chain-lifecycle",
        "cardano-stock-blockfetch",
        "cardano-stock-txsubmission-mempool",
        "cardano-stock-ledger-block-epoch",
        "cardano-stock-plutus-execution",
        "cardano-stock-network",
        "cardano-stock-resources",
        "cardano-external-restart-readiness",
        "cardano-external-sync-speed",
        "cardano-external-workload-accounting",
        "cardano-coverage-production-paths",
        "cardano-patched-protocol-decode",
        "cardano-patched-blockfetch-handler-queue",
        "cardano-patched-txsubmission-residence",
        "cardano-patched-ledger-plutus-stages",
    }
    expected = expected_amaru | expected_cardano
    records = list_definitions("measurements")
    assert {record.definition_id for record in records} == expected
    for record in records:
        versions = record.data["compatibility"]["versions"]
        expected_identity = (
            {"version": AMARU_VERSION, "source_revision": AMARU_REVISION}
            if record.definition_id in expected_amaru
            else {
                "version": "11.1.2",
                "source_revision": "fef83fed01d7926f3de83b3b917be5a4a48768b5",
            }
        )
        assert versions == [expected_identity]
        if record.data["collection_mode"] in {"patched-node", "compiler-coverage"}:
            assert record.data["default_enabled"] is False

    profiles = {record.definition_id: record.data for record in list_definitions("measurement-profiles")}
    assert set(profiles) == {
        "amaru-security-default",
        "amaru-security-patched",
        "cardano-security-default",
        "cardano-security-patched",
    }
    assert all(
        selection["threshold_gate"]["enabled"] is False
        for profile in profiles.values()
        for selection in profile["measurements"]
    )


@pytest.mark.parametrize(
    ("catalog", "definition_id", "expected_text"),
    [
        ("measurements", "amaru-stock-mempool", "Amaru mempool admission and pressure"),
        ("measurement-profiles", "amaru-security-default", "stock default"),
    ],
)
def test_measurement_catalogs_use_shared_detail_and_editor_routes(
    catalog, definition_id, expected_text
):
    detail = dashboard.render_route_html(f"/operate/{catalog}/{definition_id}")
    edit = dashboard.render_route_html(f"/operate/{catalog}/{definition_id}/edit")
    create = dashboard.render_route_html(f"/operate/{catalog}/new")

    assert detail is not None and expected_text in detail
    assert edit is not None and f'data-catalog="{catalog}"' in edit
    assert create is not None and f'data-catalog="{catalog}"' in create
    assert "/learn/measurements" in edit
    assert "/learn/measurements" in create
