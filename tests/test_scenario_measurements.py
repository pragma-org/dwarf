import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from profile_manager.scenario import ScenarioValidationError, scenario_from_body


def _scenario(**updates):
    document = {
        "spec_version": "v1",
        "id": "amaru-measurement-demo",
        "title": "Amaru measurement demo",
        "target": {"implementation": "amaru", "version": "10.11.20260912"},
        "runtime": "library",
        "setup": [],
        "load": [],
        "faults": [],
        "probes": [],
        "assertions": [],
        "teardown": [],
    }
    document.update(updates)
    return document


def _parse(document):
    return scenario_from_body((json.dumps(document) + "\n").encode())


def test_old_scenario_contract_remains_valid_without_measurement_fields():
    scenario = _parse(_scenario())

    assert scenario.measurement_profile is None
    assert scenario.measurements == []


def test_scenario_retains_profile_and_individual_measurement_overrides():
    scenario = _parse(
        _scenario(
            measurement_profile="amaru-security-default",
            measurements=[
                {"id": "amaru-stock-mempool", "enabled": False},
                {
                    "id": "amaru-external-workload-accounting",
                    "enabled": True,
                    "parameters": {"window_seconds": 5, "retain_input_ids": True},
                    "threshold_gate": {
                        "enabled": True,
                        "thresholds": [
                            {"metric": "successful_submissions_per_second", "operator": "gte", "value": 1, "unit": "tx/s"}
                        ],
                    },
                },
            ],
        )
    )

    assert scenario.measurement_profile == "amaru-security-default"
    assert [selection.id for selection in scenario.measurements] == [
        "amaru-stock-mempool",
        "amaru-external-workload-accounting",
    ]
    assert scenario.measurements[0].enabled is False
    assert scenario.measurements[1].parameters == {
        "window_seconds": 5,
        "retain_input_ids": True,
    }
    assert scenario.measurements[1].threshold_gate["enabled"] is True


def test_scenario_allows_explicit_none_profile_to_disable_default_measurements():
    scenario = _parse(_scenario(measurement_profile="none", measurements=[]))

    assert scenario.measurement_profile == "none"
    assert scenario.measurements == []


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"measurement_profile": "missing-profile"}, "unknown measurement_profile"),
        ({"measurements": [{"id": "missing-measurement", "enabled": True}]}, "unknown measurement"),
        (
            {"measurements": [
                {"id": "amaru-stock-mempool", "enabled": True},
                {"id": "amaru-stock-mempool", "enabled": False},
            ]},
            "duplicate measurement",
        ),
        (
            {"measurements": [{
                "id": "amaru-stock-mempool",
                "enabled": True,
                "parameters": {"payload": "x" * 1025},
            }]},
            "bounded scalar",
        ),
        (
            {"measurements": [{
                "id": "amaru-stock-mempool",
                "enabled": True,
                "threshold_gate": {"enabled": True, "thresholds": []},
            }]},
            "at least one threshold",
        ),
    ],
)
def test_scenario_rejects_invalid_measurement_selection(updates, message):
    with pytest.raises(ScenarioValidationError, match=message):
        _parse(_scenario(**updates))


def test_scenario_json_schema_documents_measurement_selection():
    schema = json.loads(Path("dwarf/spec/v1/schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)

    assert schema["properties"]["measurement_profile"]["type"] == ["string", "null"]
    selection = schema["properties"]["measurements"]["items"]
    assert selection["required"] == ["id", "enabled"]
    assert selection["properties"]["parameters"]["maxProperties"] == 32
    assert selection["properties"]["threshold_gate"]["properties"]["enabled"]["default"] is False
