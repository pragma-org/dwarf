import json
import subprocess
from pathlib import Path

from profile_manager.primitives import RuntimeCardanoMeasurementCalibration
from profile_manager.scenario import scenario_from_body


ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "dwarf" / "scenarios"


def _document(name):
    return json.loads((SCENARIOS / f"{name}.yaml").read_text(encoding="utf-8"))


def test_cardano_calibration_scenarios_are_paired_real_node_workloads():
    stock = _document("cardano-measurement-overhead-calibration-stock")
    patched = _document("cardano-measurement-overhead-calibration-patched")
    for document in (stock, patched):
        parsed = scenario_from_body((json.dumps(document) + "\n").encode())
        assert parsed.target == {"implementation": "cardano-node", "version": "11.1.2"}
        load = document["load"][0]
        assert load["attempts"] >= 30
        assert load["plutus_transactions"] >= 2
        assert load["epoch_observation_seconds"] >= 50
    stock_load = {**stock["load"][0], "profile_id": "<paired>"}
    patched_load = {**patched["load"][0], "profile_id": "<paired>"}
    assert stock_load == patched_load
    assert stock["seed"] == patched["seed"]
    assert stock["measurement_profile"] == "cardano-security-default"
    assert patched["measurement_profile"] == "cardano-security-patched"


def test_cardano_calibration_primitive_passes_node_workload_parameters(tmp_path, monkeypatch):
    calls = []

    class Handle:
        run_dir = tmp_path

        def log(self, **_event):
            pass

    def run(command, **kwargs):
        calls.append(command)
        output_dir = Path(command[command.index("--output-dir") + 1])
        (output_dir / "result.json").write_text(
            json.dumps({"attempts": {"total": 30}}), encoding="utf-8"
        )
        return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    primitive = RuntimeCardanoMeasurementCalibration(params={
        "runtime_root": "/runtime",
        "attempts": 30,
        "plutus_transactions": 2,
        "epoch_observation_seconds": 55,
    })

    primitive.run(Handle(), None)

    command = calls[0]
    assert command[command.index("--plutus-transactions") + 1] == "2"
    assert command[command.index("--epoch-observation-seconds") + 1] == "55.0"


def test_cardano_calibration_command_is_accepted_by_the_cardano_helper(monkeypatch, tmp_path):
    from scripts import runtime_cardano_measurement_calibration as helper

    calls = []

    class Handle:
        run_dir = tmp_path

        def log(self, **_kwargs):
            pass

    def run(command, **_kwargs):
        calls.append(command)
        output_dir = Path(command[command.index("--output-dir") + 1])
        (output_dir / "result.json").write_text(
            json.dumps({"attempts": {"total": 60}}), encoding="utf-8"
        )
        return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    RuntimeCardanoMeasurementCalibration(params={
        "runtime_root": "/runtime",
        "attempts": 60,
        "case_set": "version-table-forward-compat-v1",
    }).run(Handle(), None)

    command = calls[0]
    assert "--progress-timeout-seconds" not in command
    received = {}

    def run_leg(**kwargs):
        received.update(kwargs)
        return {"node_trace": {"record_count": 1, "protocol_record_count": 1}}

    monkeypatch.setattr(helper, "run_leg", run_leg)
    assert helper.main(command[2:]) == 0
    assert received["case_set"] == "version-table-forward-compat-v1"
