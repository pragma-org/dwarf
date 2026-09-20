from pathlib import Path

from profile_manager.scenario import semantic_validate_scenario
from profile_manager.primitives import (
    build_runtime_version_pinned_cbor_conformance_command,
    load_registry,
)


ROOT = Path(__file__).resolve().parents[1]


def _handle(*, clean=True, roundtrip=True):
    class Handle:
        events = [
            {
                "phase": "load",
                "primitive": "runtime_version_pinned_cbor_conformance",
                "event": "completed",
                "payload": {
                    "outcome": "ok",
                    "artifact_summary": {
                        "has_result_json": True,
                        "has_inputs_ndjson": True,
                        "has_report_markdown": True,
                    },
                    "report": {
                        "input_count": 100,
                        "outcome_mismatches": [] if clean else ["plutus_data/zap-1/example.cbor"],
                        "roundtrip_failures": [] if roundtrip else ["plutus_data/valid/example.cbor"],
                        "checks": {
                            "cbor_conformance_clean": clean,
                            "cbor_roundtrip_consistent": roundtrip,
                        },
                    },
                },
            }
        ]

    return Handle()


def _assertion(name):
    registry = load_registry(ROOT / "dwarf/primitives/registry.json")
    entry = registry[name]
    module = __import__(entry.module, fromlist=[entry.class_name])
    return getattr(module, entry.class_name)(params={"min_inputs_processed": 100}, entry=entry)


def test_command_and_registry_are_wired():
    command = build_runtime_version_pinned_cbor_conformance_command(
        config_path=Path("/run/config.json")
    )
    assert command[-2:] == ["--config", "/run/config.json"]
    registry = load_registry(ROOT / "dwarf/primitives/registry.json")
    assert registry["runtime_version_pinned_cbor_conformance"].family == "load"
    assert registry["cbor_conformance_clean"].family == "assertion"
    assert registry["cbor_roundtrip_consistent"].family == "assertion"


def test_conformance_assertion_fails_closed_on_expected_outcome_mismatch():
    assertion = _assertion("cbor_conformance_clean")
    assert assertion.evaluate(_handle(clean=True))["result"] == "pass"
    failed = assertion.evaluate(_handle(clean=False))
    assert failed["result"] == "fail"
    assert failed["evaluated_value"]["non_ok"] == 1


def test_roundtrip_assertion_fails_closed_on_unstable_second_encode():
    assertion = _assertion("cbor_roundtrip_consistent")
    assert assertion.evaluate(_handle(roundtrip=True))["result"] == "pass"
    assert assertion.evaluate(_handle(roundtrip=False))["result"] == "fail"



def test_frozen_card_01_scenarios_are_semantically_valid():
    expected = {
        "amaru": (
            "client-example-cbor-decoding-amaru-patched.yaml",
            "profile-u-amaru-measurement-nanoseconds-v2",
            "b159172f25a9c389f82f20bca4f15e3032791638",
        ),
        "cardano-node": (
            "client-example-cbor-decoding-cardano-patched.yaml",
            "profile-v-cardano-measurement-nanoseconds-v2",
            "fef83fed01d7926f3de83b3b917be5a4a48768b5",
        ),
    }
    for implementation, (filename, profile, revision) in expected.items():
        path = ROOT / "dwarf/scenarios" / filename
        result = semantic_validate_scenario(
            path, registry_path=ROOT / "dwarf/primitives/registry.json"
        )
        assert result["errors"] == []
        import json
        body = json.loads(path.read_text())
        assert body["profile"] == profile
        assert body["target"]["implementation"] == implementation
        assert body["setup"][0]["source_revision"] == revision
        assert body["load"][0]["primitive"] == "runtime_version_pinned_cbor_conformance"
        assert body["load"][0]["dataset_revision"] == "a7561cd063550c2218898571520f14c3674efe91"
        assert body["load"][0]["source_revision"] == revision
        assert body["load"][1]["primitive"] == "runtime_protocol_decode_cases"
        assert [row["primitive"] for row in body["assertions"]] == [
            "cbor_conformance_clean",
            "cbor_roundtrip_consistent",
            "invalid_protocol_cases_contained",
            "target_progress_continues",
        ]
