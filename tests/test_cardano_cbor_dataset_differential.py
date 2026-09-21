import json
import os
import subprocess
from pathlib import Path

import pytest

from profile_manager.primitives import load_registry
from profile_manager.scenario import semantic_validate_scenario
from scripts import runtime_cardano_cbor_dataset_differential as dataset_runtime
from scripts.runtime_cardano_cbor_dataset_differential import (
    QUALIFIED_DATASET_REVISION,
    REQUIRED_CATEGORIES,
    _inspect_container_image,
    _load_target,
    run_cardano_cbor_dataset_differential,
    validate_qualified_dataset_contract,
)


def _write_executable(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _make_source_repo(tmp_path: Path) -> tuple[Path, Path, str]:
    repo = tmp_path / "cardano-cbor-dataset"
    dataset = repo / "dataset" / "conway-123-2"
    for category in REQUIRED_CATEGORIES:
        category_dir = dataset / "plutus_data" / category
        category_dir.mkdir(parents=True, exist_ok=True)
        prefix = b"valid" if category == "valid" else b"invalid"
        for index in range(2):
            (category_dir / f"{index:05d}-sample.cbor").write_bytes(prefix + bytes([index]))
    subprocess.run(["git", "init", "-q", repo], check=True)
    subprocess.run(["git", "-C", repo, "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", repo, "config", "user.name", "DWARF test"], check=True)
    subprocess.run(["git", "-C", repo, "add", "dataset"], check=True)
    subprocess.run(["git", "-C", repo, "commit", "-qm", "fixture"], check=True)
    revision = subprocess.check_output(["git", "-C", repo, "rev-parse", "HEAD"], text=True).strip()
    return repo, dataset, revision


def _make_target(tmp_path: Path, name: str, *, disagree: bool = False) -> tuple[str, Path]:
    binary = _write_executable(
        tmp_path / name,
        """#!/usr/bin/env python3
import sys
data = sys.stdin.buffer.read()
if %s:
    print("OK")
    raise SystemExit(0)
if data.startswith(b"valid"):
    print("OK")
    raise SystemExit(0)
print("ERR rejected")
raise SystemExit(1)
""" % ("True" if disagree else "False"),
    )
    manifests = tmp_path / "manifests"
    manifests.mkdir(exist_ok=True)
    manifest_id = name.replace("_", "-")
    (manifests / f"{manifest_id}.yaml").write_text(
        json.dumps(
            {
                "id": manifest_id,
                "binary": str(binary),
                "input_format": "stdin_bytes",
                "implementation": "amaru" if "amaru" in name else "cardano-node",
                "language": "rust" if "amaru" in name else "haskell",
                "upstream_commit": "fixture-revision",
                "decoder_type": "CBOR codec",
                "invariants": ["fixture"],
            }
        ),
        encoding="utf-8",
    )
    return manifest_id, manifests


def _make_verifier(tmp_path: Path) -> Path:
    return _write_executable(
        tmp_path / "cbor",
        """#!/usr/bin/env python3
import os
import pathlib
import sys
root = pathlib.Path(sys.argv[-1])
required = {"valid", "zap-1", "zap-2", "zap-3"}
actual = {p.name for p in (root / "plutus_data").iterdir() if p.is_dir()}
if actual != required:
    print(f"bad categories: {actual}", file=sys.stderr)
    raise SystemExit(2)
coverage = pathlib.Path(os.environ["CBOR_COVERAGE_DIR"])
coverage.mkdir(parents=True, exist_ok=True)
(coverage / "report.txt").write_text("100% fixture coverage\\n", encoding="utf-8")
print("Checked 8 conway CBOR files")
""",
    )


def _config(tmp_path: Path, *, disagree: bool = False) -> dict:
    repo, dataset, _fixture_revision = _make_source_repo(tmp_path)
    reference_id, manifests = _make_target(tmp_path, "cardano_target")
    candidate_id, _ = _make_target(tmp_path, "amaru_target", disagree=disagree)
    return {
        "dataset_dir": str(dataset),
        "dataset_repo_dir": str(repo),
        "expected_source_revision": QUALIFIED_DATASET_REVISION,
        "source_repository": "https://github.com/r2rationality/cardano-cbor-dataset.git",
        "era": "conway",
        "rule": "plutus_data",
        "samples_per_category": 2,
        "manifests_dir": str(manifests),
        "reference_target_id": reference_id,
        "candidate_target_id": candidate_id,
        "reference_verifier_binary": str(_make_verifier(tmp_path)),
        "output_dir": str(tmp_path / "out"),
        "per_input_timeout_seconds": 2,
    }


def test_differential_replay_is_non_vacuous_and_retains_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(dataset_runtime, "_git_revision", lambda _path: QUALIFIED_DATASET_REVISION)
    report_path = run_cardano_cbor_dataset_differential(_config(tmp_path))
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert report["source"]["revision_verified"] is True
    assert report["selection"]["category_counts"] == {category: 2 for category in REQUIRED_CATEGORIES}
    assert report["inputs_processed"] == 8
    assert report["reference_verifier"]["passed"] is True
    assert report["reference_target"]["reached"] == 8
    assert report["candidate_target"]["reached"] == 8
    assert report["mismatch_count"] == 0
    assert report["crash_or_timeout_count"] == 0
    assert report["clean"] is True
    assert len(report["selection"]["dataset_sha256"]) == 64
    assert (tmp_path / "out" / "inputs.ndjson").read_text(encoding="utf-8").count("\n") == 8
    assert (tmp_path / "out" / "reference-coverage" / "report.txt").is_file()
    assert (tmp_path / "out" / "summary.md").is_file()


def test_differential_replay_reports_candidate_disagreement(tmp_path, monkeypatch):
    monkeypatch.setattr(dataset_runtime, "_git_revision", lambda _path: QUALIFIED_DATASET_REVISION)
    report_path = run_cardano_cbor_dataset_differential(_config(tmp_path, disagree=True))
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert report["inputs_processed"] == 8
    assert report["mismatch_count"] == 6
    assert report["candidate_expectation_mismatch_count"] == 6
    assert report["clean"] is False


def test_differential_replay_refuses_wrong_source_revision(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr(dataset_runtime, "_git_revision", lambda _path: "0" * 40)

    with pytest.raises(ValueError, match="source revision mismatch"):
        run_cardano_cbor_dataset_differential(config)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("source_repository", "https://example.invalid/dataset.git", "repository"),
        ("expected_source_revision", "0" * 40, "revision"),
        ("era", "babbage", "Conway"),
        ("rule", "transaction_body", "plutus_data"),
    ],
)
def test_dataset_contract_refuses_every_unqualified_surface(field, value, message):
    config = {
        "source_repository": "https://github.com/r2rationality/cardano-cbor-dataset.git",
        "expected_source_revision": QUALIFIED_DATASET_REVISION,
        "era": "conway",
        "rule": "plutus_data",
    }
    config[field] = value

    with pytest.raises(ValueError, match=message):
        validate_qualified_dataset_contract(config)


def test_dataset_contract_accepts_only_pinned_conway_plutus_data():
    assert validate_qualified_dataset_contract(
        {
            "source_repository": "https://github.com/r2rationality/cardano-cbor-dataset.git",
            "expected_source_revision": QUALIFIED_DATASET_REVISION,
            "era": "conway",
            "rule": "plutus_data",
        }
    ) == {
        "source_repository": "https://github.com/r2rationality/cardano-cbor-dataset.git",
        "source_revision": QUALIFIED_DATASET_REVISION,
        "era": "conway",
        "rule": "plutus_data",
        "qualification": "typed-verifier-qualified",
    }


def test_differential_replay_requires_every_category(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr(dataset_runtime, "_git_revision", lambda _path: QUALIFIED_DATASET_REVISION)
    missing = Path(config["dataset_dir"]) / "plutus_data" / "zap-3"
    for path in missing.iterdir():
        path.unlink()
    missing.rmdir()

    with pytest.raises(ValueError, match="required categories"):
        run_cardano_cbor_dataset_differential(config)


def test_target_manifest_repo_root_binary_resolves_from_dwarf_cwd(tmp_path, monkeypatch):
    dwarf_dir = tmp_path / "dwarf"
    manifests = dwarf_dir / "targets" / "manifests"
    manifests.mkdir(parents=True)
    binary = _write_executable(dwarf_dir / "targets" / "fixture-target", "#!/bin/sh\nexit 0\n")
    (manifests / "fixture-target.yaml").write_text(
        json.dumps(
            {
                "id": "fixture-target",
                "binary": "dwarf/targets/fixture-target",
                "input_format": "stdin_bytes",
                "implementation": "fixture",
                "language": "fixture",
                "upstream_commit": "fixture",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(dwarf_dir)

    target = _load_target("fixture-target", manifests)

    assert target["binary"] == str(binary.resolve())


def test_container_image_inspection_retains_immutable_provenance(monkeypatch):
    payload = [
        {
            "Id": "sha256:image-id",
            "RepoDigests": ["cardano-cbor-dataset@sha256:repo-digest"],
            "Architecture": "amd64",
            "Os": "linux",
            "Config": {
                "Labels": {
                    "io.github.r2rationality.cardano-ledger.revision": "ledger-revision"
                }
            },
        }
    ]

    def fake_run(command, **kwargs):
        assert command == ["docker", "image", "inspect", "cardano-cbor-dataset:pinned"]
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    identity = _inspect_container_image("docker", "cardano-cbor-dataset:pinned")

    assert identity == {
        "image_id": "sha256:image-id",
        "repo_digests": ["cardano-cbor-dataset@sha256:repo-digest"],
        "architecture": "amd64",
        "os": "linux",
        "labels": {
            "io.github.r2rationality.cardano-ledger.revision": "ledger-revision"
        },
    }


def test_primitive_registry_and_scenario_are_wired():
    root = Path(__file__).resolve().parents[1]
    scenario_path = (
        root
        / "dwarf"
        / "scenarios"
        / "cardano-amaru-cbor-dataset-plutus-data-differential.yaml"
    )
    registry = load_registry(root / "dwarf" / "primitives" / "registry.json")
    assert registry["runtime_cardano_cbor_dataset_differential"].family == "load"
    assert registry["cardano_cbor_dataset_differential_clean"].family == "assertion"

    result = semantic_validate_scenario(
        scenario_path,
        registry_path=root / "dwarf" / "primitives" / "registry.json",
    )
    assert result["errors"] == []

    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    assert isinstance(scenario["seed"], str)
    assert scenario["seed"].lower().startswith("0x")
    int(scenario["seed"], 16)
    assert scenario["load"][0]["manifests_dir"] == "targets/manifests"
    assert scenario["load"][0]["rule"] == "plutus_data"
    assert scenario["load"][0]["reference_target_id"] == "cardano-node-cbor-decode-plutus-data"
    assert scenario["load"][0]["candidate_target_id"] == "amaru-cbor-decode-plutus-data"

    assert (root / "dwarf" / "targets" / "cardano-node" / "src" / "DecodePlutusData.hs").is_file()
    assert (root / "dwarf" / "targets" / "amaru" / "src" / "bin" / "decode_plutus_data.rs").is_file()
    assert (
        root / "dwarf" / "targets" / "manifests" / "cardano-node-cbor-decode-plutus-data.yaml"
    ).is_file()
    assert (
        root / "dwarf" / "targets" / "manifests" / "amaru-cbor-decode-plutus-data.yaml"
    ).is_file()


def test_clean_assertion_requires_non_vacuous_matching_report():
    root = Path(__file__).resolve().parents[1]
    registry = load_registry(root / "dwarf" / "primitives" / "registry.json")
    assertion_entry = registry["cardano_cbor_dataset_differential_clean"]
    module = __import__(assertion_entry.module, fromlist=[assertion_entry.class_name])
    assertion = getattr(module, assertion_entry.class_name)(
        params={"min_inputs_processed": 8, "min_samples_per_category": 2},
        entry=assertion_entry,
    )

    class Handle:
        events = [
            {
                "phase": "load",
                "primitive": "runtime_cardano_cbor_dataset_differential",
                "event": "completed",
                "payload": {
                    "outcome": "ok",
                    "artifact_summary": {
                        "has_result_json": True,
                        "has_inputs_ndjson": True,
                        "has_summary_markdown": True,
                    },
                    "report": {
                        "clean": True,
                        "non_vacuous": True,
                        "inputs_processed": 8,
                        "mismatch_count": 0,
                        "crash_or_timeout_count": 0,
                        "reference_verifier": {"passed": True},
                        "selection": {
                            "category_counts": {category: 2 for category in REQUIRED_CATEGORIES}
                        },
                        "reference_target": {"reached": 8},
                        "candidate_target": {"reached": 8},
                    },
                },
            }
        ]

    assert assertion.evaluate(Handle())["result"] == "pass"
