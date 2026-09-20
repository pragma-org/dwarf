import hashlib
import json
from pathlib import Path

import pytest

from scripts import runtime_version_pinned_cbor_conformance as subject


AMARU_REVISION = "b159172f25a9c389f82f20bca4f15e3032791638"


def _accepted():
    return {
        "schema_version": "v1",
        "implementation": "amaru",
        "source_revision": AMARU_REVISION,
        "boundary": "production-codec-only",
        "outcome": "accepted",
        "elapsed_nanos": 2184,
        "elapsed_micros": 2,
        "first_encode_hex": "182a",
        "second_encode_hex": "182a",
        "second_decode_outcome": "accepted",
    }


def test_adapter_nanoseconds_become_fractional_microseconds_without_rounding():
    result = subject._validate_result(
        _accepted(),
        implementation="amaru",
        source_revision=AMARU_REVISION,
        expected_outcome="accepted",
    )

    assert result["elapsed_nanos"] == 2184
    assert result["elapsed_micros"] == 2
    assert result["duration_micros"] == 2.184
    assert result["roundtrip_consistent"] is True


def test_adapter_result_fails_closed_without_nanoseconds_or_stable_second_encode():
    missing_nanos = {**_accepted()}
    missing_nanos.pop("elapsed_nanos")
    with pytest.raises(subject.ConformanceContractError, match="elapsed_nanos"):
        subject._validate_result(
            missing_nanos,
            implementation="amaru",
            source_revision=AMARU_REVISION,
            expected_outcome="accepted",
        )

    unstable = {**_accepted(), "second_encode_hex": "182b"}
    result = subject._validate_result(
        unstable,
        implementation="amaru",
        source_revision=AMARU_REVISION,
        expected_outcome="accepted",
    )
    assert result["roundtrip_consistent"] is False

    wrong_schema = {**_accepted(), "schema_version": "v0"}
    with pytest.raises(subject.ConformanceContractError, match="schema_version"):
        subject._validate_result(
            wrong_schema,
            implementation="amaru",
            source_revision=AMARU_REVISION,
            expected_outcome="accepted",
        )

    inconsistent_legacy_micros = {**_accepted(), "elapsed_micros": 3}
    with pytest.raises(subject.ConformanceContractError, match="elapsed_micros"):
        subject._validate_result(
            inconsistent_legacy_micros,
            implementation="amaru",
            source_revision=AMARU_REVISION,
            expected_outcome="accepted",
        )


def test_adapter_record_requires_exact_executable_digest_and_revision(tmp_path):
    executable = tmp_path / "adapter"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    record = tmp_path / "record.json"
    record.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "production-cbor-conformance",
                "implementation": "amaru",
                "source_revision": AMARU_REVISION,
                "measurement_boundary": "production-codec-only",
                "executable": str(executable),
                "executable_sha256": digest,
                "build_result_sha256": "a" * 64,
            }
        )
    )

    loaded = subject.load_adapter_record(
        record, implementation="amaru", source_revision=AMARU_REVISION
    )
    assert loaded["executable_sha256"] == digest

    body = json.loads(record.read_text())
    body["source_revision"] = "0" * 40
    record.write_text(json.dumps(body))
    with pytest.raises(subject.ConformanceContractError, match="source_revision"):
        subject.load_adapter_record(
            record, implementation="amaru", source_revision=AMARU_REVISION
        )


def test_runner_retains_exact_100_input_selection_and_raw_timing(monkeypatch, tmp_path):
    categories = ("valid", "zap-1", "zap-2", "zap-3")
    selected = []
    for category in categories:
        for index in range(25):
            selected.append(
                {
                    "relative_path": f"plutus_data/{category}/{index:05d}.cbor",
                    "sha256": f"{index:064x}",
                    "category": category,
                    "data": bytes([index]),
                }
            )
    monkeypatch.setattr(subject, "_git_revision", lambda _path: subject.QUALIFIED_DATASET_REVISION)
    monkeypatch.setattr(subject, "_select_inputs", lambda *_args: selected)
    monkeypatch.setattr(subject, "_dataset_digest", lambda _rows: "selection")
    monkeypatch.setattr(
        subject,
        "load_adapter_record",
        lambda *_args, **_kwargs: {
            "executable": "/exact/adapter",
            "kind": "production-cbor-conformance",
            "implementation": "amaru",
            "source_revision": AMARU_REVISION,
            "measurement_boundary": "production-codec-only",
            "executable_sha256": "b" * 64,
            "build_result_sha256": "c" * 64,
        },
    )

    def adapter(_executable, payload, _timeout):
        index = payload[0]
        category = selected[len(calls)]["category"]
        calls.append(index)
        if category == "valid":
            return _accepted()
        return {
            "schema_version": "v1",
            "implementation": "amaru",
            "source_revision": AMARU_REVISION,
            "boundary": "production-codec-only",
            "outcome": "rejected",
            "elapsed_nanos": 2184,
            "elapsed_micros": 2,
        }

    calls = []
    output = tmp_path / "out"
    report_path = subject.run_cbor_conformance(
        {
            "source_repository": subject.QUALIFIED_DATASET_REPOSITORY,
            "dataset_revision": subject.QUALIFIED_DATASET_REVISION,
            "dataset_repo_dir": tmp_path,
            "dataset_dir": tmp_path,
            "implementation": "amaru",
            "source_revision": AMARU_REVISION,
            "adapter_record": tmp_path / "adapter.json",
            "output_dir": output,
        },
        adapter_runner=adapter,
    )

    report = json.loads(report_path.read_text())
    assert report["input_count"] == 100
    assert report["checks"] == {
        "cbor_conformance_clean": True,
        "cbor_roundtrip_consistent": True,
    }
    assert report["records"][0]["duration_micros"] == 2.184
    assert report["records"][0]["elapsed_nanos"] == 2184
    assert len((output / "inputs.ndjson").read_text().splitlines()) == 100
    assert report["distributions"]["accepted"]["p50_micros"] == 2.184
    assert report["distributions"]["accepted"]["sample_count"] == 25
    summary = (output / "report.md").read_text()
    assert "2.184 us" in summary
    assert "Raw nanoseconds are retained" in summary



def test_runner_retains_expected_outcome_mismatch_as_a_failed_security_check(
    monkeypatch, tmp_path
):
    selected = []
    for category in ("valid", "zap-1", "zap-2", "zap-3"):
        for index in range(25):
            selected.append(
                {
                    "relative_path": f"plutus_data/{category}/{index:05d}.cbor",
                    "sha256": f"{index:064x}",
                    "category": category,
                    "data": bytes([index]),
                }
            )
    monkeypatch.setattr(subject, "_git_revision", lambda _path: subject.QUALIFIED_DATASET_REVISION)
    monkeypatch.setattr(subject, "_select_inputs", lambda *_args: selected)
    monkeypatch.setattr(subject, "_dataset_digest", lambda _rows: "selection")
    monkeypatch.setattr(
        subject,
        "load_adapter_record",
        lambda *_args, **_kwargs: {
            "executable": "/exact/adapter",
            "kind": "production-cbor-conformance",
            "implementation": "amaru",
            "source_revision": AMARU_REVISION,
            "measurement_boundary": "production-codec-only",
            "executable_sha256": "b" * 64,
            "build_result_sha256": "c" * 64,
        },
    )

    calls = []

    def adapter(_executable, _payload, _timeout):
        category = selected[len(calls)]["category"]
        calls.append(category)
        if category in {"valid", "zap-1"}:
            return _accepted()
        return {
            "schema_version": "v1",
            "implementation": "amaru",
            "source_revision": AMARU_REVISION,
            "boundary": "production-codec-only",
            "outcome": "rejected",
            "elapsed_nanos": 2184,
            "elapsed_micros": 2,
        }

    report_path = subject.run_cbor_conformance(
        {
            "source_repository": subject.QUALIFIED_DATASET_REPOSITORY,
            "dataset_revision": subject.QUALIFIED_DATASET_REVISION,
            "dataset_repo_dir": tmp_path,
            "dataset_dir": tmp_path,
            "implementation": "amaru",
            "source_revision": AMARU_REVISION,
            "adapter_record": tmp_path / "adapter.json",
            "output_dir": tmp_path / "out",
        },
        adapter_runner=adapter,
    )

    report = json.loads(report_path.read_text())
    assert len(report["outcome_mismatches"]) == 25
    assert report["outcome_mismatches"][0].startswith("plutus_data/zap-1/")
    assert report["checks"]["cbor_conformance_clean"] is False
    assert report["records"][25]["expected_outcome"] == "rejected"
    assert report["records"][25]["outcome"] == "accepted"



def test_adapter_process_exit_must_match_terminal_outcome(monkeypatch):
    import subprocess

    accepted = json.dumps(_accepted()).encode()
    monkeypatch.setattr(
        subject.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["adapter"], 1, stdout=accepted, stderr=b""
        ),
    )
    with pytest.raises(subject.ConformanceContractError, match="exit code"):
        subject._run_adapter("adapter", b"input", 1)
