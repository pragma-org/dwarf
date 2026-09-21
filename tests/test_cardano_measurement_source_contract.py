import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSION_CATALOG = ROOT / "dwarf" / "versions" / "catalog.json"
AUDIT_ROOT = ROOT / "dwarf" / "measurements" / "audit"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _default_cardano_release() -> dict:
    catalog = _load_json(VERSION_CATALOG)
    defaults = [
        release
        for release in catalog["releases"]
        if release["implementation"] == "cardano-node"
        and release.get("channel") == "stable"
        and (release.get("verification") or {}).get("cardano-only", {}).get("default")
        is True
    ]
    assert len(defaults) == 1, (
        "the version catalog must have exactly one stable Cardano-only default"
    )
    return defaults[0]


def _audit() -> tuple[dict, dict]:
    release = _default_cardano_release()
    audit_path = AUDIT_ROOT / f"cardano-node-{release['version']}.json"
    assert audit_path.is_file(), (
        f"missing source audit for the confirmed Cardano-only default: {audit_path}"
    )
    return release, _load_json(audit_path)


def test_audit_identity_matches_the_confirmed_cardano_default() -> None:
    release, audit = _audit()
    artifact = next(item for item in release["artifacts"] if item["kind"] == "oci")

    assert audit["schema_version"] == "v1"
    assert audit["implementation"] == "cardano-node"
    assert audit["release"] == release["version"]
    assert audit["source_revision"] == release["source_revision"]
    assert audit["image"]["reference"] == artifact["reference"]
    assert audit["image"]["digest"] == artifact["digest"]
    assert audit["qualification"]["scope"] == "cardano-only"
    assert audit["qualification"]["status"] == "confirmed"
    assert audit["qualification"]["default"] is True
    assert audit["qualification"]["evidence"] == release["verification"]["cardano-only"]["evidence"]
    assert audit["dwarf_baseline"]["commit"] == (
        "19def2885a43c58e6dc11c37c4e0cdd1143c4d9a"
    )


def test_stock_signals_name_exact_source_constructors_fields_and_units() -> None:
    _release, audit = _audit()
    signals = {item["id"]: item for item in audit["stock_signals"]}
    required = {
        "chainsync",
        "blockfetch",
        "txsubmission",
        "mempool",
        "keepalive",
        "chaindb-selection-adoption",
        "ledger-block-epoch",
        "plutus-execution",
        "process-rts-resources",
        "new-tracing-prometheus-export",
    }
    assert required <= set(signals)
    for signal_id in required:
        signal = signals[signal_id]
        assert signal["source_paths"], signal_id
        assert all(not Path(path).is_absolute() for path in signal["source_paths"])
        assert signal["constructors_or_metrics"], signal_id
        assert signal["fields"], signal_id
        assert signal["units"], signal_id
        assert signal["claim"], signal_id
        assert signal["available"] in {True, "partial"}


def test_stock_config_and_metric_ownership_are_explicit() -> None:
    _release, audit = _audit()
    export = audit["export_contract"]
    assert export["legacy_tracing_removed"] is True
    assert export["new_tracing"] is True
    assert export["prometheus_simple"] is True
    assert export["source_paths"]

    ownership = audit["metric_ownership"]
    assert ownership["node_process"]
    assert ownership["container_or_network_namespace"]
    assert ownership["dwarf_controller"]
    assert ownership["network_namespace_is_per_process"] is False


def test_patch_candidates_are_source_backed_gaps_not_stock_duplicates() -> None:
    _release, audit = _audit()
    stock_ids = {item["id"] for item in audit["stock_signals"]}
    gaps = {item["id"]: item for item in audit["visibility_gaps"]}
    assert {
        "protocol-decode-duration",
        "blockfetch-handler-queue-residence",
        "txsubmission-inflight-residence",
        "ledger-plutus-stage-boundaries",
    } <= set(gaps)
    for gap_id, gap in gaps.items():
        assert gap_id not in stock_ids
        assert gap["missing_boundary"]
        assert gap["source_paths"]
        assert gap["patch_mode"] == "patched-node"
        assert gap["source_revision"] == audit["source_revision"]
        assert gap["stock_overlap_reviewed"]
        assert gap["patch_required"] is True


def test_attempt_coverage_and_claim_boundaries_are_explicit() -> None:
    _release, audit = _audit()
    timing = audit["attempt_timing_contract"]
    assert timing["outcome_inclusion"] == "all"
    assert {
        "accepted",
        "rejected",
        "invalid",
        "duplicate",
        "timeout",
        "disconnected",
        "unclassified",
    } <= set(timing["terminal_outcomes"])
    assert timing["missing_boundary_value"] == "unavailable"

    coverage = audit["coverage_boundary"]
    assert coverage["source_revision"] == audit["source_revision"]
    assert coverage["existing_dwarf_harnesses"]
    assert coverage["performance_authority"] is False
    assert coverage["correctness_authority"] is False

    claims = audit["claim_boundaries"]
    assert claims["submission_success_is_chain_adoption"] is False
    assert claims["compiler_coverage_is_correctness"] is False
    assert claims["patched_only_behavior_is_vulnerability"] is False
    assert claims["cardano_only_proves_mixed_parity"] is False
    assert claims["unavailable_internal_visibility_may_be_inferred"] is False
