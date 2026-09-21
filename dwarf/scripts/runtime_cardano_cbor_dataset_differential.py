#!/usr/bin/env python3
"""Replay a pinned cardano-cbor-dataset slice through Haskell and Amaru.

The upstream verifier establishes that the selected corpus obeys its typed
ledger/category contract. DWARF then sends the exact same bytes to both target
shims and retains one transcript record per input.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path


REQUIRED_CATEGORIES = ("valid", "zap-1", "zap-2", "zap-3")
QUALIFIED_DATASET_REPOSITORY = (
    "https://github.com/r2rationality/cardano-cbor-dataset.git"
)
QUALIFIED_DATASET_REVISION = "a7561cd063550c2218898571520f14c3674efe91"
QUALIFIED_DATASET_ERA = "conway"
QUALIFIED_DATASET_RULE = "plutus_data"


def validate_qualified_dataset_contract(config: dict) -> dict:
    """Refuse Cardano dataset surfaces that have not passed the typed verifier gate."""
    repository = str(config.get("source_repository") or "")
    revision = str(config.get("expected_source_revision") or "")
    era = str(config.get("era") or "")
    rule = str(config.get("rule") or "")
    if repository != QUALIFIED_DATASET_REPOSITORY:
        raise ValueError(
            f"dataset repository is unqualified; expected {QUALIFIED_DATASET_REPOSITORY}"
        )
    if revision != QUALIFIED_DATASET_REVISION:
        raise ValueError(
            f"dataset revision is unqualified; expected {QUALIFIED_DATASET_REVISION}"
        )
    if era != QUALIFIED_DATASET_ERA:
        raise ValueError("only the qualified Conway dataset boundary is supported")
    if rule != QUALIFIED_DATASET_RULE:
        raise ValueError("only the qualified Conway plutus_data rule is supported")
    return {
        "source_repository": repository,
        "source_revision": revision,
        "era": era,
        "rule": rule,
        "qualification": "typed-verifier-qualified",
    }


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_revision(repo_dir: Path) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ValueError(f"dataset repository is not a readable Git checkout: {repo_dir}")
    return proc.stdout.strip()


def _load_target(target_id: str, manifests_dir: Path) -> dict:
    path = manifests_dir / f"{target_id}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"target manifest not found: {path}")
    body = json.loads(path.read_text(encoding="utf-8"))
    if body.get("id") != target_id:
        raise ValueError(f"target manifest id mismatch in {path}")
    if body.get("input_format", "stdin_bytes") != "stdin_bytes":
        raise ValueError(f"target {target_id} must accept stdin_bytes")
    binary = Path(os.path.expandvars(os.path.expanduser(str(body["binary"]))))
    if not binary.is_absolute():
        if binary.parts and binary.parts[0] == "dwarf":
            binary = manifests_dir.parents[2] / binary
        else:
            binary = Path.cwd() / binary
    if not binary.is_file():
        raise FileNotFoundError(f"target binary not found: {binary}")
    if not os.access(binary, os.X_OK):
        raise PermissionError(f"target binary is not executable: {binary}")
    return {
        "id": target_id,
        "implementation": body.get("implementation", "unknown"),
        "language": body.get("language", "unknown"),
        "upstream_commit": body.get("upstream_commit", "unknown"),
        "binary": str(binary.resolve()),
        "binary_sha256": _sha256_file(binary),
    }


def _select_inputs(dataset_dir: Path, rule: str, samples_per_category: int) -> list[dict]:
    if samples_per_category < 1:
        raise ValueError("samples_per_category must be at least 1")
    rule_dir = dataset_dir / rule
    if not rule_dir.is_dir() or rule_dir.is_symlink():
        raise ValueError(f"missing real dataset rule directory: {rule_dir}")
    categories = sorted(path.name for path in rule_dir.iterdir() if path.is_dir())
    if categories != sorted(REQUIRED_CATEGORIES):
        raise ValueError(
            f"required categories for {rule}: expected {sorted(REQUIRED_CATEGORIES)}, got {categories}"
        )

    selected: list[dict] = []
    for category in REQUIRED_CATEGORIES:
        category_dir = rule_dir / category
        if category_dir.is_symlink():
            raise ValueError(f"dataset category must not be a symbolic link: {category_dir}")
        entries = sorted(category_dir.iterdir(), key=lambda path: path.name)
        invalid = [path for path in entries if path.is_symlink() or not path.is_file() or path.suffix != ".cbor"]
        if invalid:
            raise ValueError(f"dataset category contains unsupported entries: {invalid}")
        if len(entries) < samples_per_category:
            raise ValueError(
                f"dataset category {category} has {len(entries)} files; need {samples_per_category}"
            )
        for path in entries[:samples_per_category]:
            data = path.read_bytes()
            selected.append(
                {
                    "source_path": path,
                    "relative_path": f"{rule}/{category}/{path.name}",
                    "category": category,
                    "expected_outcome": "ok" if category == "valid" else "clean_error",
                    "size": len(data),
                    "sha256": _sha256_bytes(data),
                    "data": data,
                }
            )
    return selected


def _dataset_digest(selected: list[dict]) -> str:
    digest = hashlib.sha256()
    for record in selected:
        digest.update(record["relative_path"].encode("utf-8"))
        digest.update(b"\0")
        digest.update(record["sha256"].encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _stage_dataset(selected: list[dict], stage_dir: Path) -> None:
    if stage_dir.exists():
        raise FileExistsError(f"staged dataset path already exists: {stage_dir}")
    for record in selected:
        destination = stage_dir / record["relative_path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(record["source_path"], destination)


def _classify(returncode: int, stdout: str) -> str:
    first_line = stdout.splitlines()[0] if stdout else ""
    if returncode == 0 and first_line.startswith("OK"):
        return "ok"
    if returncode == 1 and first_line.startswith("ERR "):
        return "clean_error"
    return "crash"


def _run_target(binary: str, data: bytes, timeout_seconds: float) -> dict:
    started = time.monotonic()
    try:
        proc = subprocess.run(
            [binary],
            input=data,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        elapsed_ms = round((time.monotonic() - started) * 1000, 3)
        stdout = proc.stdout.decode("utf-8", errors="replace")
        stderr = proc.stderr.decode("utf-8", errors="replace")
        return {
            "outcome": _classify(proc.returncode, stdout),
            "exit_code": proc.returncode,
            "timed_out": False,
            "duration_ms": elapsed_ms,
            "stdout_head": stdout[:512],
            "stderr_head": stderr[:512],
        }
    except subprocess.TimeoutExpired as exc:
        elapsed_ms = round((time.monotonic() - started) * 1000, 3)
        return {
            "outcome": "crash",
            "exit_code": None,
            "timed_out": True,
            "duration_ms": elapsed_ms,
            "stdout_head": (exc.stdout or b"").decode("utf-8", errors="replace")[:512],
            "stderr_head": (exc.stderr or b"").decode("utf-8", errors="replace")[:512],
        }


def _inspect_container_image(docker_binary: str, image: str) -> dict:
    inspect = subprocess.run(
        [docker_binary, "image", "inspect", image],
        capture_output=True,
        text=True,
        check=False,
    )
    if inspect.returncode != 0:
        raise RuntimeError(f"reference verifier image is unavailable: {image}")
    try:
        payload = json.loads(inspect.stdout)
        record = payload[0]
    except (json.JSONDecodeError, IndexError, KeyError, TypeError) as exc:
        raise RuntimeError(f"reference verifier image inspection is invalid: {image}") from exc
    image_id = record.get("Id")
    if not isinstance(image_id, str) or not image_id.startswith("sha256:"):
        raise RuntimeError(f"reference verifier image has no immutable image id: {image}")
    config = record.get("Config") if isinstance(record.get("Config"), dict) else {}
    labels = config.get("Labels") if isinstance(config.get("Labels"), dict) else {}
    repo_digests = record.get("RepoDigests")
    if not isinstance(repo_digests, list):
        repo_digests = []
    return {
        "image_id": image_id,
        "repo_digests": sorted(str(digest) for digest in repo_digests),
        "architecture": record.get("Architecture"),
        "os": record.get("Os"),
        "labels": dict(sorted((str(key), str(value)) for key, value in labels.items())),
    }


def _run_reference_verifier(config: dict, output_dir: Path, stage_dir: Path) -> dict:
    era = str(config["era"])
    coverage_dir = output_dir / "reference-coverage"
    coverage_dir.mkdir(parents=True, exist_ok=False)
    verifier_binary = config.get("reference_verifier_binary")
    verifier_image = config.get("reference_verifier_image")
    if bool(verifier_binary) == bool(verifier_image):
        raise ValueError("configure exactly one reference_verifier_binary or reference_verifier_image")

    if verifier_binary:
        binary = Path(os.path.expandvars(os.path.expanduser(str(verifier_binary)))).resolve()
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise FileNotFoundError(f"reference verifier binary is not executable: {binary}")
        command = [str(binary), "verify", "--era", era, "deserialize", str(stage_dir)]
        verifier_identity = {"kind": "binary", "path": str(binary), "sha256": _sha256_file(binary)}
    else:
        docker_binary = str(config.get("docker_binary", "docker"))
        image_provenance = _inspect_container_image(docker_binary, str(verifier_image))
        platform = str(config.get("reference_verifier_platform", "linux/amd64"))
        command = [
            docker_binary,
            "run",
            "--rm",
            "--platform",
            platform,
            "-e",
            "CBOR_COVERAGE_DIR=/output/reference-coverage",
            "-v",
            f"{output_dir}:/output",
            str(verifier_image),
            "verify",
            "--era",
            era,
            "deserialize",
            f"/output/{stage_dir.name}",
        ]
        verifier_identity = {
            "kind": "container_image",
            "image": str(verifier_image),
            **image_provenance,
            "platform": platform,
        }

    env = os.environ.copy()
    env["CBOR_COVERAGE_DIR"] = str(coverage_dir)
    env["HPCTIXFILE"] = str(coverage_dir / "reference-verifier.tix")
    timeout_seconds = float(config.get("reference_verifier_timeout_seconds", 600))
    started = time.monotonic()
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            env=env,
        )
        timed_out = False
        exit_code = proc.returncode
        stdout = proc.stdout
        stderr = proc.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = None
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
    elapsed_ms = round((time.monotonic() - started) * 1000, 3)
    (output_dir / "reference-verifier.stdout.log").write_text(stdout, encoding="utf-8")
    (output_dir / "reference-verifier.stderr.log").write_text(stderr, encoding="utf-8")
    coverage_files = sorted(
        str(path.relative_to(output_dir)) for path in coverage_dir.rglob("*") if path.is_file()
    )
    return {
        **verifier_identity,
        "command": command,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "duration_ms": elapsed_ms,
        "passed": exit_code == 0 and not timed_out,
        "coverage_files": coverage_files,
        "stdout_log": "reference-verifier.stdout.log",
        "stderr_log": "reference-verifier.stderr.log",
    }


def _write_summary(output_dir: Path, report: dict) -> None:
    counts = report["selection"]["category_counts"]
    lines = [
        "# Cardano CBOR Dataset Differential",
        "",
        f"- Source revision verified: `{report['source']['revision_verified']}`",
        f"- Dataset digest: `{report['selection']['dataset_sha256']}`",
        f"- Era/rule: `{report['era']}/{report['rule']}`",
        f"- Category counts: `{json.dumps(counts, sort_keys=True)}`",
        f"- Inputs processed: {report['inputs_processed']}",
        f"- Upstream typed verifier passed: `{report['reference_verifier']['passed']}`",
        f"- Cardano/Amaru mismatches: {report['mismatch_count']}",
        f"- Crashes or timeouts: {report['crash_or_timeout_count']}",
        f"- Clean: `{report['clean']}`",
        "",
        "> Scope: typed library decoder evidence only; this is not live-node or protocol-path evidence.",
        "",
    ]
    (output_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def run_cardano_cbor_dataset_differential(config: dict) -> Path:
    qualification = validate_qualified_dataset_contract(config)
    dataset_dir = Path(os.path.expandvars(os.path.expanduser(str(config["dataset_dir"])))).resolve()
    repo_dir = Path(os.path.expandvars(os.path.expanduser(str(config["dataset_repo_dir"])))).resolve()
    output_dir = Path(os.path.expandvars(os.path.expanduser(str(config["output_dir"])))).resolve()
    manifests_dir = Path(os.path.expandvars(os.path.expanduser(str(config["manifests_dir"])))).resolve()
    if not dataset_dir.is_dir() or dataset_dir.is_symlink():
        raise FileNotFoundError(f"dataset directory not found: {dataset_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    actual_revision = _git_revision(repo_dir)
    expected_revision = str(config["expected_source_revision"])
    if actual_revision != expected_revision:
        raise ValueError(
            f"source revision mismatch: expected {expected_revision}, got {actual_revision}"
        )

    rule = str(config["rule"])
    selected = _select_inputs(dataset_dir, rule, int(config.get("samples_per_category", 10)))
    stage_dir = output_dir / "reference-dataset"
    _stage_dataset(selected, stage_dir)
    reference_verifier = _run_reference_verifier(config, output_dir, stage_dir)

    reference_target = _load_target(str(config["reference_target_id"]), manifests_dir)
    candidate_target = _load_target(str(config["candidate_target_id"]), manifests_dir)
    timeout_seconds = float(config.get("per_input_timeout_seconds", 5))
    transcript_path = output_dir / "inputs.ndjson"
    transcript: list[dict] = []
    with transcript_path.open("w", encoding="utf-8") as stream:
        for index, source in enumerate(selected, start=1):
            reference = _run_target(reference_target["binary"], source["data"], timeout_seconds)
            candidate = _run_target(candidate_target["binary"], source["data"], timeout_seconds)
            record = {
                "index": index,
                "relative_path": source["relative_path"],
                "category": source["category"],
                "expected_outcome": source["expected_outcome"],
                "size": source["size"],
                "sha256": source["sha256"],
                "reference": reference,
                "candidate": candidate,
                "targets_agree": reference["outcome"] == candidate["outcome"],
                "reference_matches_expectation": reference["outcome"] == source["expected_outcome"],
                "candidate_matches_expectation": candidate["outcome"] == source["expected_outcome"],
            }
            transcript.append(record)
            stream.write(json.dumps(record, sort_keys=True) + "\n")

    category_counts = {
        category: sum(1 for record in selected if record["category"] == category)
        for category in REQUIRED_CATEGORIES
    }
    mismatch_count = sum(1 for record in transcript if not record["targets_agree"])
    reference_mismatch_count = sum(1 for record in transcript if not record["reference_matches_expectation"])
    candidate_mismatch_count = sum(1 for record in transcript if not record["candidate_matches_expectation"])
    crash_or_timeout_count = sum(
        1
        for record in transcript
        for target in (record["reference"], record["candidate"])
        if target["outcome"] == "crash" or target["timed_out"]
    )
    non_vacuous = len(transcript) > 0 and all(category_counts.values())
    clean = (
        non_vacuous
        and reference_verifier["passed"]
        and mismatch_count == 0
        and reference_mismatch_count == 0
        and candidate_mismatch_count == 0
        and crash_or_timeout_count == 0
    )
    report = {
        "schema_version": 1,
        "source": {
            "repository": str(config["source_repository"]),
            "repo_dir": str(repo_dir),
            "expected_revision": expected_revision,
            "actual_revision": actual_revision,
            "revision_verified": actual_revision == expected_revision,
        },
        "era": str(config["era"]),
        "rule": rule,
        "selection": {
            "dataset_dir": str(dataset_dir),
            "samples_per_category": int(config.get("samples_per_category", 10)),
            "category_counts": category_counts,
            "dataset_sha256": _dataset_digest(selected),
            "inputs": [
                {
                    key: record[key]
                    for key in ("relative_path", "category", "expected_outcome", "size", "sha256")
                }
                for record in selected
            ],
        },
        "reference_verifier": reference_verifier,
        "reference_target": {**reference_target, "reached": len(transcript)},
        "candidate_target": {**candidate_target, "reached": len(transcript)},
        "inputs_processed": len(transcript),
        "mismatch_count": mismatch_count,
        "reference_expectation_mismatch_count": reference_mismatch_count,
        "candidate_expectation_mismatch_count": candidate_mismatch_count,
        "crash_or_timeout_count": crash_or_timeout_count,
        "non_vacuous": non_vacuous,
        "clean": clean,
        "transcript": "inputs.ndjson",
        "claim_scope": "typed_library_decoder_only",
        "qualification": qualification,
    }
    report_path = output_dir / "result.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_summary(output_dir, report)
    return report_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="JSON configuration file")
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    report_path = run_cardano_cbor_dataset_differential(config)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    print(
        " ".join(
            [
                "cardano_cbor_dataset_differential_completed=true",
                f"inputs_processed={report['inputs_processed']}",
                f"mismatch_count={report['mismatch_count']}",
                f"clean={str(report['clean']).lower()}",
            ]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
