import json
from pathlib import Path
from types import SimpleNamespace

from profile_manager.run_plan import RunPlanRequest, _scenario_substrate_version_profile
from profile_manager.scenario import load_scenario


ROOT = Path(__file__).resolve().parents[1]


def test_latest_amaru_value_overflow_scenario_uses_fresh_funded_fail_closed_workload():
    scenario_path = (
        ROOT
        / "dwarf/scenarios/ledger-submit-value-coin-i64-overflow-regression-latest-amaru.yaml"
    )
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))

    assert scenario["target"] == {
        "implementation": "amaru",
        "version": "resolved-plan",
    }
    nodes = {node["id"]: node for node in scenario["substrate"]["nodes"]}
    assert nodes["amaru1"]["identity_binding"] == "resolved-plan"
    assert nodes["producer1"]["supporting"] is True
    workloads = [
        step
        for step in scenario["load"]
        if step["primitive"] == "runtime_value_coin_overflow_regression"
    ]
    assert workloads == [
        {
            "primitive": "runtime_value_coin_overflow_regression",
            "runtime_metadata_path": "outputs/substrate-compose/runtime.json",
            "target_node": "amaru1",
            "reference_node": "producer1",
            "output_dir": "outputs/value-coin-overflow",
        }
    ]
    assert any(
        item["primitive"] == "value_coin_overflow_regression_passed"
        and item["result_path"] == "outputs/value-coin-overflow/result.json"
        for item in scenario["assertions"]
    )

    loaded = load_scenario(scenario_path)
    loaded_nodes = {node["id"]: node for node in loaded.substrate["nodes"]}
    assert loaded_nodes["producer1"]["supporting"] is True
    profile = _scenario_substrate_version_profile(
        loaded, RunPlanRequest(scenario_id=loaded.id)
    )
    assert profile["amaru_count"] == 1
    assert profile["haskell_count"] == 0


def test_value_overflow_primitive_schema_requires_all_runtime_inputs():
    registry = json.loads((ROOT / "dwarf/primitives/registry.json").read_text())
    entry = registry["primitives"]["runtime_value_coin_overflow_regression"]
    assert entry["params_schema"] == "primitives/load/runtime_value_coin_overflow.schema.json"
    schema = json.loads((ROOT / "dwarf" / entry["params_schema"]).read_text())
    assert set(schema["required"]) == {
        "runtime_metadata_path",
        "target_node",
        "reference_node",
        "output_dir",
    }
