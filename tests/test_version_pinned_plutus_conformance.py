import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from scripts import runtime_version_pinned_plutus_conformance as subject
from profile_manager import primitives as primitives_module


AMARU_REVISION = "b159172f25a9c389f82f20bca4f15e3032791638"
CARDANO_REVISION = "fef83fed01d7926f3de83b3b917be5a4a48768b5"


def _result(implementation, outcome, *, cpu=1000, memory=200, nanos=2184):
    return {
        "schema_version": "v1",
        "implementation": implementation,
        "source_revision": AMARU_REVISION if implementation == "amaru" else CARDANO_REVISION,
        "boundary": "production-plutus-v2-vm-only",
        "plutus_version": "v2",
        "cost_model_sha256": subject.QUALIFIED_COST_MODEL_SHA256,
        "outcome": outcome,
        "cpu_budget": cpu,
        "memory_budget": memory,
        "elapsed_nanos": nanos,
        "elapsed_micros": nanos // 1000,
    }


def test_result_keeps_raw_nanoseconds_and_fractional_microseconds():
    result = subject._validate_result(
        _result("amaru", "accepted"),
        implementation="amaru",
        source_revision=AMARU_REVISION,
        expected_outcome="accepted",
        cost_model_sha256=subject.QUALIFIED_COST_MODEL_SHA256,
    )
    assert result["elapsed_nanos"] == 2184
    assert result["elapsed_micros"] == 2
    assert result["duration_micros"] == 2.184
    assert result["cpu_budget"] == 1000
    assert result["memory_budget"] == 200


def test_result_rejects_wrong_model_and_inconsistent_legacy_microseconds():
    with pytest.raises(subject.PlutusContractError, match="cost_model_sha256"):
        subject._validate_result(
            {**_result("amaru", "accepted"), "cost_model_sha256": "0" * 64},
            implementation="amaru", source_revision=AMARU_REVISION,
            expected_outcome="accepted", cost_model_sha256=subject.QUALIFIED_COST_MODEL_SHA256,
        )
    with pytest.raises(subject.PlutusContractError, match="elapsed_micros"):
        subject._validate_result(
            {**_result("amaru", "accepted"), "elapsed_micros": 3},
            implementation="amaru", source_revision=AMARU_REVISION,
            expected_outcome="accepted", cost_model_sha256=subject.QUALIFIED_COST_MODEL_SHA256,
        )


def _fixture(monkeypatch, tmp_path):
    scripts = {}
    for name, payload in (("always-succeeds-v2.plutus", b"success"), ("always-fails-v2.plutus", b"failure")):
        path = tmp_path / name
        path.write_bytes(payload)
        scripts[name] = {"path": path, "sha256": hashlib.sha256(payload).hexdigest()}
    model = tmp_path / "cost-model.json"
    model.write_text("[1,2,3]\n")
    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    monkeypatch.setattr(subject, "QUALIFIED_COST_MODEL_SHA256", digest)
    monkeypatch.setattr(subject, "QUALIFIED_SCRIPTS", scripts)
    monkeypatch.setattr(subject, "load_adapter_record", lambda _path, *, implementation, source_revision: {
        "implementation": implementation, "source_revision": source_revision,
        "kind": "production-plutus-v2-conformance",
        "measurement_boundary": "production-plutus-v2-vm-only",
        "executable": f"/{implementation}/adapter",
        "executable_sha256": "a" * 64, "build_result_sha256": "b" * 64,
    })
    return model


def test_runner_executes_both_scripts_thirty_times_and_compares_budgets(monkeypatch, tmp_path):
    model = _fixture(monkeypatch, tmp_path)
    calls = []
    def adapter(executable, request, _timeout):
        implementation = "amaru" if executable.startswith("/amaru/") else "cardano-node"
        calls.append((implementation, request["script_name"]))
        return _result(implementation, request["expected_outcome"])

    report_path = subject.run_plutus_conformance({
        "cost_model": model, "output_dir": tmp_path / "out", "executions_per_script": 30,
        "adapters": {
            "amaru": {"record": tmp_path / "amaru.json", "source_revision": AMARU_REVISION},
            "cardano-node": {"record": tmp_path / "cardano.json", "source_revision": CARDANO_REVISION},
        },
    }, adapter_runner=adapter)
    report = json.loads(report_path.read_text())
    assert len(calls) == 120
    assert report["checks"] == {"plutus_result_and_budget_match": True}
    assert report["distributions"]["amaru"]["accepted"]["sample_count"] == 30
    assert report["distributions"]["cardano-node"]["rejected"]["p50_micros"] == 2.184
    assert report["records"][0]["elapsed_nanos"] == 2184
    assert len((tmp_path / "out" / "raw-evaluations.ndjson").read_text().splitlines()) == 120
    assert "Raw nanoseconds are retained" in (tmp_path / "out" / "report.md").read_text()


def test_runner_fails_security_check_on_budget_difference(monkeypatch, tmp_path):
    model = _fixture(monkeypatch, tmp_path)
    def adapter(executable, request, _timeout):
        implementation = "amaru" if executable.startswith("/amaru/") else "cardano-node"
        return _result(implementation, request["expected_outcome"], cpu=1001 if implementation == "amaru" else 1000)

    report_path = subject.run_plutus_conformance({
        "cost_model": model, "output_dir": tmp_path / "out", "executions_per_script": 1,
        "adapters": {
            "amaru": {"record": tmp_path / "amaru.json", "source_revision": AMARU_REVISION},
            "cardano-node": {"record": tmp_path / "cardano.json", "source_revision": CARDANO_REVISION},
        },
    }, adapter_runner=adapter)
    report = json.loads(report_path.read_text())
    assert report["checks"]["plutus_result_and_budget_match"] is False
    assert len(report["mismatches"]) == 2


def test_controller_primitive_re_resolves_frozen_adapter_provenance(
    monkeypatch, tmp_path
):
    expected = {
        "implementation": "amaru",
        "source_revision": AMARU_REVISION,
        "adapter_set_sha256": "a" * 64,
        "executable_sha256": "b" * 64,
        "build_result_sha256": "c" * 64,
        "build_log_sha256": "d" * 64,
    }
    monkeypatch.setattr(
        primitives_module,
        "resolve_plutus_conformance_adapter",
        lambda **_kwargs: {
            **expected,
            "build_result_sha256": "e" * 64,
            "record_path": str(tmp_path / "record.json"),
        },
    )
    primitive = primitives_module.RuntimeVersionPinnedPlutusConformance(
        params={
            "output_dir": "outputs/plutus",
            "cost_model": "corpora/cardano-measurement/plutus-v2-cost-model-protocol-v10.json",
            "adapters": {
                "amaru": {
                    "source_revision": AMARU_REVISION,
                    "adapter_set_sha256": "a" * 64,
                    "expected_provenance": expected,
                }
            },
        }
    )
    handle = type("Handle", (), {"run_dir": tmp_path})()

    with pytest.raises(ValueError, match="provenance changed"):
        primitive.run(handle, None)


def test_controller_primitive_executes_digest_verified_run_private_snapshot(
    monkeypatch, tmp_path
):
    executable = tmp_path / "registry-adapter"
    executable.write_bytes(
        b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 10 + b"\x3e\x00" + b"adapter"
    )
    executable.chmod(0o755)
    expected = {
        "implementation": "amaru",
        "source_revision": AMARU_REVISION,
        "adapter_set_sha256": "a" * 64,
        "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "build_result_sha256": "c" * 64,
        "build_log_sha256": "d" * 64,
        "platform": "linux/amd64",
    }
    monkeypatch.setattr(
        primitives_module,
        "resolve_plutus_conformance_adapter",
        lambda **_kwargs: {
            **expected,
            "executable": str(executable),
            "record_path": str(tmp_path / "registry-record.json"),
        },
    )
    seen = {}

    def run(command, **_kwargs):
        config_path = Path(command[-1])
        config = json.loads(config_path.read_text(encoding="utf-8"))
        snapshot_record = Path(config["adapters"]["amaru"]["record"])
        snapshot = json.loads(snapshot_record.read_text(encoding="utf-8"))
        snapshot_executable = Path(snapshot["executable"])
        assert snapshot_executable != executable
        assert tmp_path in snapshot_executable.parents
        assert hashlib.sha256(snapshot_executable.read_bytes()).hexdigest() == expected[
            "executable_sha256"
        ]
        seen["snapshot_root"] = snapshot_executable.parent
        output = Path(config["output_dir"])
        output.mkdir(parents=True)
        (output / "report.json").write_text("{}\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(primitives_module.subprocess, "run", run)

    class Handle:
        run_dir = tmp_path

        def __init__(self):
            self.events = []

        def log(self, **event):
            self.events.append(event)

    primitive = primitives_module.RuntimeVersionPinnedPlutusConformance(
        params={
            "output_dir": "outputs/plutus",
            "cost_model": "corpora/cardano-measurement/plutus-v2-cost-model-protocol-v10.json",
            "adapters": {
                "amaru": {
                    "source_revision": AMARU_REVISION,
                    "adapter_set_sha256": "a" * 64,
                    "expected_provenance": expected,
                }
            },
        }
    )
    handle = Handle()
    primitive.run(handle, None)

    assert not seen["snapshot_root"].exists()
    assert handle.events[-1]["event"] == "completed"


def test_card02_primitives_are_registered_and_scenarios_validate():
    from profile_manager.primitives import load_registry
    from profile_manager.scenario import semantic_validate_scenario

    root = Path(__file__).resolve().parents[1]
    registry = load_registry(root / "dwarf/primitives/registry.json")
    for name, family in {
        "runtime_version_pinned_plutus_conformance": "load",
        "runtime_controlled_plutus_transactions": "load",
        "plutus_result_and_budget_match": "assertion",
        "plutus_live_outcomes_observed": "assertion",
    }.items():
        assert registry[name].family == family
    for implementation in ("amaru", "cardano"):
        path = root / "dwarf/scenarios" / f"client-example-plutus-vm-{implementation}.yaml"
        assert semantic_validate_scenario(
            path, registry_path=root / "dwarf/primitives/registry.json"
        )["errors"] == []
