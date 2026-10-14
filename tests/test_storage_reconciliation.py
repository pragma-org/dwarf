from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
from pathlib import Path

import pytest

from profile_manager import config


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _write_run(
    storage_root: Path,
    run_id: str,
    *,
    previous: dict | None = None,
    payload: bytes = b"evidence\n",
) -> dict:
    run_dir = storage_root / "runs" / run_id
    run_dir.mkdir(parents=True)
    manifest = {
        "run_id": run_id,
        "framework": {"commit": "f" * 40, "version": "test"},
        "scenario": {"id": "storage-test"},
        "exit_status": "pass",
    }
    (run_dir / "manifest.json").write_bytes(_canonical(manifest))
    (run_dir / "evidence.bin").write_bytes(payload)
    chain = {
        "run_id": run_id,
        "manifest_hash": hashlib.sha256(_canonical(manifest)).hexdigest(),
        "prev_hash": (
            hashlib.sha256(_canonical(previous)).hexdigest() if previous else "genesis"
        ),
        "timestamp": "2026-10-08T00:00:00Z",
        "continuity": "linked" if previous else "genesis",
    }
    (run_dir / "chain.json").write_text(json.dumps(chain, indent=2) + "\n")
    return chain


def _roots(root: Path):
    return config.StorageRoots(
        runs=root / "runs",
        state=root / "state",
        bundles=root / "bundles",
        runtime_root=root,
        mode="explicit",
        sources={"runs": "test", "state": "test", "bundles": "test"},
    )


def _tree_bytes(path: Path) -> dict[str, bytes]:
    return {
        str(item.relative_to(path)): item.read_bytes()
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }


def test_storage_roots_use_installed_xdg_default_and_config_follows_state(tmp_path):
    roots = config.resolve_storage_roots(environ={"XDG_DATA_HOME": str(tmp_path)})

    assert roots == config.StorageRoots(
        runs=tmp_path / "dwarf" / "runs",
        state=tmp_path / "dwarf" / "state",
        bundles=tmp_path / "dwarf" / "bundles",
        runtime_root=tmp_path / "dwarf",
        mode="xdg",
        sources={"runs": "xdg", "state": "xdg", "bundles": "xdg"},
    )
    assert config.config_path(environ={"XDG_DATA_HOME": str(tmp_path)}) == roots.state / "config.yaml"


def test_storage_root_precedence_and_partial_environment_fail_closed(tmp_path):
    runtime = tmp_path / "runtime"
    trio = {name: str(tmp_path / name.lower()) for name in (
        "ADA2_DWARF_RUNS_DIR", "ADA2_DWARF_STATE_DIR", "ADA2_DWARF_BUNDLES_DIR"
    )}
    roots = config.resolve_storage_roots(
        environ={**trio, "ADA2_DWARF_RUNTIME_ROOT": str(runtime)}
    )
    assert (roots.runs, roots.state, roots.bundles, roots.mode) == (
        Path(trio["ADA2_DWARF_RUNS_DIR"]),
        Path(trio["ADA2_DWARF_STATE_DIR"]),
        Path(trio["ADA2_DWARF_BUNDLES_DIR"]),
        "configured",
    )

    with pytest.raises(config.StorageConfigurationError, match="all three"):
        config.resolve_storage_roots(
            environ={
                "ADA2_DWARF_RUNS_DIR": str(tmp_path / "split-runs"),
                "ADA2_DWARF_RUNTIME_ROOT": str(runtime),
            }
        )


def test_explicit_paths_override_environment_and_development_is_opt_in(tmp_path):
    configured = str(tmp_path / "configured")
    env = {"ADA2_DWARF_RUNTIME_ROOT": configured}
    roots = config.resolve_storage_roots(
        runs_dir=tmp_path / "explicit-runs", environ=env
    )
    assert roots.runs == tmp_path / "explicit-runs"
    assert roots.state == Path(configured) / "state"
    assert roots.sources["runs"] == "explicit"

    checkout = tmp_path / "checkout" / "dwarf"
    development = config.resolve_storage_roots(
        development=True, checkout_root=checkout, environ={}
    )
    assert development.mode == "development"
    assert development.runs == checkout / "runs"
    assert config.resolve_storage_roots(environ={}).mode == "xdg"


def test_complete_explicit_contract_outranks_partial_environment(tmp_path):
    roots = config.resolve_storage_roots(
        runs_dir=tmp_path / "explicit-runs",
        state_dir=tmp_path / "explicit-state",
        bundles_dir=tmp_path / "explicit-bundles",
        environ={"ADA2_DWARF_RUNS_DIR": str(tmp_path / "ignored")},
    )
    assert roots.mode == "explicit"
    assert roots.runs == tmp_path / "explicit-runs"
    assert roots.state == tmp_path / "explicit-state"
    assert roots.bundles == tmp_path / "explicit-bundles"

    runtime = config.resolve_storage_roots(
        runtime_root=tmp_path / "runtime",
        environ={
            "ADA2_DWARF_RUNS_DIR": str(tmp_path / "ignored-runs"),
            "ADA2_DWARF_STATE_DIR": str(tmp_path / "ignored-state"),
            "ADA2_DWARF_BUNDLES_DIR": str(tmp_path / "ignored-bundles"),
        },
    )
    assert runtime.runtime_root == tmp_path / "runtime"
    assert runtime.runs == tmp_path / "runtime" / "runs"


RUNTIME_STORAGE_MODULES = (
    "profile_manager/cli.py",
    "profile_manager/run_presentation.py",
    "profile_manager/data/bundle_import.py",
    "profile_manager/data/bundles.py",
    "profile_manager/data/client_example_evidence.py",
    "profile_manager/data/compare.py",
    "profile_manager/data/coverage.py",
    "profile_manager/data/health_metrics.py",
    "profile_manager/data/operate_audit.py",
    "profile_manager/data/operate_compare.py",
    "profile_manager/data/operate_coverage.py",
    "profile_manager/data/operate_coverage_trend.py",
    "profile_manager/data/operate_crashes.py",
    "profile_manager/data/operate_run.py",
    "profile_manager/data/operate_run_compare.py",
    "profile_manager/data/operate_run_compare_field_diff.py",
    "profile_manager/data/operate_run_tail.py",
    "profile_manager/data/operate_static_analysis.py",
    "profile_manager/data/operate_testcases.py",
    "profile_manager/data/operate_timeline.py",
    "profile_manager/data/runs.py",
    "profile_manager/data/scheduler.py",
    "profile_manager/primitives.py",
    "scripts/runtime_coverage_aggregate.py",
)


@pytest.mark.parametrize("relative", RUNTIME_STORAGE_MODULES)
def test_runtime_storage_modules_do_not_implement_their_own_storage_fallback(relative):
    source = (Path(__file__).parents[1] / "dwarf" / relative).read_text()
    assert not re.search(r'os\.environ\.get\("ADA2_DWARF_(?:RUNS|STATE|BUNDLES)_DIR"', source)
    assert not re.search(r'parents\[[0-9]+\].{0,80}/\s*"(?:runs|state|bundles)"', source)


def test_runtime_package_has_one_environment_storage_resolver():
    package = Path(__file__).parents[1] / "dwarf" / "profile_manager"
    offenders = []
    for path in package.rglob("*.py"):
        if path.name == "config.py":
            continue
        source = path.read_text()
        if re.search(r'os\.environ\.get\("ADA2_DWARF_(?:RUNS|STATE|BUNDLES)_DIR"', source):
            offenders.append(str(path.relative_to(package)))
    assert offenders == []


def test_inventory_classifies_complete_incomplete_invalid_and_unreadable(tmp_path):
    from profile_manager.data.storage_reconciliation import inventory_storage

    complete = _write_run(tmp_path, "run-complete")
    (tmp_path / "runs" / "run-incomplete").mkdir()
    invalid_dir = tmp_path / "runs" / "run-invalid"
    invalid_dir.mkdir()
    (invalid_dir / "manifest.json").write_text("{not-json")
    (invalid_dir / "chain.json").write_text("{}")
    unreadable_dir = tmp_path / "runs" / "run-unreadable"
    _write_run(tmp_path, "run-unreadable", previous=complete)
    unreadable_file = unreadable_dir / "evidence.bin"
    unreadable_file.chmod(0)
    try:
        result = inventory_storage(tmp_path)
    finally:
        unreadable_file.chmod(stat.S_IRUSR | stat.S_IWUSR)

    assert {row["run_id"] for row in result["runs"]["complete"]} == {"run-complete"}
    assert {row["run_id"] for row in result["runs"]["incomplete"]} == {"run-incomplete"}
    assert {row["run_id"] for row in result["runs"]["invalid"]} == {"run-invalid"}
    assert {row["run_id"] for row in result["runs"]["unreadable"]} == {"run-unreadable"}
    assert result["runs"]["counts"]["manifest_present"] == 3
    assert result["runs"]["counts"]["importable_complete"] == 1


@pytest.mark.parametrize("defect", ["manifest-id", "chain-id", "manifest-hash", "symlink", "fifo"])
def test_inventory_rejects_identity_hash_escape_and_special_file_defects(tmp_path, defect):
    from profile_manager.data.storage_reconciliation import inventory_storage

    run_id = f"run-{defect}"
    _write_run(tmp_path, run_id)
    run_dir = tmp_path / "runs" / run_id
    if defect == "manifest-id":
        body = json.loads((run_dir / "manifest.json").read_text())
        body["run_id"] = "other"
        (run_dir / "manifest.json").write_bytes(_canonical(body))
    elif defect == "chain-id":
        body = json.loads((run_dir / "chain.json").read_text())
        body["run_id"] = "other"
        (run_dir / "chain.json").write_text(json.dumps(body))
    elif defect == "manifest-hash":
        body = json.loads((run_dir / "chain.json").read_text())
        body["manifest_hash"] = "0" * 64
        (run_dir / "chain.json").write_text(json.dumps(body))
    elif defect == "symlink":
        (run_dir / "escape").symlink_to(tmp_path / "outside")
    else:
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFO is not supported")
        os.mkfifo(run_dir / "special")

    result = inventory_storage(tmp_path)
    invalid = result["runs"]["invalid"]
    assert [row["run_id"] for row in invalid] == [run_id]
    assert invalid[0]["reasons"]


def test_dry_run_and_apply_preserve_full_tree_heads_and_source_then_restart_noop(tmp_path):
    from profile_manager.data.storage_reconciliation import reconcile_storage

    source = tmp_path / "source"
    destination = tmp_path / "destination"
    _write_run(source, "run-a", payload=b"\x00exact\xff")
    (source / "state").mkdir()
    (destination / "state").mkdir(parents=True)
    (source / "state" / "chain-head.json").write_bytes(b'{"source":true}\n')
    (destination / "state" / "chain-head.json").write_bytes(b'{"destination":true}\n')
    source_before = _tree_bytes(source)
    destination_head = (destination / "state" / "chain-head.json").read_bytes()

    preview = reconcile_storage(source, destination=_roots(destination), apply=False)
    assert preview["mode"] == "dry-run"
    assert preview["runs"]["import"] == ["run-a"]
    assert not (destination / "runs" / "run-a").exists()

    applied = reconcile_storage(source, destination=_roots(destination), apply=True)
    assert applied["status"] == "applied"
    assert _tree_bytes(source) == source_before
    assert _tree_bytes(destination / "runs" / "run-a") == _tree_bytes(source / "runs" / "run-a")
    assert (destination / "state" / "chain-head.json").read_bytes() == destination_head
    receipt = Path(applied["receipt_path"])
    assert receipt.is_file() and destination / "state" in receipt.parents
    receipt_body = json.loads(receipt.read_text())
    assert receipt_body["runs"][0]["source_digest"] == receipt_body["runs"][0]["destination_digest"]
    assert receipt_body["runs"][0]["manifest_framework_commit"] == "f" * 40

    restarted = reconcile_storage(source, destination=_roots(destination), apply=True)
    assert restarted["runs"]["duplicate"] == ["run-a"]
    assert restarted["runs"]["import"] == []


def test_apply_stages_entire_selected_set_before_committing_any_run(tmp_path, monkeypatch):
    from profile_manager.data import storage_reconciliation

    source = tmp_path / "source"
    destination = tmp_path / "destination"
    first = _write_run(source, "run-a")
    _write_run(source, "run-b", previous=first)
    original_copytree = shutil.copytree

    def fail_second_copy(source_path, destination_path, **kwargs):
        if Path(source_path).name == "run-b":
            raise OSError("simulated staging failure")
        return original_copytree(source_path, destination_path, **kwargs)

    monkeypatch.setattr(storage_reconciliation.shutil, "copytree", fail_second_copy)
    with pytest.raises(OSError, match="simulated staging failure"):
        storage_reconciliation.reconcile_storage(
            source, destination=_roots(destination), apply=True
        )

    assert not (destination / "runs" / "run-a").exists()
    assert not (destination / "runs" / "run-b").exists()


def test_atomic_moves_are_staged_inside_each_destination_volume(tmp_path, monkeypatch):
    from profile_manager.data import storage_reconciliation

    source = tmp_path / "source"
    destination = tmp_path / "destination"
    _write_run(source, "run-a")
    (source / "bundles").mkdir(parents=True)
    (source / "bundles" / "run-a.tar.gz").write_bytes(b"bundle")
    original_replace = os.replace

    def require_same_component_volume(source_path, destination_path):
        source_path = Path(source_path)
        destination_path = Path(destination_path)
        if destination_path.parent == destination / "runs":
            assert destination / "runs" in source_path.parents
        if destination_path.parent == destination / "bundles":
            assert destination / "bundles" in source_path.parents
        return original_replace(source_path, destination_path)

    monkeypatch.setattr(
        storage_reconciliation.os, "replace", require_same_component_volume
    )
    result = storage_reconciliation.reconcile_storage(
        source,
        destination=_roots(destination),
        apply=True,
        include_bundles=True,
    )

    assert result["status"] == "applied"
    assert not (destination / "runs" / ".storage-reconciliation-staging").exists()
    assert not (destination / "bundles" / ".storage-reconciliation-staging").exists()


def test_inventory_ignores_reserved_in_volume_staging_directories(tmp_path):
    from profile_manager.data.storage_reconciliation import inventory_storage

    _write_run(tmp_path, "run-a")
    (tmp_path / "runs" / ".storage-reconciliation-staging" / "orphan").mkdir(
        parents=True
    )
    (tmp_path / "bundles" / ".storage-reconciliation-staging" / "orphan").mkdir(
        parents=True
    )

    result = inventory_storage(tmp_path)

    assert result["runs"]["counts"]["run_directories"] == 1
    assert result["bundles"]["counts"] == {
        "complete": 0,
        "invalid": 0,
        "unreadable": 0,
    }


def test_interrupted_commit_leaves_durable_provenance_and_rerun_is_safe(
    tmp_path, monkeypatch
):
    from profile_manager.data import storage_reconciliation

    source = tmp_path / "source"
    destination = tmp_path / "destination"
    first = _write_run(source, "run-a")
    _write_run(source, "run-b", previous=first)
    original_replace = os.replace
    committed_targets = 0

    def fail_second_run_commit(source_path, destination_path):
        nonlocal committed_targets
        target = Path(destination_path)
        if target.parent == destination / "runs":
            committed_targets += 1
            if committed_targets == 2:
                raise OSError("simulated commit interruption")
        return original_replace(source_path, destination_path)

    monkeypatch.setattr(storage_reconciliation.os, "replace", fail_second_run_commit)
    with pytest.raises(OSError, match="simulated commit interruption"):
        storage_reconciliation.reconcile_storage(
            source, destination=_roots(destination), apply=True
        )

    assert (destination / "runs" / "run-a").is_dir()
    assert not (destination / "runs" / "run-b").exists()
    transactions = list(
        (destination / "state" / "storage-reconciliation" / "transactions").glob("*.json")
    )
    assert len(transactions) == 1
    transaction = json.loads(transactions[0].read_text())
    assert transaction["status"] == "failed"
    assert transaction["committed_runs"] == ["run-a"]
    assert transaction["runs"][0]["source_digest"]
    assert transaction["runs"][0]["file_inventory"]

    monkeypatch.setattr(storage_reconciliation.os, "replace", original_replace)
    restarted = storage_reconciliation.reconcile_storage(
        source, destination=_roots(destination), apply=True
    )
    assert restarted["status"] == "applied"
    assert restarted["runs"]["duplicate"] == ["run-a"]
    assert restarted["runs"]["import"] == ["run-b"]
    assert (destination / "runs" / "run-b").is_dir()


def test_same_id_different_tree_refuses_entire_apply(tmp_path):
    from profile_manager.data.storage_reconciliation import StorageReconciliationError, reconcile_storage

    source = tmp_path / "source"
    destination = tmp_path / "destination"
    _write_run(source, "run-collision", payload=b"source")
    _write_run(source, "run-new")
    _write_run(destination, "run-collision", payload=b"destination")

    with pytest.raises(StorageReconciliationError, match="collision"):
        reconcile_storage(source, destination=_roots(destination), apply=True)
    assert not (destination / "runs" / "run-new").exists()


def test_full_tree_digest_includes_empty_directories(tmp_path):
    from profile_manager.data.storage_reconciliation import StorageReconciliationError, reconcile_storage

    source = tmp_path / "source"
    destination = tmp_path / "destination"
    _write_run(source, "run-empty-dir")
    _write_run(destination, "run-empty-dir")
    (source / "runs" / "run-empty-dir" / "empty-evidence-dir").mkdir()

    with pytest.raises(StorageReconciliationError, match="collision"):
        reconcile_storage(source, destination=_roots(destination), apply=True)


def test_selected_run_import_includes_predecessor_closure_in_order(tmp_path):
    from profile_manager.data.storage_reconciliation import reconcile_storage

    source = tmp_path / "source"
    destination = tmp_path / "destination"
    predecessor = _write_run(source, "run-previous")
    _write_run(source, "run-selected", previous=predecessor)

    result = reconcile_storage(
        source,
        destination=_roots(destination),
        run_ids=["run-selected"],
        apply=True,
    )
    assert result["runs"]["import"] == ["run-previous", "run-selected"]
    assert (destination / "runs" / "run-previous" / "chain.json").is_file()


def test_missing_predecessor_refuses_apply(tmp_path):
    from profile_manager.data.storage_reconciliation import StorageReconciliationError, reconcile_storage

    source = tmp_path / "source"
    chain = _write_run(source, "run-a")
    broken = _write_run(source, "run-b", previous=chain)
    (source / "runs" / "run-a").rename(source / "removed-predecessor")

    with pytest.raises(StorageReconciliationError, match="predecessor"):
        reconcile_storage(
            source,
            destination=_roots(tmp_path / "destination"),
            run_ids=["run-b"],
            apply=True,
        )


def test_unreadable_run_blocks_unscoped_apply_but_not_explicit_clean_selection(tmp_path):
    from profile_manager.data.storage_reconciliation import StorageReconciliationError, reconcile_storage

    source = tmp_path / "source"
    destination = tmp_path / "destination"
    _write_run(source, "run-clean")
    _write_run(source, "run-unreadable")
    unreadable = source / "runs" / "run-unreadable" / "evidence.bin"
    unreadable.chmod(0)
    try:
        with pytest.raises(StorageReconciliationError, match="unreadable"):
            reconcile_storage(source, destination=_roots(destination), apply=True)
        result = reconcile_storage(
            source,
            destination=_roots(destination),
            run_ids=["run-clean"],
            apply=True,
        )
    finally:
        unreadable.chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert result["runs"]["import"] == ["run-clean"]
    assert not (destination / "runs" / "run-unreadable").exists()


def test_bundle_import_is_byte_exact_and_name_collision_refuses_overwrite(tmp_path):
    from profile_manager.data.storage_reconciliation import StorageReconciliationError, reconcile_storage

    source = tmp_path / "source"
    destination = tmp_path / "destination"
    (source / "bundles").mkdir(parents=True)
    (destination / "bundles").mkdir(parents=True)
    (source / "bundles" / "new.tar.gz").write_bytes(b"archive")
    first = reconcile_storage(
        source, destination=_roots(destination), apply=True, include_bundles=True
    )
    assert (destination / "bundles" / "new.tar.gz").read_bytes() == b"archive"
    assert first["bundles"]["import"] == ["new.tar.gz"]

    (source / "bundles" / "same-name.tar.gz").write_bytes(b"source")
    (destination / "bundles" / "same-name.tar.gz").write_bytes(b"destination")
    with pytest.raises(StorageReconciliationError, match="bundle collision"):
        reconcile_storage(
            source, destination=_roots(destination), apply=True, include_bundles=True
        )
    assert (destination / "bundles" / "same-name.tar.gz").read_bytes() == b"destination"


def test_storage_cli_branches_before_deployment_intake_and_requires_explicit_apply(
    tmp_path, monkeypatch, capsys
):
    from profile_manager.cli import main

    source = tmp_path / "source"
    destination = tmp_path / "destination"
    _write_run(source, "run-cli")
    monkeypatch.setenv("ADA2_PROFILE_MANAGER_CONFIG", str(tmp_path / "missing-config.json"))
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(destination / "runs"))
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(destination / "state"))
    monkeypatch.setenv("ADA2_DWARF_BUNDLES_DIR", str(destination / "bundles"))

    assert main(["doctor", "storage", "--from", str(source), "--json"]) == 0
    doctor = json.loads(capsys.readouterr().out)
    assert doctor["sources"][0]["runs"]["counts"]["complete"] == 1

    assert main(["migrate", "storage", "--from", str(source), "--runs-only", "--json"]) == 0
    dry_run = json.loads(capsys.readouterr().out)
    assert dry_run["mode"] == "dry-run"
    assert not (destination / "runs" / "run-cli").exists()

    assert main([
        "migrate", "storage", "--from", str(source), "--apply", "--runs-only",
        "--plan-digest", dry_run["plan_digest"], "--json"
    ]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["status"] == "applied"
    assert (destination / "runs" / "run-cli" / "manifest.json").is_file()


def test_cli_apply_requires_explicit_scope_and_reviewed_plan_digest(tmp_path, monkeypatch, capsys):
    from profile_manager.cli import main

    source = tmp_path / "source"
    destination = tmp_path / "destination"
    _write_run(source, "run-safe")
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(destination / "runs"))
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(destination / "state"))
    monkeypatch.setenv("ADA2_DWARF_BUNDLES_DIR", str(destination / "bundles"))

    assert main(["migrate", "storage", "--from", str(source), "--apply", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "refused"
    assert not (destination / "runs" / "run-safe").exists()

    assert main([
        "migrate", "storage", "--from", str(source), "--apply", "--runs-only",
        "--plan-digest", "0" * 64, "--json",
    ]) == 2
    refusal = json.loads(capsys.readouterr().out)
    assert "stale plan digest" in refusal["error"]
    assert not (destination / "runs" / "run-safe").exists()
