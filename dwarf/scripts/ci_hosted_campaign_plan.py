#!/usr/bin/env python3
"""Register one verified hosted Amaru image and freeze its initial campaign.

The registry exists only inside the disposable GitHub-hosted job.  This module
does not publish an image or assert node compatibility.  It retains the exact
local manifest identity and leaves runtime compatibility pending qualification.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import subprocess

from profile_manager.candidate_build_executor import _owned_directory, _save
from profile_manager.run_plan import digest_run_plan
from profile_manager.security_campaign import _schema_validate, digest_security_campaign, resolve_security_campaign
from profile_manager.security_campaign_suite import load_measurement_suite, load_security_campaign_suite
from profile_manager.testplease_candidates import CandidateSnapshot
from profile_manager.version_catalog import load_version_catalog, resolve_default, validate_version_catalog


ROOT = Path(__file__).resolve().parents[2]
REGISTRY_REPOSITORY = "localhost:5000/dwarf/amaru-candidate"
INITIAL_SECURITY_ENTRIES = (
    "ledger-submit-transaction-array-arity-differential-amaru-cardano-node",
    "ledger-submit-empty-redeemers-container-differential-amaru-cardano-node",
    "ledger-submit-inline-datum-tag24-trailing-differential-amaru-cardano-node",
    "ledger-submit-multiasset-compact-boundary-differential-amaru-cardano-node",
    "ledger-submit-uplc-deep-delay-stack-safety-differential-amaru-cardano-node",
    "governance-zero-denominator-admission-differential-amaru-cardano-node",
)
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_TREE = re.compile(r"^[0-9a-f]{64}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,127}$")


def select_primary_mode(builds: list[dict]) -> dict:
    """Choose execution mode from independently observed build outcomes.

    A missing or failed patched lane is a stock fallback, not a candidate
    rejection. Stock must always be a complete immutable build because it is the
    submitted-source authority and the replay target.
    """
    by_mode = {}
    for row in builds:
        mode = row.get("mode")
        if mode in by_mode:
            raise ValueError(f"duplicate hosted build observation: {mode}")
        by_mode[mode] = row
    stock = by_mode.get("stock", {})
    if (stock.get("status") != "built"
            or not _DIGEST.fullmatch(str(stock.get("artifacts", {}).get("image_digest", "")))):
        raise ValueError("hosted campaign requires an immutable successful stock build")
    patched = by_mode.get("patched")
    common = {"fallback_mode": "stock"}
    if patched is None:
        return {**common, "primary_mode": "stock", "reason": "patch-unavailable:missing-patched-build-observation",
                "calibration_required": False, "stock_replay_required": False}
    if patched.get("status") != "built":
        category = "patch-build-failed" if patched.get("status") == "error" else "patch-unavailable"
        reason = str(patched.get("reason") or "unspecified")
        return {**common, "primary_mode": "stock", "reason": f"{category}:{reason}",
                "calibration_required": False, "stock_replay_required": False}
    if (not _DIGEST.fullmatch(str(patched.get("patch_set_digest", "")))
            or not _DIGEST.fullmatch(str(patched.get("artifacts", {}).get("image_digest", "")))):
        raise ValueError("successful patched build lacks immutable artifact or patch identity")
    return {**common, "primary_mode": "patched", "reason": "compatible-patched-build-observed",
            "calibration_required": True, "stock_replay_required": True}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _regular_json(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"missing regular JSON input: {path.name}")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"JSON input must be an object: {path.name}")
    return value


def _amaru_version(metadata: dict) -> str:
    matches = []
    for package in metadata.get("packages", []):
        targets = package.get("targets", []) if isinstance(package, dict) else []
        if (package.get("name") == "amaru"
                and any(target.get("name") == "amaru" and "bin" in target.get("kind", [])
                        for target in targets if isinstance(target, dict))):
            matches.append(package.get("version"))
    if len(matches) != 1 or not isinstance(matches[0], str) or not _VERSION.fullmatch(matches[0]):
        raise ValueError("Cargo metadata does not identify one exact Amaru executable version")
    return matches[0]


def _registered_image(local_name: str, tree: str, config_id: str, *, mode="stock", run) -> dict:
    if mode not in {"stock", "patched"}:
        raise ValueError("hosted image mode must be stock or patched")
    target = f"{REGISTRY_REPOSITORY}:{tree[:16]}" + ("" if mode == "stock" else "-patched")
    run(["docker", "image", "tag", local_name, target], check=True,
        capture_output=True, text=True, timeout=60)
    run(["docker", "image", "push", target], check=True,
        capture_output=True, text=True, timeout=300)
    inspected = run(["docker", "image", "inspect", target], check=True,
                    capture_output=True, text=True, timeout=60)
    decoded = json.loads(inspected.stdout)
    image = decoded[0] if isinstance(decoded, list) and len(decoded) == 1 else None
    repo_digests = image.get("RepoDigests", []) if isinstance(image, dict) else []
    prefix = REGISTRY_REPOSITORY + "@"
    exact = sorted({value[len(prefix):] for value in repo_digests
                    if isinstance(value, str) and value.startswith(prefix)})
    if (image is None or image.get("Id") != config_id or image.get("Os") != "linux"
            or image.get("Architecture") != "amd64" or target not in image.get("RepoTags", [])
            or (image.get("Config") or {}).get("Entrypoint") != ["/usr/local/bin/amaru"]
            or len(exact) != 1 or not _DIGEST.fullmatch(exact[0])):
        raise ValueError("registered runtime image identity or platform differs from verified stage")
    return {"mode": mode, "tag": target, "repository": REGISTRY_REPOSITORY,
            "manifest_digest": exact[0], "config_image_id": config_id}


def _campaign_catalog(source: dict, *, version: str, commit: str, images: list[dict]) -> dict:
    catalog = deepcopy(source)
    catalog["releases"] = [row for row in catalog["releases"]
                           if not (row.get("implementation") == "amaru" and row.get("version") == version)]
    # A same-version official pair says nothing about candidate source bytes.
    catalog["compatibility_pairs"] = [row for row in catalog["compatibility_pairs"]
                                      if row.get("amaru_version") != version]
    artifacts = []
    for row in images:
        artifact = {"kind": "oci", "reference": row["repository"],
                    "availability": "available", "digest": row["manifest_digest"],
                    "config_image_id": row["config_image_id"],
                    "executable_digest": row["executable_digest"],
                    "mode": row["mode"], "public": False,
                    "platforms": ["linux/amd64"], "runtime_interface": "extracted-binary",
                    "note": "Disposable GitHub-hosted candidate registry; retained campaign identity only."}
        if row.get("patch_set_digest"):
            artifact["patch_set_digest"] = row["patch_set_digest"]
        artifacts.append(artifact)
    catalog["releases"].append({
        "implementation": "amaru", "version": version, "channel": "main",
        "source_revision": commit,
        "artifacts": artifacts,
        "verification": {},
    })
    return validate_version_catalog(catalog)


def _hosted_measurement_campaign(manifest: dict, *, primary_mode: str) -> tuple[dict, list[str]]:
    """Keep one executable exercise per implementation/tap and observed mode."""
    result = deepcopy(manifest)
    selected = {}
    all_ids = set()
    candidate_implementation = result["candidate"]["implementation"]
    for cell in sorted(result["required_tap_cells"],
                       key=lambda row: (row["implementation"], row["measurement_id"],
                                        row["topology_id"], row["node_id"])):
        all_ids.add(cell["measurement_id"])
        key = (cell["implementation"], cell["measurement_id"])
        desired = primary_mode if cell["implementation"] == candidate_implementation else "stock"
        if desired in cell["target_modes"] and key not in selected:
            selected[key] = {**cell, "target_modes": [desired]}
    result["required_tap_cells"] = [selected[key] for key in sorted(selected)]
    selected_ids = {cell["measurement_id"] for cell in result["required_tap_cells"]}
    deferred = sorted(all_ids - selected_ids)
    for key in ("run_plan_digest", "campaign_digest", "campaign_id"):
        result.pop(key, None)
    result["run_plan_digest"] = digest_run_plan(result)
    result["campaign_digest"] = digest_security_campaign(result)
    result["campaign_id"] = "campaign-" + result["campaign_digest"].split(":")[1]
    _schema_validate(result)
    return result, deferred


def _security_suite_for_mode(suite: dict, implementation: str, mode: str) -> dict:
    result = deepcopy(suite)
    if mode == "stock":
        return result
    profile = "amaru-security-patched" if implementation == "amaru" else "cardano-security-patched"
    for group in result["groups"]:
        for entry in group["entries"]:
            for attachment in entry.get("measurement_attachments", []):
                if attachment["implementation"] != implementation:
                    continue
                attachment["target_mode"] = "patched"
                attachment["measurement_profile"] = profile
                attachment["result_namespace"] = attachment["result_namespace"].replace("/stock", "/patched")
    return result


def plan_hosted_campaign(discovery_path, stage_path, stock_build, directory, *,
                         catalog_path=ROOT / "dwarf/versions/catalog.json", run=subprocess.run):
    output = _owned_directory(directory)
    status = {"schema_version": "v1", "kind": "hosted-campaign-plan-status",
              "status": "partial", "ready_for_campaign": False, "published": False}
    _save(output / "status.json", status)
    try:
        discovery_path, stage_path, stock_build = Path(discovery_path), Path(stage_path), Path(stock_build)
        discovery = _regular_json(discovery_path)
        stage = _regular_json(stage_path)
        discovery_sha = _sha256(discovery_path)
        if (discovery_path.with_suffix(".sha256").read_text().strip() != discovery_sha
                or discovery.get("kind") != "security-campaign-discovery"
                or discovery.get("status") != "validated"
                or stage.get("kind") != "hosted-campaign-stage" or stage.get("status") != "staged"
                or stage.get("reason") != "hosted-campaign-plan-registration-pending"
                or stage.get("discovery_sha256") != discovery_sha):
            raise ValueError("hosted discovery or stage identity differs")
        candidates = discovery.get("candidates", [])
        staged = stage.get("candidates", [])
        if len(candidates) != 1 or len(staged) != 1:
            raise ValueError("initial hosted plan requires exactly one Amaru candidate")
        candidate, staged_candidate = candidates[0], staged[0]
        key = (candidate.get("repository_commit"), candidate.get("source_tree_digest"))
        if (candidate.get("implementation") != "amaru" or not _COMMIT.fullmatch(str(key[0]))
                or not _TREE.fullmatch(str(key[1]))
                or staged_candidate.get("repository_commit") != key[0]
                or staged_candidate.get("source_tree_digest") != key[1]):
            raise ValueError("staged candidate differs from discovery")
        stage_variants = staged_candidate.get("variants") or [{
            "mode": "stock", "status": "built", "lane": staged_candidate.get("lane"),
            "runtime_image_name": staged_candidate.get("runtime_image_name"),
            "runtime_image_config_id": staged_candidate.get("runtime_image_config_id"),
            "files": staged_candidate.get("files"),
        }]
        observations, registrations, versions = [], [], set()
        for staged_variant in stage_variants:
            mode = staged_variant.get("mode")
            if mode not in {"stock", "patched"}:
                raise ValueError("staged candidate has an unknown build mode")
            if staged_variant.get("status") != "built":
                observations.append({"mode": mode, "status": staged_variant.get("status"),
                                     "reason": staged_variant.get("reason") or "unspecified"})
                continue
            lane = stock_build / str(staged_variant.get("lane", ""))
            receipt = _regular_json(lane / "receipt.json")
            metadata_path, executable_path = lane / "fetch-metadata.json", lane / "executable"
            for name, path in (("fetch-metadata.json", metadata_path), ("executable", executable_path)):
                expected = staged_variant.get("files", {}).get(name)
                if (path.is_symlink() or not path.is_file() or expected != _sha256(path)
                        or receipt.get("files", {}).get(name) != expected):
                    raise ValueError(f"staged {mode} candidate artifact changed: {name}")
            expected_kind = f"amaru-network-{mode}-build"
            if (receipt.get("kind") != expected_kind or receipt.get("repository_commit") != key[0]
                    or receipt.get("source_tree_digest") != key[1]
                    or receipt.get("runtime_image_name") != staged_variant.get("runtime_image_name")):
                raise ValueError(f"{mode} receipt differs from staged candidate")
            config_id = staged_variant.get("runtime_image_config_id")
            if not _DIGEST.fullmatch(str(config_id)):
                raise ValueError(f"staged {mode} candidate has no exact image config identity")
            versions.add(_amaru_version(_regular_json(metadata_path)))
            executable_digest = "sha256:" + _sha256(executable_path)
            registered = _registered_image(receipt["runtime_image_name"], key[1], config_id,
                                           mode=mode, run=run)
            registered["executable_digest"] = executable_digest
            if mode == "patched":
                patch_set = staged_variant.get("patch_set_digest")
                if patch_set != receipt.get("patch_set_digest") or not _DIGEST.fullmatch(str(patch_set)):
                    raise ValueError("staged patched candidate has no exact patch identity")
                registered["patch_set_digest"] = patch_set
            registrations.append(registered)
            observations.append({"mode": mode, "status": "built",
                                 "patch_set_digest": registered.get("patch_set_digest"),
                                 "artifacts": {"image_digest": registered["manifest_digest"],
                                               "binary_digest": executable_digest,
                                               "executable_digest": executable_digest},
                                 "runtime_image_config_id": registered["config_image_id"]})
        if len(versions) != 1:
            raise ValueError("stock and patched builds report different Amaru versions")
        version = next(iter(versions))
        decision = select_primary_mode(observations)
        registrations.sort(key=lambda row: (row["mode"] != decision["primary_mode"], row["mode"]))

        base_catalog = load_version_catalog(catalog_path)
        cardano = resolve_default(base_catalog, "cardano-only")["release"]
        catalog = _campaign_catalog(base_catalog, version=version, commit=key[0], images=registrations)
        catalog_file = output / "version-catalog.json"
        _save(catalog_file, catalog)
        selection = {"entry_ids": list(INITIAL_SECURITY_ENTRIES), "implementations": {
            "amaru": {"kind": "candidate", "version": version},
            "cardano-node": {"kind": "release", "version": cardano["version"]},
        }}
        _save(output / "selection.json", selection)
        stock_lane = next(row for row in stage_variants if row.get("mode") == "stock" and row.get("status") == "built")
        snapshot = CandidateSnapshot("amaru", candidate["candidate_name"], key[0], key[1],
                                     stock_build / stock_lane["lane"])
        security_suite = _security_suite_for_mode(
            load_security_campaign_suite(ROOT / "dwarf/security-suites/github-security-regression-v1.yaml"),
            "amaru", decision["primary_mode"],
        )
        manifest = resolve_security_campaign(
            snapshot,
            security_suite,
            load_measurement_suite(ROOT / "dwarf/qualification-suites/node-measurements-v1.yaml"),
            selection=selection, catalog=catalog,
        )
        manifest, deferred_measurements = _hosted_measurement_campaign(
            manifest, primary_mode=decision["primary_mode"])
        builds = []
        for row in observations:
            if row.get("status") != "built":
                continue
            build = {"mode": row["mode"], "source": manifest["candidate"],
                     "artifacts": row["artifacts"],
                     "runtime_image_config_id": row["runtime_image_config_id"]}
            if row.get("patch_set_digest"):
                build["patch_set_digest"] = row["patch_set_digest"]
            builds.append(build)
        _save(output / "manifest.json", manifest)
        _save(output / "build-observations.json", builds)
        _save(output / "candidate-registration.json", {"images": registrations,
                                                         "execution_decision": decision})
        files = {name: _sha256(output / name) for name in (
            "version-catalog.json", "selection.json", "manifest.json",
            "build-observations.json", "candidate-registration.json")}
        status.update(status="planned", reason="runtime-compatibility-qualification-pending",
                      candidate_version=version, supporting_cardano_version=cardano["version"],
                      selected_entry_ids=list(INITIAL_SECURITY_ENTRIES),
                      execution_decision=decision,
                      measurement_modes=sorted({mode for cell in manifest["required_tap_cells"]
                                                for mode in cell["target_modes"]}),
                      required_measurement_ids=sorted({cell["measurement_id"] for cell in manifest["required_tap_cells"]}),
                      deferred_measurement_ids=deferred_measurements, files=files)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        status.update(status="error", reason="hosted-campaign-plan-invalid", detail=str(exc)[:2048])
    _save(output / "status.json", status)
    return status


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--discovery", type=Path, required=True)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--stock-build", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, default=ROOT / "dwarf/versions/catalog.json")
    args = parser.parse_args(argv)
    result = plan_hosted_campaign(args.discovery, args.stage, args.stock_build, args.directory,
                                  catalog_path=args.catalog)
    print(result["reason"])
    return 0 if result["status"] == "planned" else 1


if __name__ == "__main__":
    raise SystemExit(main())
