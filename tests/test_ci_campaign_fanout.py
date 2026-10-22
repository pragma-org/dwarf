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


def test_stock_solo_scope_keeps_exactly_one_stock_measurement_per_implementation():
    inventory = {
        "schema_version": "v1",
        "kind": "campaign-work-item-inventory",
        "campaign_digest": "sha256:" + "a" * 64,
        "items": [
            {"item_id": "amaru", "kind": "measurement", "phase": "amaru-solo",
             "implementation": "amaru", "mode": "stock", "measurement_ids": [
                 "amaru-external-restart-readiness", "amaru-stock-resources",
             ]},
            {"item_id": "cardano", "kind": "measurement", "phase": "cardano-solo",
             "implementation": "cardano-node", "mode": "stock", "measurement_ids": [
                 "cardano-external-restart-readiness", "cardano-stock-resources",
             ]},
            {"item_id": "mixed", "kind": "security", "phase": "mixed"},
        ],
        "blockers": [{"measurement_id": "deferred-full-campaign-gap"}],
    }

    scoped = api.scope_execution_inventory(inventory, "stock-solo")

    assert [row["item_id"] for row in scoped["items"]] == ["amaru", "cardano"]
    assert [row["measurement_ids"] for row in scoped["items"]] == [
        ["amaru-stock-resources"], ["cardano-stock-resources"]
    ]
    assert scoped["blockers"] == []
    assert scoped["deferred_full_campaign_blockers"] == inventory["blockers"]
    assert scoped["deferred_full_campaign_measurements"] == [
        "amaru-external-restart-readiness",
        "cardano-external-restart-readiness",
    ]
    assert scoped["scope"] == "stock-solo"
    assert scoped["claim"] == "hosted-stock-execution-and-portable-evidence"
    assert scoped["inventory_digest"] == api.inventory_digest(scoped)


def test_stock_solo_status_is_scoped_complete_without_security_claim():
    inventory = {
        "scope": "stock-solo",
        "items": [
            {"kind": "measurement", "phase": "amaru-solo", "implementation": "amaru", "mode": "stock"},
            {"kind": "measurement", "phase": "cardano-solo", "implementation": "cardano-node", "mode": "stock"},
        ],
        "blockers": [],
        "deferred_full_campaign_blockers": [{"measurement_id": "later"}],
    }

    result = api.stock_solo_proof_status(
        inventory,
        item_success=True,
        readiness_passed=True,
        cleanup_errors=[],
    )

    assert result["status"] == "complete"
    assert result["measurement_completeness"] == "scoped-complete"
    assert result["security_result"] == "not-run"
    assert result["full_campaign_deferred"] is True
    assert result["deferred_full_campaign_blockers"] == [{"measurement_id": "later"}]


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


def test_rebase_native_export_ignores_other_signature_copy(tmp_path):
    import hashlib
    from pathlib import Path

    lane = tmp_path / 'shard'
    runtime = lane / 'campaign-runtime'
    run_id = '20261022T053847Z-73c9f65b'
    export = runtime / 'runs' / run_id / 'outputs' / 'export'
    export.mkdir(parents=True)
    other = export.parent / 'signature'
    other.mkdir()
    (other / 'signature.json').write_bytes(b'other signature')
    receipt = {'run_id': run_id}
    for field, name in [('archive', run_id + '-bundle-export.tar.gz'), ('signature', 'signature.json')]:
        payload = field.encode()
        (export / name).write_bytes(payload)
        receipt[field] = '/old/job/campaign-runtime/runs/' + run_id + '/outputs/export/' + name
        receipt[field + '_sha256'] = hashlib.sha256(payload).hexdigest()
    state = {'cells': {'cell': {'archives': [receipt]}}}
    api._rebase_receipts(state, runtime / 'primary/state.json', tmp_path / 'aggregate')
    assert Path(receipt['signature']).read_bytes() == b'signature'


@pytest.mark.parametrize('problem', ['tampered', 'symlink'])
def test_rebase_native_export_rejects_invalid_source(tmp_path, problem):
    import hashlib

    runtime = tmp_path / 'shard/campaign-runtime'
    run_id = 'run-example'
    export = runtime / 'runs' / run_id / 'outputs/export'
    export.mkdir(parents=True)
    name = run_id + '-bundle-export.tar.gz'
    if problem == 'symlink':
        outside = tmp_path / 'outside'
        outside.write_bytes(b'expected')
        (export / name).symlink_to(outside)
    else:
        (export / name).write_bytes(b'tampered')
    receipt = {'run_id': run_id, 'archive': '/old/runs/' + run_id + '/outputs/export/' + name,
               'archive_sha256': hashlib.sha256(b'expected').hexdigest()}
    with pytest.raises(ValueError):
        api._rebase_receipts({'cells': {'cell': {'archives': [receipt]}}}, runtime / 'primary/state.json', tmp_path / 'aggregate')


@pytest.mark.parametrize("scope,selected_id,other_index", [
    ("stock-amaru", "amaru", 1),
    ("stock-cardano", "cardano", 0),
])
def test_single_stock_scope_completes_without_other_node_and_rejects_extra_items(scope, selected_id, other_index):
    inventory = {
        "kind": "campaign-work-item-inventory", "campaign_digest": "sha256:" + "a" * 64,
        "items": [
            {"item_id": "amaru", "kind": "measurement", "phase": "amaru-solo",
             "implementation": "amaru", "mode": "stock", "measurement_ids": ["amaru-stock-resources"]},
            {"item_id": "cardano", "kind": "measurement", "phase": "cardano-solo",
             "implementation": "cardano-node", "mode": "stock", "measurement_ids": ["cardano-stock-resources"]},
            {"item_id": "mixed", "kind": "security", "phase": "mixed"},
        ],
        "blockers": [{"measurement_id": "not-exercised-by-this-proof"}],
    }
    scoped = api.scope_execution_inventory(inventory, scope)
    assert [row["item_id"] for row in scoped["items"]] == [selected_id]
    assert scoped["inventory_digest"] == api.inventory_digest(scoped)
    assert scoped["deferred_full_campaign_blockers"] == inventory["blockers"]
    result = api.stock_solo_proof_status(scoped, item_success=True, readiness_passed=True, cleanup_errors=[])
    assert result["status"] == "complete"
    assert result["proof_scope"] == scope
    assert result["measurement_completeness"] == "scoped-complete"
    assert result["security_result"] == "not-run"
    for overrides in ({"item_success": False}, {"readiness_passed": False}, {"cleanup_errors": ["residue"]}):
        arguments = dict(item_success=True, readiness_passed=True, cleanup_errors=[])
        arguments.update(overrides)
        assert api.stock_solo_proof_status(scoped, **arguments)["status"] == "incomplete"
    scoped["items"].append(inventory["items"][other_index])
    assert api.stock_solo_proof_status(scoped, item_success=True, readiness_passed=True, cleanup_errors=[])["status"] == "incomplete"
