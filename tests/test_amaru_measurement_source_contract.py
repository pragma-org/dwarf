import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSION_CATALOG = ROOT / "dwarf" / "versions" / "catalog.json"
AUDIT_ROOT = ROOT / "dwarf" / "measurements" / "audit"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _default_amaru_release() -> dict:
    catalog = _load_json(VERSION_CATALOG)
    defaults = [
        release
        for release in catalog["releases"]
        if release["implementation"] == "amaru"
        and release.get("channel") == "stable"
        and (release.get("verification") or {}).get("amaru-only", {}).get("default") is True
    ]
    assert len(defaults) == 1, "the version catalog must have exactly one stable Amaru-only default"
    return defaults[0]


def _audit() -> tuple[dict, dict]:
    release = _default_amaru_release()
    audit_path = AUDIT_ROOT / f"amaru-{release['version']}.json"
    assert audit_path.is_file(), f"missing source audit for the confirmed Amaru default: {audit_path}"
    return release, _load_json(audit_path)


def test_audit_identity_matches_the_confirmed_default_release() -> None:
    release, audit = _audit()
    artifact = next(item for item in release["artifacts"] if item["kind"] == "oci")

    assert audit["schema_version"] == "v1"
    assert audit["implementation"] == "amaru"
    assert audit["release"] == release["version"]
    assert audit["source_revision"] == release["source_revision"]
    assert audit["image"]["reference"] == artifact["reference"]
    assert audit["image"]["digest"] == artifact["digest"]
    assert audit["qualification"]["scope"] == "amaru-only"
    assert audit["qualification"]["status"] == "confirmed"
    assert audit["qualification"]["default"] is True
    assert audit["qualification"]["evidence"] == release["verification"]["amaru-only"]["evidence"]
    assert audit["dwarf_baseline"]["commit"] == "25e1ad0867d8ebac8628707c73b388a5654efa21"


def test_audit_records_stock_signals_with_exact_source_anchors() -> None:
    _release, audit = _audit()
    signals = {item["id"]: item for item in audit["stock_signals"]}
    required = {
        "header-lifecycle",
        "fork-switch",
        "mempool",
        "ledger-rule-stages",
        "transaction-validation",
        "plutus-execution",
        "block-epoch",
        "chain-tip",
        "networking",
        "process-resources",
        "otel-json-export",
    }
    assert required <= set(signals)
    for signal_id in required:
        signal = signals[signal_id]
        assert signal["source_paths"], signal_id
        assert all(path.startswith(("crates/", "monitoring/", "docs/")) for path in signal["source_paths"])
        assert signal["available"] is True
        assert signal["claim"]
        assert signal["fields"]


def test_patch_candidates_are_explicit_gaps_not_duplicate_stock_telemetry() -> None:
    _release, audit = _audit()
    stock_ids = {item["id"] for item in audit["stock_signals"]}
    gaps = {item["id"]: item for item in audit["visibility_gaps"]}
    assert {
        "live-protocol-cbor-decode",
        "blockfetch-handler-queue",
        "txsubmission-inflight-residence",
    } <= set(gaps)
    for gap_id, gap in gaps.items():
        assert gap_id not in stock_ids
        assert gap["missing_boundary"]
        assert gap["patch_mode"] == "patched-node"
        assert gap["source_revision"] == audit["source_revision"]
        assert gap["stock_overlap_reviewed"]
        assert gap["patch_required"] is True
    assert audit["deferred_patch_candidates"][0]["id"] == "scheduler-mailbox-lock-attribution"
    assert audit["deferred_patch_candidates"][0]["patch_required"] is False


def test_audit_preserves_cddl_dataset_coverage_and_claim_boundaries() -> None:
    _release, audit = _audit()
    inputs = audit["input_and_coverage_boundaries"]
    assert inputs["cuddle_role"] == "structurally-valid-seed-and-scaffolding-supplier"
    assert inputs["cuddle_is_runtime_fuzzer"] is False
    assert inputs["cardano_cbor_dataset"]["revision"] == "a7561cd063550c2218898571520f14c3674efe91"
    assert inputs["cardano_cbor_dataset"]["qualified_surfaces"] == ["conway/plutus_data"]
    assert inputs["compiler_coverage_is_performance_authority"] is False
    assert inputs["compiler_coverage_is_correctness"] is False
    assert audit["claim_boundaries"]["library_execution_is_live_node"] is False
    assert audit["claim_boundaries"]["submission_success_is_chain_adoption"] is False
    assert audit["claim_boundaries"]["patched_only_behavior_is_vulnerability"] is False
