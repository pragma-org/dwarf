import json
from pathlib import Path

from profile_manager.scenario import scenario_from_body


ROOT = Path(__file__).resolve().parents[1]
SCENARIO_PATH = ROOT / "dwarf" / "scenarios" / "amaru-measurement-e2e-stock.yaml"
SCHEMA_PATH = (
    ROOT
    / "dwarf"
    / "primitives"
    / "load"
    / "runtime_amaru_measurement_calibration.schema.json"
)


def _scenario_document():
    return json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))


def test_amaru_measurement_e2e_scenario_is_exact_additive_and_portable():
    document = _scenario_document()
    parsed = scenario_from_body((json.dumps(document) + "\n").encode())

    assert parsed.id == "amaru-measurement-e2e-stock"
    assert parsed.runtime == "devnet"
    assert parsed.target == {
        "implementation": "amaru",
        "version": "10.11.20260912",
    }
    assert parsed.profile == "profile-r-amaru-measurement-stock-control"
    assert parsed.measurement_profile == "amaru-security-default"
    assert parsed.seed == "0xA11CE501"
    assert parsed.setup == []
    assert parsed.teardown == []
    assert "fresh-profile-deploy-required" in document["tags"]
    assert any(
        "freshly deployed" in blocker.lower()
        for blocker in document["promotion_blockers"]
    )
    assert "/home/" not in SCENARIO_PATH.read_text(encoding="utf-8")
    assert "/Users/" not in SCENARIO_PATH.read_text(encoding="utf-8")
    assert "/opt/dwarf/cardano-profiles" not in SCENARIO_PATH.read_text(
        encoding="utf-8"
    )


def test_amaru_measurement_e2e_uses_profile_resolution_and_non_vacuous_attempts():
    document = _scenario_document()
    workload = document["load"]

    assert len(workload) == 1
    load = workload[0]
    assert load["primitive"] == "runtime_amaru_measurement_calibration"
    assert load["profile_id"] == "profile-r-amaru-measurement-stock-control"
    assert "runtime_root" not in load
    assert load["attempts"] >= 30
    assert load["response_timeout_seconds"] > 0
    assert load["expected_helper_exit"] == 0
    assert document["assertions"] == [
        {"primitive": "load_events_are_ok", "min_completed": 1}
    ]


def test_calibration_schema_accepts_exactly_one_portable_runtime_selector():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["oneOf"] == [
        {"required": ["profile_id"], "not": {"required": ["runtime_root"]}},
        {"required": ["runtime_root"], "not": {"required": ["profile_id"]}},
    ]
    assert schema["properties"]["profile_id"] == {
        "type": "string",
        "pattern": "^[a-z0-9][a-z0-9_-]{0,80}$",
    }
