#!/usr/bin/env python3
"""Explicit, idempotent SARIF regeneration for an existing DWARF run."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DWARF_ROOT = SCRIPT_DIR.parent
if str(DWARF_ROOT) not in sys.path:
    sys.path.insert(0, str(DWARF_ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from profile_manager.sarif import (  # noqa: E402,F401
    SARIF_SCHEMA_PATH,
    SARIF_SCHEMA_URI,
    build_sarif_log,
    run_sarif_export,
    validate_sarif,
)
from runtime_telemetry import emit_target_event  # noqa: E402


def infer_runs_dir(explicit_runs_dir: str | None) -> Path | None:
    if explicit_runs_dir:
        return Path(explicit_runs_dir)
    run_dir = os.environ.get("ADA2_DWARF_RUN_DIR")
    if run_dir:
        return Path(run_dir).parent
    configured = os.environ.get("ADA2_DWARF_RUNS_DIR")
    return Path(configured) if configured else None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Regenerate a captured bundle's SARIF v2.1.0 export")
    parser.add_argument("--runs-dir", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--target-run-id", required=True)
    parser.add_argument("--schema-path", default=str(SARIF_SCHEMA_PATH))
    args = parser.parse_args(argv[1:])
    runs_dir = infer_runs_dir(args.runs_dir)
    if runs_dir is None:
        print("missing runs dir", file=sys.stderr)
        return 1
    output_dir = Path(args.output_dir) if args.output_dir else runs_dir / args.target_run_id / "outputs/sarif-export"
    emit_target_event(
        primitive="runtime_bundle_export_sarif",
        event="bundle_export_sarif_started",
        payload={"runs_dir": str(runs_dir), "target_run_id": args.target_run_id, "output_dir": str(output_dir)},
    )
    result = run_sarif_export(
        runs_dir=runs_dir,
        output_dir=output_dir,
        target_run_id=args.target_run_id,
        schema_path=Path(args.schema_path),
        generation="explicit-primitive",
    )
    emit_target_event(
        primitive="runtime_bundle_export_sarif",
        event="bundle_export_sarif_completed",
        payload=result,
    )
    print(
        "target_run_id={target_run_id} schema_valid={schema_valid} sarif_result_count={sarif_result_count} sarif_relpath={sarif_relpath}".format(**result)
    )
    return 0 if result["schema_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
