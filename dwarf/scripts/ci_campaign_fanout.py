#!/usr/bin/env python3
"""Prepare, select, and later aggregate isolated hosted campaign shards."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil

from profile_manager.campaign_work_items import (
    calibration_work_items,
    execution_work_items,
    phase_work_items,
    validate_work_item,
)
from profile_manager.forensic import atomic_write_bytes
from scripts.ci_security_campaign import decide_patched_primary, work_item_succeeded
from scripts.ci_security_campaign import _digest as checkpoint_digest
from scripts.ci_security_campaign import _evaluate, _save


def _write(path: Path, document) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(
        path,
        (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode(),
    )


def _matrix(items):
    return {"include": [{"shard": item["shard"]} for item in items]}


def _inventory(kind, campaign_digest, items, *, blockers=None):
    from profile_manager.campaign_work_items import _digest

    body = {
        "schema_version": "v1",
        "kind": kind,
        "campaign_digest": campaign_digest,
        "items": deepcopy(items),
        "blockers": deepcopy(blockers or []),
    }
    body["inventory_digest"] = _digest(
        {"items": body["items"], "blockers": body["blockers"]}
    )
    return body


def _emit_items(directory: Path, inventory: dict, matrix_name: str) -> None:
    item_root = directory / "work-items"
    for item in inventory["items"]:
        _write(item_root / f"{item['shard']}.json", item)
    _write(directory / matrix_name, _matrix(inventory["items"]))


def prepare_fanout(primary_manifest, stock_manifest, directory):
    """Freeze calibration items before any hosted workload executes."""

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    items = calibration_work_items(primary_manifest, stock_manifest)
    inventory = _inventory(
        "campaign-calibration-work-item-inventory",
        primary_manifest["campaign_digest"],
        items,
    )
    _write(directory / "manifest.json", primary_manifest)
    _write(directory / "stock-manifest.json", stock_manifest)
    _write(directory / "calibration-work-items.json", inventory)
    _emit_items(directory, inventory, "calibration-matrix.json")
    status = {"status": "prepared", "items": items, "inventory_digest": inventory["inventory_digest"]}
    _write(directory / "fanout-status.json", status)
    return status


def _state_for(items, states, item_id):
    expected = next((row for row in items if row["item_id"] == item_id), None)
    if expected is None:
        return None
    matches = []
    for state in states:
        item = state.get("work_item") if isinstance(state, dict) else None
        if isinstance(item, dict) and item.get("item_id") == item_id:
            validate_work_item(item, items)
            if state.get("campaign_digest") != item["campaign_digest"]:
                raise ValueError("calibration shard belongs to another campaign")
            matches.append(state)
    if len(matches) > 1:
        raise ValueError(f"duplicate calibration shard: {item_id}")
    return matches[0] if matches else None


def _calibration_result(state, mode):
    if state is None:
        return {"status": "unavailable", "reason": f"{mode} calibration shard is missing"}
    return deepcopy(
        state.get("cells", {})
        .get(f"calibration:{mode}", {})
        .get("calibration_result", {"status": "unavailable", "reason": f"{mode} calibration did not retain a result"})
    )


def select_execution(primary_manifest, stock_manifest, builds, calibration_states, directory):
    """Select stock/patched once, then emit the executable workload matrix."""

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    calibration_items = calibration_work_items(primary_manifest, stock_manifest)
    stock_state = _state_for(calibration_items, calibration_states, "calibration:stock")
    stock_item = next(row for row in calibration_items if row["item_id"] == "calibration:stock")
    if stock_state is None or not work_item_succeeded(stock_state, stock_item):
        raise ValueError("stock calibration did not complete successfully")
    patched_item = next((row for row in calibration_items if row["item_id"] == "calibration:patched"), None)
    if patched_item is None:
        decision = {
            "status": "passed",
            "selected_mode": "stock",
            "reason": "patched-variant-not-selected-by-frozen-plan",
            "retry_build": False,
        }
    else:
        patched = next((row for row in builds if row.get("mode") == "patched"), None)
        if patched is None:
            raise ValueError("patched calibration plan has no patched build observation")
        patched_state = _state_for(calibration_items, calibration_states, "calibration:patched")
        decision = decide_patched_primary(
            _calibration_result(stock_state, "stock"),
            _calibration_result(patched_state, "patched"),
            expected_source_revision=primary_manifest["candidate"]["repository_commit"],
            expected_patch_set_sha256=patched.get("patch_set_digest"),
        )
    selected_mode = decision["selected_mode"]
    selected = primary_manifest if selected_mode == "patched" else stock_manifest
    replay = selected_mode == "patched"
    inventory = execution_work_items(
        selected,
        stock_manifest=stock_manifest if replay else None,
        stock_replay_required=replay,
    )
    _write(directory / "selected-manifest.json", selected)
    _write(directory / "manifest.json", primary_manifest)
    _write(directory / "stock-manifest.json", stock_manifest)
    _write(directory / "execution-decision.json", decision)
    _write(directory / "execution-work-items.json", inventory)
    _emit_items(directory, inventory, "execution-matrix.json")
    phases = phase_work_items(inventory)
    for phase, items in phases.items():
        _write(directory / f"{phase}-matrix.json", _matrix(items))
    status = {
        "status": "selected",
        "selected_mode": selected_mode,
        "reason": decision["reason"],
        "stock_replay_required": replay,
        "work_item_count": len(inventory["items"]),
        "phase_work_item_counts": {
            phase: len(items) for phase, items in phases.items()
        },
        "blocker_count": len(inventory["blockers"]),
        "inventory_digest": inventory["inventory_digest"],
    }
    _write(directory / "fanout-status.json", status)
    return status


def _shard_root(state_path: Path) -> Path:
    parts = state_path.parts
    if "campaign-runtime" in parts:
        index = parts.index("campaign-runtime")
        return Path(*parts[:index])
    return state_path.parent


def _rebase_receipts(state, state_path: Path, destination: Path) -> None:
    source_root = _shard_root(state_path)
    for record in state.get("cells", {}).values():
        for receipt in record.get("archives", []):
            run_id = str(receipt.get("run_id") or "")
            if not run_id:
                raise ValueError("shard archive receipt has no run ID")
            export = destination / "runs" / run_id / "outputs" / "export"
            export.mkdir(parents=True, exist_ok=True)
            for field in ("archive", "signature"):
                name = Path(str(receipt.get(field) or "")).name
                matches = [
                    path
                    for path in source_root.rglob(name)
                    if path.is_file()
                    and not path.is_symlink()
                    and (run_id in path.parts or run_id in path.name)
                ]
                if len(matches) != 1:
                    raise ValueError(f"shard receipt file is missing or ambiguous: {name}")
                target = export / name
                shutil.copyfile(matches[0], target)
                import hashlib

                if hashlib.sha256(target.read_bytes()).hexdigest() != receipt.get(field + "_sha256"):
                    raise ValueError(f"shard receipt digest mismatch: {name}")
                receipt[field] = str(target)


def aggregate_execution(
    selected_manifest,
    stock_manifest,
    inventory,
    state_paths,
    directory,
):
    """Verify every assigned shard and merge it into the publication layout."""

    from profile_manager.campaign_evidence import evaluate_stock_replay_authority
    from profile_manager.campaign_work_items import _digest as inventory_digest

    if inventory.get("inventory_digest") != inventory_digest(
        {"items": inventory.get("items", []), "blockers": inventory.get("blockers", [])}
    ):
        raise ValueError("execution work-item inventory digest mismatch")
    allowed = inventory.get("items", [])
    expected = {item["work_item_digest"]: item for item in allowed}
    observed = {}
    builds = None
    directory = Path(directory)
    primary_state = {
        "schema_version": "v1",
        "campaign_digest": selected_manifest["campaign_digest"],
        "phase": "full",
        "builds": [],
        "readiness": {"passed": True, "executed": True},
        "cells": {},
        "cleanup_errors": [],
    }
    stock_state = {
        "schema_version": "v1",
        "campaign_digest": stock_manifest["campaign_digest"],
        "phase": "security-replay",
        "builds": [],
        "readiness": {"passed": True, "executed": True},
        "cells": {},
        "cleanup_errors": [],
    }
    item_success = True
    retained_run_ids = set()
    for path in [Path(value) for value in state_paths]:
        state = _load(path)
        retained = state.get("checkpoint_digest")
        if retained != checkpoint_digest({key: value for key, value in state.items() if key != "checkpoint_digest"}):
            raise ValueError("execution shard checkpoint digest mismatch")
        item = validate_work_item(state.get("work_item"), allowed)
        digest = item["work_item_digest"]
        if digest in observed:
            raise ValueError(f"duplicate execution shard: {item['item_id']}")
        observed[digest] = path
        if state.get("campaign_digest") != item["campaign_digest"]:
            raise ValueError("execution shard campaign identity mismatch")
        if builds is None:
            builds = deepcopy(state.get("builds"))
        elif state.get("builds") != builds:
            raise ValueError("execution shards contain different build observations")
        item_success = work_item_succeeded(state, item) and item_success
        target = stock_state if item["kind"] == "stock-replay" else primary_state
        if state.get("readiness", {}).get("passed") is not True:
            target["readiness"] = deepcopy(state.get("readiness", {}))
        target["cleanup_errors"].extend(state.get("cleanup_errors", []))
        copied = deepcopy(state)
        _rebase_receipts(copied, path, directory)
        for key, record in copied.get("cells", {}).items():
            if key in target["cells"]:
                raise ValueError(f"conflicting merged campaign cell: {key}")
            for receipt in record.get("archives", []):
                run_id = receipt["run_id"]
                if run_id in retained_run_ids:
                    raise ValueError(f"duplicate retained run ID across shards: {run_id}")
                retained_run_ids.add(run_id)
            target["cells"][key] = record
    missing = sorted(item["item_id"] for digest, item in expected.items() if digest not in observed)
    if missing:
        raise ValueError("missing execution shards: " + ", ".join(missing))
    primary_state["builds"] = deepcopy(builds or [])
    stock_state["builds"] = deepcopy(builds or [])
    _evaluate(selected_manifest, primary_state)
    replay_items = [item for item in allowed if item["kind"] == "stock-replay"]
    if replay_items:
        _evaluate(stock_manifest, stock_state)
        authority = evaluate_stock_replay_authority(
            selected_manifest,
            primary_state["evaluation"],
            primary_state,
            stock_manifest,
            stock_state["evaluation"],
            stock_state,
        )
        primary_state["evaluation"]["stock_replay_authority"] = authority
        from profile_manager.security_campaign import campaign_evaluation_status

        primary_state["status"] = campaign_evaluation_status(
            selected_manifest, primary_state["evaluation"]
        )
        primary_state["stock_replay"] = deepcopy(stock_state)
        _save(directory / "stock-replay/state.json", stock_state)
    _write(directory / "selected-manifest.json", selected_manifest)
    _write(directory / "stock-manifest.json", stock_manifest)
    _save(directory / "selected/state.json", primary_state)
    status_complete = (
        item_success
        and not inventory.get("blockers")
        and primary_state["readiness"].get("passed") is True
        and not primary_state["cleanup_errors"]
        and primary_state["status"].get("measurement_completeness") == "complete"
        and primary_state["status"].get("security_result") in {"pass", "finding"}
        and primary_state["status"].get("security_authority")
        in {None, "patched-primary-stock-confirmed", "patched-primary-stock-disagrees"}
    )
    result = {
        "status": "complete" if status_complete else "incomplete",
        "campaign_id": selected_manifest["campaign_id"],
        "campaign_digest": selected_manifest["campaign_digest"],
        "work_item_count": len(allowed),
        "blockers": deepcopy(inventory.get("blockers", [])),
        **primary_state["status"],
    }
    _write(directory / "aggregate-status.json", result)
    return result


def _load(path):
    value = json.loads(Path(path).read_text())
    if not isinstance(value, dict):
        raise ValueError(f"JSON input must be an object: {path}")
    return value


def _load_states(root: Path):
    states = []
    for path in sorted(root.rglob("state.json")):
        value = _load(path)
        if value.get("work_item", {}).get("kind") == "calibration":
            states.append(value)
    return states


def _execution_state_paths(root: Path):
    paths = []
    for path in sorted(root.rglob("state.json")):
        value = _load(path)
        if value.get("work_item", {}).get("kind") in {
            "security", "measurement", "stock-replay"
        }:
            paths.append(path)
    return paths


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    select = commands.add_parser("select")
    aggregate = commands.add_parser("aggregate")
    for command in (prepare, select):
        command.add_argument("--manifest", type=Path, required=True)
        command.add_argument("--stock-manifest", type=Path, required=True)
        command.add_argument("--directory", type=Path, required=True)
    select.add_argument("--build-observations", type=Path, required=True)
    select.add_argument("--calibration-root", type=Path, required=True)
    aggregate.add_argument("--manifest", type=Path, required=True)
    aggregate.add_argument("--stock-manifest", type=Path, required=True)
    aggregate.add_argument("--inventory", type=Path, required=True)
    aggregate.add_argument("--shard-root", type=Path, required=True)
    aggregate.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        primary, stock = _load(args.manifest), _load(args.stock_manifest)
        if args.command == "prepare":
            result = prepare_fanout(primary, stock, args.directory)
        elif args.command == "select":
            builds = json.loads(args.build_observations.read_text())
            if not isinstance(builds, list):
                raise ValueError("build observations must be an array")
            result = select_execution(
                primary,
                stock,
                builds,
                _load_states(args.calibration_root),
                args.directory,
            )
        else:
            result = aggregate_execution(
                primary,
                stock,
                _load(args.inventory),
                _execution_state_paths(args.shard_root),
                args.directory,
            )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0 if result.get("status") != "incomplete" else 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"campaign fan-out: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
