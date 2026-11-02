import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from profile_manager import primitives
from scripts import runtime_cardano_cbor_dataset_differential as dataset_runtime


QUALIFIED_RULES = frozenset(
    {
        "plutus_data",
        "transaction_body",
        "block",
        "header",
        "certificate",
        "auxiliary_data",
    }
)


def _write_executable(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _relative_paths(rule: str = "transaction_body") -> list[str]:
    return [
        f"{rule}/{category}/{index:05d}.cbor"
        for category in dataset_runtime.REQUIRED_CATEGORIES
        for index in range(2)
    ]


def _config(
    tmp_path: Path,
    *,
    rejected_paths: set[str],
    candidate_accepts: set[str] | None = None,
    host_failure_paths: bool = False,
    structural_lines: list[str] | None = None,
    verifier_signal: bool = False,
    verifier_sleep_seconds: float = 0.0,
) -> tuple[dict, dict[str, bytes]]:
    candidate_accepts = candidate_accepts or set()
    structural_lines = structural_lines or []
    repo = tmp_path / "cardano-cbor-dataset"
    dataset = repo / "dataset" / "conway-123-2"
    input_bytes = {}
    for relative_path in _relative_paths():
        data = (
            b"accept:" + relative_path.encode("utf-8")
            if relative_path in candidate_accepts or relative_path not in rejected_paths
            else b"reject:" + relative_path.encode("utf-8")
        )
        path = dataset / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        input_bytes[relative_path] = data

    candidate = _write_executable(
        tmp_path / "candidate",
        """#!/usr/bin/env python3
import sys

data = sys.stdin.buffer.read()
if data.startswith(b"reject:"):
    print("ERR fixture rejection")
    raise SystemExit(1)
print("OK")
""",
    )
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    candidate_id = "amaru-cbor-decode-tx-body"
    (manifests / f"{candidate_id}.yaml").write_text(
        json.dumps(
            {
                "id": candidate_id,
                "binary": str(candidate),
                "input_format": "stdin_bytes",
                "implementation": "amaru",
                "language": "rust",
                "upstream_commit": "fixture-revision",
            }
        ),
        encoding="utf-8",
    )

    output_dir = tmp_path / "output"
    failure_root = (
        str(output_dir / "reference-dataset")
        if host_failure_paths
        else "/output/reference-dataset"
    )
    failure_lines = [
        f"FAIL {failure_root}/{relative_path} (fixture rejection)"
        for relative_path in sorted(rejected_paths)
    ]
    failure_lines.extend(structural_lines)
    verifier = _write_executable(
        tmp_path / "cbor-verifier",
        """#!/usr/bin/env python3
import os
from pathlib import Path
import signal
import time

lines = %s
sleep_seconds = %s
terminate_by_signal = %s
coverage = Path(os.environ["CBOR_COVERAGE_DIR"])
run_dir = coverage / "reference-dataset-deserialize-20261102T000000Z"
run_dir.mkdir(parents=True, exist_ok=True)
(run_dir / "failures.log").write_text("\\n".join(lines) + ("\\n" if lines else ""), encoding="utf-8")
(run_dir / "report.txt").write_text("fixture report\\n", encoding="utf-8")
time.sleep(sleep_seconds)
if terminate_by_signal:
    os.kill(os.getpid(), signal.SIGKILL)
raise SystemExit(1 if lines else 0)
"""
        % (
            repr(failure_lines),
            repr(verifier_sleep_seconds),
            repr(verifier_signal),
        ),
    )
    return (
        {
            "dataset_dir": str(dataset),
            "dataset_repo_dir": str(repo),
            "expected_source_revision": dataset_runtime.QUALIFIED_DATASET_REVISION,
            "source_repository": dataset_runtime.QUALIFIED_DATASET_REPOSITORY,
            "era": "conway",
            "rule": "transaction_body",
            "samples_per_category": 2,
            "manifests_dir": str(manifests),
            "candidate_target_id": candidate_id,
            "reference_verifier_binary": str(verifier),
            "output_dir": str(output_dir),
            "per_input_timeout_seconds": 2,
            "reference_verifier_timeout_seconds": (
                0.05 if verifier_sleep_seconds else 2
            ),
        },
        input_bytes,
    )


def _run(tmp_path: Path, monkeypatch, **config_options) -> dict:
    config, _input_bytes = _config(tmp_path, **config_options)
    monkeypatch.setattr(
        dataset_runtime,
        "_git_revision",
        lambda _repo: dataset_runtime.QUALIFIED_DATASET_REVISION,
    )
    report_path = dataset_runtime.run_cardano_cbor_dataset_differential(config)
    return json.loads(report_path.read_text(encoding="utf-8"))


def test_verifier_derived_reference_is_label_independent(tmp_path, monkeypatch):
    report = _run(
        tmp_path,
        monkeypatch,
        rejected_paths=set(_relative_paths()),
    )

    assert report["reference_verifier"]["passed"] is False
    assert report["reference_verifier"]["parsed"] is True
    assert report["reference_verifier"]["failed_input_count"] == 8
    assert report["reference_target"]["kind"] == "reference_verifier_failures_log"
    assert report["reference_target"]["reached"] == 8
    assert report["mismatch_count"] == 0
    assert report["reference_expectation_mismatch_count"] == 2
    assert report["candidate_expectation_mismatch_count"] == 2
    assert report["qualification"]["qualification"] == "typed-verifier-verdict-only"
    assert report["qualification"]["labels_trusted"] is False
    assert report["clean"] is True


def test_verifier_derived_reference_records_candidate_divergence(tmp_path, monkeypatch):
    mismatch_path = "transaction_body/zap-1/00000.cbor"
    config, input_bytes = _config(
        tmp_path,
        rejected_paths=set(_relative_paths()),
        candidate_accepts={mismatch_path},
    )
    monkeypatch.setattr(
        dataset_runtime,
        "_git_revision",
        lambda _repo: dataset_runtime.QUALIFIED_DATASET_REVISION,
    )

    report_path = dataset_runtime.run_cardano_cbor_dataset_differential(config)
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert report["clean"] is False
    assert report["mismatch_count"] == 1
    assert report["divergences"] == [
        {
            "relative_path": mismatch_path,
            "sha256": hashlib.sha256(input_bytes[mismatch_path]).hexdigest(),
            "reference_outcome": "clean_error",
            "candidate_outcome": "ok",
        }
    ]


def test_qualified_dataset_contract_accepts_six_rules_and_rejects_redeemer():
    base = {
        "source_repository": dataset_runtime.QUALIFIED_DATASET_REPOSITORY,
        "expected_source_revision": dataset_runtime.QUALIFIED_DATASET_REVISION,
        "era": "conway",
    }

    assert dataset_runtime.QUALIFIED_DATASET_RULES == QUALIFIED_RULES
    for rule in QUALIFIED_RULES:
        qualification = dataset_runtime.validate_qualified_dataset_contract(
            {**base, "rule": rule}
        )
        assert qualification["rule"] == rule
        assert qualification["qualification"] == "typed-verifier-verdict-only"
        assert qualification["labels_trusted"] is (rule == "plutus_data")

    with pytest.raises(ValueError, match="qualified Conway"):
        dataset_runtime.validate_qualified_dataset_contract({**base, "rule": "redeemer"})


def test_structural_failures_log_error_marks_verifier_unparsed(tmp_path, monkeypatch):
    offending_line = "unknown conway rule transaction_body"
    report = _run(
        tmp_path,
        monkeypatch,
        rejected_paths=set(),
        structural_lines=[offending_line],
    )

    assert report["reference_verifier"]["parsed"] is False
    assert report["reference_verifier"]["parse_errors"] == [offending_line]
    assert report["mismatch_count"] == 0
    assert report["clean"] is False


def test_binary_verifier_host_paths_match_staged_inputs(tmp_path, monkeypatch):
    report = _run(
        tmp_path,
        monkeypatch,
        rejected_paths=set(_relative_paths()),
        host_failure_paths=True,
    )

    assert report["reference_verifier"]["parsed"] is True
    assert report["mismatch_count"] == 0
    assert report["clean"] is True


def test_timed_out_verifier_log_is_not_usable_reference_evidence(tmp_path, monkeypatch):
    report = _run(
        tmp_path,
        monkeypatch,
        rejected_paths=set(_relative_paths()),
        verifier_sleep_seconds=1,
    )

    assert report["reference_verifier"]["timed_out"] is True
    assert report["reference_verifier"]["parsed"] is False
    assert "timed out" in " ".join(report["reference_verifier"]["parse_errors"])
    assert report["clean"] is False


def test_signaled_verifier_log_is_not_usable_reference_evidence(tmp_path, monkeypatch):
    report = _run(
        tmp_path,
        monkeypatch,
        rejected_paths=set(_relative_paths()),
        verifier_signal=True,
    )

    assert report["reference_verifier"]["exit_code"] < 0
    assert report["reference_verifier"]["parsed"] is False
    assert "signal" in " ".join(report["reference_verifier"]["parse_errors"])
    assert report["clean"] is False


def test_runtime_primitive_omits_absent_reference_target(tmp_path, monkeypatch):
    config, _input_bytes = _config(tmp_path, rejected_paths=set())
    captured = {}

    class Handle:
        run_dir = tmp_path / "run"
        events = []

        def log(self, **event):
            self.events.append(event)

    def fake_run(command, **_kwargs):
        config_path = Path(command[-1])
        captured.update(json.loads(config_path.read_text(encoding="utf-8")))
        return subprocess.CompletedProcess(command, 0, stdout=b"completed", stderr=b"")

    monkeypatch.setattr(primitives.subprocess, "run", fake_run)
    primitive = primitives.RuntimeCardanoCborDatasetDifferential(params=config)

    primitive.run(Handle(), None)

    assert "reference_target_id" not in captured
