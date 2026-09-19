from pathlib import Path

from profile_manager.data.catalog_definitions import load_definition
from profile_manager.measurements import (
    validate_measurement_definition,
    validate_measurement_profile,
)


VERSION = "11.1.2"
REVISION = "fef83fed01d7926f3de83b3b917be5a4a48768b5"

STOCK = {
    "cardano-stock-chain-lifecycle",
    "cardano-stock-blockfetch",
    "cardano-stock-txsubmission-mempool",
    "cardano-stock-ledger-block-epoch",
    "cardano-stock-plutus-execution",
    "cardano-stock-network",
    "cardano-stock-resources",
}
EXTERNAL = {
    "cardano-external-restart-readiness",
    "cardano-external-sync-speed",
    "cardano-external-workload-accounting",
}
COVERAGE = {"cardano-coverage-production-paths"}
PATCHED = {
    "cardano-patched-protocol-decode",
    "cardano-patched-blockfetch-handler-queue",
    "cardano-patched-txsubmission-residence",
    "cardano-patched-ledger-plutus-stages",
}
IMPLEMENTED_PATCHED = {
    "cardano-patched-protocol-decode",
    "cardano-patched-ledger-plutus-stages",
}
ALL = STOCK | EXTERNAL | COVERAGE | PATCHED


def test_cardano_measurement_definitions_are_exact_and_non_gating() -> None:
    for measurement_id in sorted(ALL):
        record = load_definition("measurements", measurement_id)
        body = record.data
        assert validate_measurement_definition(body) == body
        assert body["compatibility"]["implementation"] == "cardano-node"
        assert body["compatibility"]["versions"] == [
            {"version": VERSION, "source_revision": REVISION}
        ]
        assert body["collector"]["failure_behavior"] in {"warn", "disable", "skip"}
        assert body["threshold_gate"]["default_enabled"] is False
        assert body["required_capabilities"]
        assert body["emitted_artifacts"]
        assert body["correlation_identifiers"]


def test_cardano_measurement_modes_match_their_authority() -> None:
    for measurement_id in STOCK:
        body = load_definition("measurements", measurement_id).data
        assert body["collection_mode"] == "stock-telemetry"
        assert body["compatibility"]["target_modes"] == ["stock", "patched"]
    for measurement_id in EXTERNAL:
        body = load_definition("measurements", measurement_id).data
        assert body["collection_mode"] == "external"
        assert body["compatibility"]["target_modes"] == ["stock", "patched"]
    for measurement_id in COVERAGE:
        body = load_definition("measurements", measurement_id).data
        assert body["collection_mode"] == "compiler-coverage"
        assert body["compatibility"]["target_modes"] == ["coverage"]
        assert body["default_enabled"] is False
    for measurement_id in PATCHED:
        body = load_definition("measurements", measurement_id).data
        assert body["collection_mode"] == "patched-node"
        assert body["compatibility"]["target_modes"] == ["patched"]
        assert body["default_enabled"] is False


def test_cardano_measurement_profiles_select_the_complete_compatible_sets() -> None:
    default = load_definition("measurement-profiles", "cardano-security-default").data
    patched = load_definition("measurement-profiles", "cardano-security-patched").data
    assert validate_measurement_profile(default) == default
    assert validate_measurement_profile(patched) == patched
    assert default["implementation"] == patched["implementation"] == "cardano-node"
    assert default["target_modes"] == ["stock"]
    assert patched["target_modes"] == ["patched"]
    assert {item["id"] for item in default["measurements"] if item["enabled"]} == STOCK | EXTERNAL
    assert {item["id"] for item in patched["measurements"] if item["enabled"]} == (
        STOCK | EXTERNAL | IMPLEMENTED_PATCHED
    )
    assert not (
        PATCHED - IMPLEMENTED_PATCHED
    ) & {item["id"] for item in patched["measurements"] if item["enabled"]}
    for profile in (default, patched):
        assert all(item["threshold_gate"] == {"enabled": False, "thresholds": []} for item in profile["measurements"])


def test_cardano_measurement_files_are_additive_and_portable() -> None:
    root = Path(__file__).resolve().parents[1]
    for measurement_id in ALL:
        path = root / "dwarf" / "measurements" / f"{measurement_id}.yaml"
        assert path.is_file()
        text = path.read_text(encoding="utf-8")
        assert "/home/" not in text
        assert "/Users/" not in text
