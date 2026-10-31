import json
from pathlib import Path
from types import SimpleNamespace

import profile_manager.submit_primitives as submit_module
from profile_manager.submit_primitives import RuntimeTxSubmitDifferential, SubmitOutcomeMatches


ROOT = Path(__file__).resolve().parents[1]


def _write_result(run_dir, relative_path, *, target, reference="unknown"):
    path = run_dir / relative_path
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "target": {"outcome": target},
                "reference": {"outcome": reference},
            }
        ),
        encoding="utf-8",
    )


def test_submit_outcome_matches_can_assert_target_only_at_exact_result_path(tmp_path):
    _write_result(
        tmp_path,
        "outputs/boundary-control/result.json",
        target="reject",
    )
    _write_result(
        tmp_path,
        "outputs/security-mutant/result.json",
        target="reject",
    )

    result = SubmitOutcomeMatches(
        params={
            "expected_target_outcome": "reject",
            "result_path": "outputs/security-mutant/result.json",
        }
    ).evaluate(SimpleNamespace(run_dir=tmp_path))

    assert result["result"] == "pass"
    assert result["evaluated_value"] == {
        "expected": {"target": "reject"},
        "observed": {"target": "reject"},
    }


def test_submit_outcome_matches_target_only_fails_closed_when_result_missing(tmp_path):
    result = SubmitOutcomeMatches(
        params={
            "expected_target_outcome": "reject",
            "result_path": "outputs/security-mutant/result.json",
        }
    ).evaluate(SimpleNamespace(run_dir=tmp_path))

    assert result["result"] == "fail"


def test_runtime_submit_applies_auditable_fixed_width_hex_replacement(
    monkeypatch, tmp_path
):
    submitted = []

    def submit(_url, payload):
        submitted.append(payload)
        return {"http": 400, "outcome": "reject", "body": "controlled rejection"}

    monkeypatch.setattr(submit_module, "_submit", submit)
    result = RuntimeTxSubmitDifferential(
        params={
            "tx_hex": "a11b8000000000000000",
            "target_submit_api": "http://127.0.0.1:8090",
            "output_dir": "outputs/control",
            "hex_replacements": [
                {
                    "from": "1b8000000000000000",
                    "to": "1b7fffffffffffffff",
                }
            ],
        }
    ).run(SimpleNamespace(run_dir=tmp_path), None)

    assert submitted == [bytes.fromhex("a11b7fffffffffffffff")]
    assert result["payload_mutations"] == [
        {"from": "1b8000000000000000", "to": "1b7fffffffffffffff"}
    ]
    assert result["tx_source"] == "inline-hex"


def test_latest_amaru_value_overflow_scenario_has_boundary_control_and_support_node():
    scenario = json.loads(
        (
            ROOT
            / "dwarf/scenarios/ledger-submit-value-coin-i64-overflow-regression-latest-amaru.yaml"
        ).read_text(encoding="utf-8")
    )

    assert scenario["target"] == {
        "implementation": "amaru",
        "version": "resolved-plan",
    }
    nodes = {node["id"]: node for node in scenario["substrate"]["nodes"]}
    assert nodes["amaru1"]["identity_binding"] == "resolved-plan"
    assert nodes["producer1"]["supporting"] is True
    submits = [
        step
        for step in scenario["load"]
        if step["primitive"] == "runtime_tx_submit_differential"
    ]
    assert all("tx_file" not in step for step in submits)
    assert len({step["tx_hex"] for step in submits}) == 1
    assert [step["output_dir"] for step in submits] == [
        "outputs/i64-max-control",
        "outputs/i64-overflow-mutant",
    ]
    assert submits[0]["hex_replacements"] == [
        {"from": "1b8000000000000000", "to": "1b7fffffffffffffff"}
    ]
    assert "hex_replacements" not in submits[1]
    assertions = {
        item.get("result_path"): item
        for item in scenario["assertions"]
        if item["primitive"] == "submit_outcome_matches"
    }
    assert set(assertions) == {
        "outputs/i64-max-control/result.json",
        "outputs/i64-overflow-mutant/result.json",
    }
    assert all(item["expected_target_outcome"] == "reject" for item in assertions.values())
