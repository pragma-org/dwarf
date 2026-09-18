import json
import subprocess
from pathlib import Path

from profile_manager.primitives import RuntimeAmaruMeasurementCalibration
from profile_manager.scenario import scenario_from_body


ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "dwarf" / "scenarios"
PROFILES = ROOT / "dwarf" / "profiles"


def _document(name):
    return json.loads((SCENARIOS / f"{name}.yaml").read_text(encoding="utf-8"))


def test_calibration_profiles_are_additive_exact_and_use_distinct_runtime_identities():
    stock = json.loads(
        (PROFILES / "profile-r-amaru-measurement-stock-control" / "profile.yaml").read_text()
    )
    patched = json.loads(
        (PROFILES / "profile-q-amaru-measurement-patched" / "profile.yaml").read_text()
    )

    assert stock["version_policy"] == patched["version_policy"] == "exact"
    assert stock["amaru_version"] == patched["amaru_version"] == "10.11.20260912"
    assert stock["measurement_target_mode"] == "stock"
    assert patched["measurement_target_mode"] == "patched"
    assert stock["amaru_json_traces"] is True
    assert patched["amaru_json_traces"] is True
    assert patched["measurement_patch_revision"] == "b159172f25a9c389f82f20bca4f15e3032791638"
    assert len(patched["measurement_patch_set_sha256"]) == 64
    assert stock["id"] != patched["id"]


def test_calibration_scenarios_parse_and_have_identical_fixed_workloads():
    stock = _document("amaru-measurement-overhead-calibration-stock")
    patched = _document("amaru-measurement-overhead-calibration-patched")

    for document in (stock, patched):
        parsed = scenario_from_body((json.dumps(document) + "\n").encode())
        assert parsed.seed == "0xA11CE501"
        assert parsed.runtime == "devnet"

    assert stock["seed"] == patched["seed"]
    stock_load = {**stock["load"][0], "runtime_root": "<paired>"}
    patched_load = {**patched["load"][0], "runtime_root": "<paired>"}
    assert stock_load == patched_load
    assert stock_load["primitive"] == "runtime_amaru_measurement_calibration"
    assert stock_load["attempts"] == 40
    assert stock["profile"] != patched["profile"]
    assert stock["measurement_profile"] == "amaru-security-default"
    assert patched["measurement_profile"] == "amaru-security-patched"
    assert stock["assertions"] == patched["assertions"]


def test_calibration_helper_default_is_installed_source_relative():
    helper = Path(RuntimeAmaruMeasurementCalibration._DEFAULT_HELPER)

    assert helper == ROOT / "dwarf" / "scripts" / "runtime_amaru_measurement_calibration.py"
    assert helper.is_file()


def test_calibration_primitive_runs_retained_leg_with_exact_attempt_count(
    tmp_path, monkeypatch
):
    calls = []

    class Handle:
        run_dir = tmp_path

        def __init__(self):
            self.events = []

        def log(self, **event):
            self.events.append(event)

    def run(command, **kwargs):
        calls.append((command, kwargs))
        output_dir = Path(command[command.index("--output-dir") + 1])
        (output_dir / "result.json").write_text(
            json.dumps({"attempts": {"total": 40}}), encoding="utf-8"
        )
        return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    handle = Handle()
    primitive = RuntimeAmaruMeasurementCalibration(
        params={"runtime_root": "/runtime", "attempts": 40}
    )

    primitive.run(handle, None)

    command, kwargs = calls[0]
    assert command[2:4] == ["leg", "--runtime-root"]
    assert command[command.index("--attempts") + 1] == "40"
    assert kwargs["cwd"] == ROOT / "dwarf"
    assert handle.events[-1]["payload"]["outcome"] == "ok"
    assert handle.events[-1]["payload"]["report"]["attempts"]["total"] == 40
