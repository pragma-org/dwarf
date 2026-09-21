import os
from pathlib import Path

from profile_manager.scenario import semantic_validate_scenario
from profile_manager.primitives import (
    _build_dwarf_telemetry_env,
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

def test_child_script_environment_uses_absolute_dwarf_pythonpath(monkeypatch, tmp_path):
    class Handle:
        run_dir = tmp_path / "run"

    monkeypatch.setenv("PYTHONPATH", "dwarf")

    env = _build_dwarf_telemetry_env(Handle())

    assert env["PYTHONPATH"].split(os.pathsep)[0] == str(ROOT / "dwarf")


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
        assert body["probes"][0]["progress_reference"] == "load-start"
        assert [row["primitive"] for row in body["assertions"]] == [
            "cbor_conformance_clean",
            "cbor_roundtrip_consistent",
            "invalid_protocol_cases_contained",
            "target_progress_continues",
        ]


def test_fixed_amaru_regression_scenario_links_old_failure_and_upstream_fix():
    path = ROOT / "dwarf/scenarios/client-example-cbor-decoding-amaru-d3a6dafc-regression.yaml"
    result = semantic_validate_scenario(
        path, registry_path=ROOT / "dwarf/primitives/registry.json"
    )

    assert result["errors"] == []
    import json
    body = json.loads(path.read_text())
    assert body["profile"] == "profile-x-amaru-cbor-fix-regression-nanoseconds-v2"
    assert body["target"] == {
        "implementation": "amaru",
        "version": "v10.11.20260912-30-gd3a6dafc",
        "source_revision": "d3a6dafcced78f5809a96619e883cf04911d2bdc",
    }
    assert body["m1_trace"]["finding_ids"] == ["amaru-plutus-data-byte-string-bound"]
    assert body["m1_trace"]["historical_run_ids"] == ["20260920T135054Z-28289dcd"]
    assert body["m1_trace"]["upstream_fix_revisions"] == [
        "d3a6dafcced78f5809a96619e883cf04911d2bdc"
    ]
    assert body["load"][0]["adapter_manifest_sha256"] == (
        "8877a4ae7f46521ae1a8bf6584e4092b39ef3f0f8acf93bbcc4e0bd551f1f685"
    )
    assert body["load"][0]["adapter_manifest"] == (
        "targets/amaru/conformance-adapters/"
        "d3a6dafcced78f5809a96619e883cf04911d2bdc/manifest.json"
    )
    assert body["load"][0]["dataset_revision"] == (
        "a7561cd063550c2218898571520f14c3674efe91"
    )
    assert [row["primitive"] for row in body["assertions"]] == [
        "cbor_conformance_clean",
        "cbor_roundtrip_consistent",
        "invalid_protocol_cases_contained",
        "target_progress_continues",
    ]
