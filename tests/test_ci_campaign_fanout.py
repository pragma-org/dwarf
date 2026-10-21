"""Focused contracts for hosted campaign fan-out planning and selection."""
from copy import deepcopy
import hashlib
import json

import pytest

from profile_manager.security_campaign import digest_security_campaign
from scripts import ci_campaign_fanout as api


def freeze(manifest):
    manifest["campaign_digest"] = digest_security_campaign(manifest)
    manifest["campaign_id"] = "campaign-" + manifest["campaign_digest"].split(":", 1)[1]
    return manifest


def calibration_state(item, *, available=True, mean=100.0):
    mode = item["mode"]
    target = {
        "implementation": "amaru",
        "version": "proposal",
        "source_revision": "a" * 40,
        "mode": mode,
        "image_digest": "sha256:" + ("1" if mode == "stock" else "2") * 64,
    }
    if mode == "patched":
        target["patch_set_sha256"] = "sha256:" + "e" * 64
    result = {
        "status": "available" if available else "unavailable",
        "target": target,
        "workload_identity": {"scenario_id": "calibration", "seed": "0xA11CE",
                              "workload_digest": "sha256:" + "3" * 64, "attempt_count": 40},
        "runner": {"script": "calibration.py", "script_sha256": "sha256:" + "4" * 64},
        "timing_policy": {"clock": "monotonic-perf-counter-ns", "warmup": "none"},
        "hardware": {"architecture": "x86_64", "logical_cpu_count": 4},
        "attempts": {"total": 40, "outcomes": {"rejected": 40}},
        "measurements": {"handshake_rejection_roundtrip": {
            "unit": "us", "sample_count": 40, "mean": mean, "median": mean,
            "p95": mean, "p99": mean,
        }},
    }
    return {
        "schema_version": "v1",
        "campaign_digest": item["campaign_digest"],
        "phase": "calibration",
        "work_item": item,
        "builds": [],
        "readiness": {"passed": True},
        "cells": {f"calibration:{mode}": {
            "status": "complete" if available else "incomplete",
            "calibration_result": result,
            "archives": [{"run_id": f"run-{mode}"}],
        }},
        "cleanup_errors": [],
    }


@pytest.fixture
def manifests():
    from test_campaign_evidence import campaign_plan as fixture

    stock = deepcopy(fixture.__wrapped__())
    patched = deepcopy(stock)
    patched["topologies"][0]["nodes"][0]["identity"]["mode"] = "patched"
    for cell in patched["required_tap_cells"]:
        cell["target_modes"] = ["patched"]
    freeze(patched)
    return patched, stock


def test_prepare_emits_two_calibration_items_and_matrix(manifests, tmp_path):
    patched, stock = manifests

    result = api.prepare_fanout(patched, stock, tmp_path)

    assert result["status"] == "prepared"
    inventory = json.loads((tmp_path / "calibration-work-items.json").read_text())
    assert [row["item_id"] for row in inventory["items"]] == ["calibration:patched", "calibration:stock"]
    assert json.loads((tmp_path / "calibration-matrix.json").read_text()) == {
        "include": [{"shard": row["shard"]} for row in inventory["items"]]
    }
    assert all((tmp_path / "work-items" / f"{row['shard']}.json").is_file() for row in inventory["items"])


def test_selector_uses_patched_after_paired_calibration(manifests, tmp_path):
    patched, stock = manifests
    prepared = tmp_path / "prepared"
    api.prepare_fanout(patched, stock, prepared)
    inventory = json.loads((prepared / "calibration-work-items.json").read_text())
    states = [calibration_state(item, mean=100.0 if item["mode"] == "stock" else 103.0)
              for item in inventory["items"]]
    builds = [{"mode": "patched", "patch_set_digest": "sha256:" + "e" * 64}]

    result = api.select_execution(patched, stock, builds, states, tmp_path / "selected")

    assert result["selected_mode"] == "patched"
    assert result["stock_replay_required"] is True
    selected = json.loads((tmp_path / "selected/execution-work-items.json").read_text())
    assert any(row["kind"] == "stock-replay" for row in selected["items"])
    assert sum(result["phase_work_item_counts"].values()) == len(selected["items"])
    assert result["phase_work_item_counts"]["amaru-solo"]
    assert result["phase_work_item_counts"]["cardano-solo"] == 0
    assert json.loads((tmp_path / "selected/amaru-solo-matrix.json").read_text())["include"]
    assert json.loads((tmp_path / "selected/cardano-solo-matrix.json").read_text()) == {"include": []}
    assert len(json.loads((tmp_path / "selected/mixed-matrix.json").read_text())["include"]) == result["phase_work_item_counts"]["mixed"]


def test_selector_falls_back_to_stock_when_patched_calibration_is_unavailable(manifests, tmp_path):
    patched, stock = manifests
    items = api.prepare_fanout(patched, stock, tmp_path / "prepared")["items"]
    states = [calibration_state(item, available=item["mode"] == "stock") for item in items]
    builds = [{"mode": "patched", "patch_set_digest": "sha256:" + "e" * 64}]

    result = api.select_execution(patched, stock, builds, states, tmp_path / "selected")

    assert result["selected_mode"] == "stock"
    assert result["stock_replay_required"] is False
    selected = json.loads((tmp_path / "selected/selected-manifest.json").read_text())
    assert selected["campaign_digest"] == stock["campaign_digest"]


def test_selector_rejects_failed_stock_calibration(manifests, tmp_path):
    patched, stock = manifests
    items = api.prepare_fanout(patched, stock, tmp_path / "prepared")["items"]
    states = [calibration_state(item, available=False) for item in items]

    with pytest.raises(ValueError, match="stock calibration"):
        api.select_execution(
            patched,
            stock,
            [{"mode": "patched", "patch_set_digest": "sha256:" + "e" * 64}],
            states,
            tmp_path / "selected",
        )


def shard_state(manifest, item, root):
    from test_campaign_evidence import evidence, measurement
    from scripts.ci_security_campaign import _digest, _evaluate

    ev = evidence(manifest)
    if item["kind"] == "security":
        run_id = ev["security"][0]["run_id"]
        key = "security:" + item["entry_id"]
        result = {"security": ev["security"][0], "measurements": []}
    else:
        cell = next(
            row for row in manifest["required_tap_cells"]
            if row["topology_id"] == item["topology_id"]
            and row["node_id"] == item["node_id"]
            and row["scenario_id"] == item["scenario_id"]
            and row["measurement_id"] in item["measurement_ids"]
        )
        measured = measurement(manifest, cell, ev, mode=item["mode"])
        run_id = measured["result"]["run_id"]
        key = "measurement:" + ":".join([
            item["topology_id"], item["node_id"], cell["measurement_id"], item["mode"]
        ])
        result = {"run_id": run_id, "execution_complete": True, "measurements": [measured]}
    lane = root / item["shard"]
    lane.mkdir(parents=True)
    archive = lane / f"{run_id}-bundle-export.tar.gz"
    signature = lane / f"{run_id}-signature.json"
    archive.write_bytes(b"archive:" + run_id.encode())
    signature.write_text("signed:" + run_id)
    receipt = {
        "run_id": run_id,
        "archive": "/stale/runner/path/" + archive.name,
        "signature": "/stale/runner/path/" + signature.name,
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "signature_sha256": hashlib.sha256(signature.read_bytes()).hexdigest(),
    }
    state = {
        "schema_version": "v1",
        "campaign_digest": manifest["campaign_digest"],
        "phase": "full",
        "work_item": item,
        "builds": ev["builds"],
        "readiness": {"passed": True},
        "cells": {key: {
            "status": "complete",
            "attempts": 1,
            "archives": [receipt],
            "result": result,
        }},
        "cleanup_errors": [],
    }
    _evaluate(manifest, state)
    state["checkpoint_digest"] = _digest({key: value for key, value in state.items() if key != "checkpoint_digest"})
    path = lane / "state.json"
    path.write_text(json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n")
    return path


def compact_campaign(manifest):
    manifest = deepcopy(manifest)
    cell = deepcopy(next(
        row for row in manifest["required_tap_cells"]
        if row["measurement_id"] == "amaru-stock-resources"
    ))
    cell["runtime"] = {"status": "implemented"}
    cell["target_modes"] = ["stock"]
    cell["rule"] = {"min_samples": 1, "correlation": ["run_id"]}
    manifest["required_tap_cells"] = [cell]
    return freeze(manifest)


def test_aggregate_merges_complete_shard_and_rebases_archives(manifests, tmp_path):
    _patched, stock = manifests
    selected = compact_campaign(stock)
    inventory = api.execution_work_items(selected)
    item = inventory["items"][0]
    state_paths = [shard_state(selected, item, tmp_path / "shards") for item in inventory["items"]]

    result = api.aggregate_execution(
        selected,
        stock,
        inventory,
        state_paths,
        tmp_path / "aggregate",
    )

    assert result["status"] == "complete"
    combined = json.loads((tmp_path / "aggregate/selected/state.json").read_text())
    assert combined["status"]["security_result"] == "pass"
    assert combined["status"]["measurement_completeness"] == "complete"
    receipt = next(iter(combined["cells"].values()))["archives"][0]
    assert receipt["archive"].startswith(str(tmp_path / "aggregate"))
    assert (tmp_path / "aggregate/selected-manifest.json").is_file()


def test_aggregate_rejects_missing_or_duplicate_shards(manifests, tmp_path):
    _patched, stock = manifests
    selected = compact_campaign(stock)
    inventory = api.execution_work_items(selected)
    item = inventory["items"][0]
    state = shard_state(selected, item, tmp_path / "shards")

    with pytest.raises(ValueError, match="missing execution shards"):
        api.aggregate_execution(selected, stock, inventory, [], tmp_path / "missing")
    with pytest.raises(ValueError, match="duplicate execution shard"):
        api.aggregate_execution(selected, stock, inventory, [state, state], tmp_path / "duplicate")


def test_aggregate_rejects_altered_checkpoint(manifests, tmp_path):
    _patched, stock = manifests
    selected = compact_campaign(stock)
    inventory = api.execution_work_items(selected)
    state_path = shard_state(selected, inventory["items"][0], tmp_path / "shards")
    state = json.loads(state_path.read_text())
    state["readiness"]["passed"] = False
    state_path.write_text(json.dumps(state))

    with pytest.raises(ValueError, match="checkpoint digest"):
        api.aggregate_execution(selected, stock, inventory, [state_path], tmp_path / "aggregate")
