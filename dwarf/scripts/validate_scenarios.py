#!/usr/bin/env python3
"""DWARF CI validation gate.

Wallet-free, infra-free checks intended to run on every push/PR:

  1. SCHEMA     — every scenario in dwarf/scenarios/*.yaml validates against
                  dwarf/spec/v1/schema.json.
  2. SEMANTIC   — every scenario passes `scenario validate --semantic`
                  (all referenced primitives exist in the registry).
  3. ANTITHESIS — every profile renders into a well-formed Antithesis bundle
                  offline (no docker daemon, no registry push).
  4. PROVENANCE — every node version label agrees with the commit it names, per
                  dwarf/versions/catalog.json: structured {version,
                  source_revision} pairs, prose "amaru 0903 (ea1f34e4)" pairs,
                  amaru-YYYYMMDD tokens in scenario/profile ids, and pinned node
                  images in compose files / Dockerfiles.

Nothing here spins up a devnet, a fuzzer, or an Antithesis run — those are the
separate "full runs" gate (they need a self-hosted runner / wallet).

Exit code: 0 if all checks pass, 1 otherwise. With --strict, SEMANTIC and
PROVENANCE warnings are also treated as failures.

Usage:
    python3 dwarf/scripts/validate_scenarios.py [--strict] [--json]
Run from the repo root or anywhere; paths are resolved relative to this file.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# ---- locate the DWARF python package (dwarf/) regardless of cwd -------------
DWARF_DIR = Path(__file__).resolve().parents[1]          # .../dwarf
REPO_ROOT = DWARF_DIR.parent                              # repo root
SCENARIO_DIR = DWARF_DIR / "scenarios"
SCHEMA_PATH = DWARF_DIR / "spec" / "v1" / "schema.json"
# Checked-in, submission-ready MOOG assets. Experimental and reference bundles
# are intentionally excluded until their README documents a MOOG launch path.
MOOG_ASSET_DIRS = (
    REPO_ROOT / "antithesis" / "cardano_amaru_adversarial",
    REPO_ROOT / "antithesis" / "cardano_node_dwarf",
)
if str(DWARF_DIR) not in sys.path:
    sys.path.insert(0, str(DWARF_DIR))


def _iter_scenarios() -> list[Path]:
    return sorted(SCENARIO_DIR.glob("*.yaml"))


def check_schema(scenarios: list[Path]) -> tuple[int, list[str]]:
    """jsonschema-validate every scenario. Returns (fail_count, messages)."""
    import jsonschema

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.Draft7Validator(schema)
    fails: list[str] = []
    for path in scenarios:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            fails.append(f"{path.name}: not valid JSON — {exc}")
            continue
        errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.path))
        for err in errors:
            loc = ".".join(str(p) for p in err.path) or "<root>"
            fails.append(f"{path.name}: schema: {loc}: {err.message}")
    return len(fails), fails


def check_semantic(scenarios: list[Path]) -> tuple[int, int, list[str]]:
    """Run `scenario validate --semantic` in-process for each scenario.

    Returns (fail_count, warn_count, fail_messages).
    """
    from profile_manager import cli

    fails: list[str] = []
    warn_count = 0
    for path in scenarios:
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                sys.argv = ["cli", "scenario", "validate", str(path), "--semantic"]
                rc = cli.main()
        except SystemExit as exc:
            rc = exc.code
        except Exception as exc:  # noqa: BLE001 - report, don't crash the gate
            rc = 99
            buf.write(f"FAIL: {type(exc).__name__}: {exc}")
        out = buf.getvalue()
        if rc not in (0, None):
            for line in out.splitlines():
                if line.startswith("FAIL"):
                    fails.append(f"{path.name}: {line}")
            if not any(f.startswith(path.name) for f in fails):
                fails.append(f"{path.name}: semantic validate exited {rc}")
        elif "WARN:" in out:
            warn_count += 1
    return len(fails), warn_count, fails


def check_antithesis() -> tuple[int, int, list[str]]:
    """Render each profile's Antithesis bundle offline. Returns (fail, ok, msgs)."""
    from profile_manager.profiles import load_profiles
    from profile_manager.antithesis import build_antithesis_bundle

    fails: list[str] = []
    ok = 0
    try:
        profiles = list(load_profiles())
    except Exception as exc:  # noqa: BLE001
        return 1, 0, [f"could not load profiles: {exc}"]
    for prof in profiles:
        with tempfile.TemporaryDirectory() as tmp:
            try:
                written = build_antithesis_bundle(prof, tmp)
            except Exception as exc:  # noqa: BLE001
                fails.append(f"{prof.id}: antithesis build raised {type(exc).__name__}: {exc}")
                continue
            required = {"config/docker-compose.yaml", "test/parallel_driver.sh"}
            missing = required - set(written)
            if missing:
                fails.append(f"{prof.id}: bundle missing {sorted(missing)}")
            else:
                ok += 1
    return len(fails), ok, fails


def check_moog_assets() -> tuple[int, int, list[str]]:
    """Validate fault exclusions in every documented MOOG launch asset."""
    from profile_manager.antithesis_validation import moog_fault_exclusion_errors

    fails: list[str] = []
    ok = 0
    for asset_dir in MOOG_ASSET_DIRS:
        compose_path = asset_dir / "docker-compose.yaml"
        display_dir = (
            asset_dir.relative_to(REPO_ROOT)
            if asset_dir.is_relative_to(REPO_ROOT)
            else asset_dir
        )
        if not compose_path.is_file():
            fails.append(f"{display_dir}: missing docker-compose.yaml")
            continue
        errors = moog_fault_exclusion_errors(compose_path)
        if errors:
            display_path = (
                compose_path.relative_to(REPO_ROOT)
                if compose_path.is_relative_to(REPO_ROOT)
                else compose_path
            )
            fails.extend(f"{display_path}: {error}" for error in errors)
        else:
            ok += 1
    return len(fails), ok, fails


def check_harness_prereqs(scenarios: list[Path]) -> tuple[int, int, list[str]]:
    """Flag scenarios whose AFL coverage harness isn't provisioned on this host.

    Informational, NOT a hard failure: these scenarios skip cleanly at run time
    (the runtime_aflpp_campaign primitive emits a "harness not provisioned" skip
    rather than failing). This surfaces, in a fresh checkout, exactly which
    scenarios need `delivery/scripts/build-afl-harness.sh` before they can run.

    Returns (unprovisioned_count, provisioned_count, note_messages).
    """
    override = os.environ.get("DWARF_AFL_HARNESS")
    needs: list[str] = []
    provisioned = 0
    for path in scenarios:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for step in doc.get("load", []) or []:
            if step.get("primitive") != "runtime_aflpp_campaign":
                continue
            baked = step.get("target_binary_path")
            if not baked:
                continue
            effective = override or baked
            resolved = Path(os.path.expanduser(os.path.expandvars(str(effective))))
            if resolved.exists():
                provisioned += 1
            else:
                needs.append(
                    f"{path.name}: AFL harness not provisioned ({effective}) — "
                    f"run delivery/scripts/build-afl-harness.sh (or set DWARF_AFL_HARNESS)"
                )
            break
    return len(needs), provisioned, needs


PROVENANCE_TEXT_SUFFIXES = {".md", ".json", ".yaml", ".yml", ".txt", ".sh", ".py", ".rst"}
# The catalog is the truth source itself; tests carry deliberate mislabels.
PROVENANCE_EXCLUDE_PREFIXES = ("dwarf/versions/", "tests/")
PROVENANCE_MAX_BYTES = 3_000_000


def _provenance_files(root: Path) -> list[Path]:
    """Tracked text files (git ls-files), falling back to a filesystem walk."""
    try:
        listed = subprocess.run(
            ["git", "-C", str(root), "ls-files"],
            capture_output=True, text=True, check=True, timeout=60,
        ).stdout.splitlines()
        candidates = [root / rel for rel in listed]
    except (OSError, subprocess.SubprocessError):
        candidates = [
            path for path in root.rglob("*")
            if path.is_file() and ".git" not in path.parts and "dist-newstyle" not in path.parts
        ]
    selected = []
    for path in candidates:
        rel = path.relative_to(root).as_posix()
        if rel.startswith(PROVENANCE_EXCLUDE_PREFIXES):
            continue
        if path.suffix in PROVENANCE_TEXT_SUFFIXES or path.name == "Dockerfile":
            selected.append(path)
    return selected


def check_version_provenance(
    root: Path = REPO_ROOT, catalog_path: Path | None = None
) -> tuple[int, int, list[str], list[str]]:
    """A version label must agree with the commit it names.

    Returns (fail_count, warn_count, fail_msgs, warn_msgs).  Failures are the
    historical mislabel class: a label that resolves to catalog release X next
    to (or paired with) a revision that is not X's source revision.  Warnings
    are pinned node images the catalog has no binary identity for.
    """
    import yaml
    from profile_manager.version_provenance import (
        ProvenanceIndex, check_id_labels, check_image_refs, check_prose, check_structured, check_submit_api_refs,
    )

    try:
        index = ProvenanceIndex.load(catalog_path) if catalog_path else ProvenanceIndex.load()
    except Exception as exc:  # noqa: BLE001
        return 1, 0, [f"version catalog invalid: {exc}"], []
    fails: list[str] = []
    warns: list[str] = []
    for path in _provenance_files(root):
        try:
            if path.stat().st_size > PROVENANCE_MAX_BYTES:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = path.relative_to(root).as_posix()
        fails.extend(f"{rel}:{line}: provenance: {msg}" for line, msg in check_prose(index, text))
        if path.suffix in {".json", ".yaml", ".yml"}:
            try:
                document = yaml.safe_load(text)
            except yaml.YAMLError:
                document = None
            fails.extend(f"{rel}: provenance: {msg}" for msg in check_structured(index, document))
            fails.extend(f"{rel}: provenance: {msg}" for msg in check_id_labels(index, document))
        if "compose" in path.name or path.name == "Dockerfile":
            image_fails, image_warns = check_image_refs(index, text)
            sub_fails, sub_warns = check_submit_api_refs(index, text)
            image_fails = list(image_fails) + list(sub_fails)
            image_warns = list(image_warns) + list(sub_warns)
            fails.extend(f"{rel}:{line}: provenance: {msg}" for line, msg in image_fails)
            warns.extend(f"{rel}:{line}: provenance: {msg}" for line, msg in image_warns)
    return len(fails), len(warns), fails, warns


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="DWARF CI validation gate")
    ap.add_argument("--strict", action="store_true",
                    help="treat semantic WARNs as failures")
    ap.add_argument("--json", action="store_true", help="emit a JSON summary to stdout")
    ap.add_argument("--report", metavar="PATH",
                    help="also write the JSON summary to PATH (independent of console format)")
    args = ap.parse_args(argv)

    scenarios = _iter_scenarios()
    schema_fail, schema_msgs = check_schema(scenarios)
    sem_fail, sem_warn, sem_msgs = check_semantic(scenarios)
    ant_fail, ant_ok, ant_msgs = check_antithesis()
    moog_fail, moog_ok, moog_msgs = check_moog_assets()
    harness_needs, harness_ok, harness_msgs = check_harness_prereqs(scenarios)
    prov_fail, prov_warn, prov_msgs, prov_warn_msgs = check_version_provenance()

    all_msgs = schema_msgs + sem_msgs + ant_msgs + moog_msgs + prov_msgs
    hard_fail = schema_fail + sem_fail + ant_fail + moog_fail + prov_fail
    failed = hard_fail > 0 or (args.strict and (sem_warn > 0 or prov_warn > 0))
    if args.strict:
        all_msgs += prov_warn_msgs

    summary = {
        "scenarios": len(scenarios),
        "schema_failures": schema_fail,
        "semantic_failures": sem_fail,
        "semantic_warnings": sem_warn,
        "antithesis_profiles_ok": ant_ok,
        "antithesis_failures": ant_fail,
        "moog_assets_ok": moog_ok,
        "moog_asset_failures": moog_fail,
        "provenance_failures": prov_fail,
        "provenance_warnings": prov_warn,
        # Informational: harness-dependent scenarios that would skip on this host.
        "harness_provisioned": harness_ok,
        "harness_unprovisioned": harness_needs,
        "strict": args.strict,
        "passed": not failed,
    }

    notes = harness_msgs + ([] if args.strict else prov_warn_msgs)
    report = {"summary": summary, "messages": all_msgs, "notes": notes}
    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("=" * 60)
        print("DWARF validation gate")
        print("=" * 60)
        print(f"  scenarios checked      : {len(scenarios)}")
        print(f"  schema failures        : {schema_fail}")
        print(f"  semantic failures      : {sem_fail}")
        print(f"  semantic warnings      : {sem_warn}"
              + ("  (fatal: --strict)" if args.strict else ""))
        print(f"  antithesis profiles ok : {ant_ok}")
        print(f"  antithesis failures    : {ant_fail}")
        print(f"  moog assets ok         : {moog_ok}")
        print(f"  moog asset failures    : {moog_fail}")
        print(f"  provenance failures    : {prov_fail}")
        print(f"  provenance warnings    : {prov_warn}"
              + ("  (fatal: --strict)" if args.strict else ""))
        print(f"  aflpp harness ready    : {harness_ok}")
        print(f"  aflpp needs harness    : {harness_needs}"
              + ("  (skips at run time; build-afl-harness.sh)" if harness_needs else ""))
        if all_msgs:
            print("-" * 60)
            for m in all_msgs:
                print(f"  ✗ {m}")
        if notes:
            print("-" * 60)
            for m in notes:
                print(f"  ℹ {m}")
        print("=" * 60)
        print("RESULT:", "PASS ✅" if not failed else "FAIL ❌")

    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
