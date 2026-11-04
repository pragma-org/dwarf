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
    # The live devnet forges probabilistically.  Keep the calibration window
    # long enough to observe honest node progress as well as hostile attempts.
    assert load["attempts"] >= 100
    assert load["response_timeout_seconds"] > 0
    assert load["expected_helper_exit"] == 0
    assert document["assertions"] == [
        {
            "primitive": "load_events_are_ok",
            "min_completed": 1,
            "min_event_count": 1,
        }
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


def test_calibration_schema_accepts_workload_options_and_rejects_unknown():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    props = schema["properties"]

    assert props["plutus_transactions"] == {"type": "integer", "minimum": 0, "maximum": 100}
    assert props["submission_entry"] == {"type": "string", "enum": ["p1", "amaru-consumer"]}
    assert props["inclusion_fallback_seconds"] == {"type": "number", "exclusiveMinimum": 0}
    assert props["epoch_transitions"] == {"type": "integer", "minimum": 0, "maximum": 5}
    assert props["epoch_timeout_seconds"] == {"type": "number", "exclusiveMinimum": 0, "maximum": 900}
    assert schema["additionalProperties"] is False


def test_amaru_calibration_primitive_passes_workload_flags_and_merges_plutus_accounting(
    monkeypatch, tmp_path
):
    import subprocess

    from profile_manager.primitives import RuntimeAmaruMeasurementCalibration

    commands = []

    class Handle:
        run_dir = tmp_path
        _seed = "0xA11CE501"
        events = []

        def log(self, **event):
            self.events.append(event)

    def run(command, **kwargs):
        commands.append(command)
        output_dir = Path(command[command.index("--output-dir") + 1])
        output_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "attempt_id": "plutus-0000", "outcome": "accepted",
            "spend_transaction_id": "ab" * 32, "transaction_bytes": 400,
            "elapsed_nanos": 2_000_000, "elapsed_micros": 2000.0,
            "submit_to_block_inclusion_nanos": 2_000_000, "submit_to_block_inclusion_micros": 2000.0,
        }
        rejected = dict(record, attempt_id="plutus-0001", outcome="rejected", spend_transaction_id="cd" * 32)
        (output_dir / "result.json").write_text(json.dumps({
            "attempts": {"total": 1},
            "plutus_workload": {"records": [record, rejected]},
        }))
        (output_dir / "attempts.ndjson").write_text(json.dumps({
            "attempt_id": "handshake-0000", "outcome": "accepted", "elapsed_micros": 5, "request_length": 12,
        }) + "\n")
        return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    handle = Handle()
    RuntimeAmaruMeasurementCalibration(params={
        "runtime_root": "/runtime", "attempts": 30,
        "plutus_transactions": 2, "epoch_transitions": 1,
    }).run(handle, None)

    command = commands[0]
    assert command[command.index("--plutus-transactions") + 1] == "2"
    assert command[command.index("--epoch-transitions") + 1] == "1"
    assert "--submission-entry" not in command
    assert "--epoch-timeout-seconds" not in command
    started = next(event for event in handle.events if event["event"] == "started")
    assert started["payload"]["plutus_transactions"] == 2
    assert started["payload"]["epoch_transitions"] == 1
    accounting = next(event for event in handle.events if event["event"] == "workload_accounting")
    payload = accounting["payload"]
    assert payload["attempted"] == 3
    assert payload["successful"] == 2
    assert payload["rejected"] == 1
    assert payload["bytes"] == 12 + 800
    assert [row["input_id"] for row in payload["attempts"]] == ["handshake-0000", "plutus-0000", "plutus-0001"]
    assert payload["attempts"][1]["submit_to_block_inclusion_micros"] == 2000.0


def test_amaru_calibration_primitive_without_workload_options_keeps_legacy_command(
    monkeypatch, tmp_path
):
    import subprocess

    from profile_manager.primitives import RuntimeAmaruMeasurementCalibration

    commands = []

    class Handle:
        run_dir = tmp_path

        def log(self, **_event):
            pass

    def run(command, **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    RuntimeAmaruMeasurementCalibration(params={"runtime_root": "/runtime", "attempts": 30}).run(Handle(), None)

    assert not any(flag in commands[0] for flag in (
        "--plutus-transactions", "--submission-entry", "--inclusion-fallback-seconds",
        "--epoch-transitions", "--epoch-timeout-seconds",
    ))
