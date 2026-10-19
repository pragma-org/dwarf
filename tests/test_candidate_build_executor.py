"""The executor gate must retain blockers without executing candidate code."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess

import pytest

from profile_manager import candidate_builds as builds
from profile_manager.testplease_candidates import discover_candidates
from scripts.ci_security_campaign import host_lock
from test_candidate_builds import COMMIT, source_candidate, write


def api():
    from profile_manager import candidate_build_executor
    return candidate_build_executor


def forbid_processes(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("executor launched a command"))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("executor launched a process"))


@pytest.mark.parametrize("implementation", ["amaru", "cardano-node"])
def test_every_lane_retains_precise_unresolved_receipt_without_execution(tmp_path, monkeypatch, implementation):
    candidate = source_candidate(tmp_path / "submitted", implementation)
    write(candidate.source_root, "candidate.yaml", "command: touch /tmp/DO-NOT-EXECUTE\n")
    candidate = discover_candidates(tmp_path / "submitted", repository_commit=COMMIT)[0]
    forbid_processes(monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", "secret-must-not-enter-receipt")
    for plan in builds.plan_candidate_builds(candidate):
        output = tmp_path / plan.variant
        receipt = api().execute_candidate_build(plan, directory=output, lock_path=tmp_path / "host.lock")
        assert receipt["status"] == "blocked"
        assert receipt["executed"] is receipt["ready"] is False
        assert receipt["reason"] == "candidate-build-prerequisites-unresolved"
        assert {"build-container-image-unpinned", "dependency-closure-unfrozen", "disposable-build-backend-unimplemented"} <= set(receipt["blockers"])
        assert receipt["source_copy_verified"] is True
        assert receipt["identity"]["plan_digest"] == plan.plan_digest
        assert receipt["identity"]["source_tree_digest"] == candidate.source_tree_digest
        assert receipt["identity"]["commands"] == [list(command) for command in plan.commands]
        assert receipt["artifacts"] == {}
        assert receipt["observations"] == []
        assert json.loads((output / "receipt.json").read_text()) == receipt
        assert "secret-must-not-enter-receipt" not in (output / "receipt.json").read_text()
        assert sorted(p.name for p in output.iterdir()) == ["receipt.json"]


def test_source_is_copied_independently_and_cleaned_after_gate(tmp_path, monkeypatch):
    candidate = source_candidate(tmp_path / "submitted")
    source = candidate.source_root / "crates/amaru/src/main.rs"
    source.chmod(0o755)
    candidate = discover_candidates(tmp_path / "submitted", repository_commit=COMMIT)[0]
    plan = builds.plan_candidate_builds(candidate)[0]
    executor = api()
    original = executor._freeze_source
    captured = []

    def inspect_copy(candidate, destination, **kwargs):
        original(candidate, destination, **kwargs)
        copy = destination / "crates/amaru/src/main.rs"
        assert copy.read_bytes() == source.read_bytes()
        assert copy.stat().st_ino != source.stat().st_ino
        assert copy.stat().st_mode & 0o111
        captured.append(destination)

    monkeypatch.setattr(executor, "_freeze_source", inspect_copy)
    executor.execute_candidate_build(plan, directory=tmp_path / "out", lock_path=tmp_path / "host.lock")
    assert captured and not captured[0].exists()
    assert source.read_text() == "fn main() {}\n"


@pytest.mark.parametrize("change", ["command", "env", "workdir", "pin", "resolved", "source"])
def test_modified_plan_or_source_is_rejected_with_retained_error(tmp_path, monkeypatch, change):
    candidate = source_candidate(tmp_path / "submitted")
    plan = builds.plan_candidate_builds(candidate)[0]
    mutations = {
        "command": {"commands": (("sh", "-c", "touch /tmp/NEVER"),)},
        "env": {"environment": (("GITHUB_TOKEN", "untrusted"),)},
        "workdir": {"command_workdirs": ("/",)},
        "pin": {"build_container_image": "image@sha256:" + "a" * 64},
        "resolved": {"unresolved": ()},
    }
    if change == "source":
        (candidate.source_root / "Cargo.lock").write_text("changed")
    else:
        plan = replace(plan, **mutations[change])
        plan = replace(plan, plan_digest=builds._identity(plan))
    forbid_processes(monkeypatch)
    receipt = api().execute_candidate_build(plan, directory=tmp_path / "out", lock_path=tmp_path / "host.lock")
    assert receipt["status"] == "error"
    assert receipt["reason"] == "candidate-build-validation-failed"
    assert receipt["source_copy_verified"] is False
    assert receipt["observations"] == []
    assert (tmp_path / "out/receipt.json").is_file()


@pytest.mark.parametrize("kind", ["internal-link", "escape-link", "fifo"])
def test_unsafe_source_entries_fail_closed(tmp_path, monkeypatch, kind):
    candidate = source_candidate(tmp_path / "submitted")
    plan = builds.plan_candidate_builds(candidate)[0]
    if kind == "fifo":
        os.mkfifo(candidate.source_root / "pipe")
    else:
        target = "Cargo.lock" if kind == "internal-link" else "/etc/passwd"
        (candidate.source_root / "link").symlink_to(target)
        if kind == "internal-link":
            candidate = discover_candidates(tmp_path / "submitted", repository_commit=COMMIT)[0]
            plan = builds.plan_candidate_builds(candidate)[0]
    forbid_processes(monkeypatch)
    receipt = api().execute_candidate_build(plan, directory=tmp_path / "out", lock_path=tmp_path / "host.lock")
    assert receipt["status"] == "error"
    assert not receipt["source_copy_verified"]


def test_original_source_is_not_rescanned_after_snapshot(tmp_path, monkeypatch):
    candidate = source_candidate(tmp_path / "submitted")
    plan = builds.plan_candidate_builds(candidate)[0]
    executor = api()
    original = executor._freeze_source

    def mutate(candidate, destination, **kwargs):
        original(candidate, destination, **kwargs)
        (candidate.source_root / "Cargo.lock").write_text("changed after copy")

    monkeypatch.setattr(executor, "_freeze_source", mutate)
    receipt = executor.execute_candidate_build(plan, directory=tmp_path / "out", lock_path=tmp_path / "host.lock")
    assert receipt["status"] == "blocked"
    assert receipt["source_copy_verified"] is True
    assert receipt["observations"] == []


def test_host_lock_failure_is_retained_and_preserves_other_resources(tmp_path):
    plan = builds.plan_candidate_builds(source_candidate(tmp_path / "submitted"))[0]
    other = tmp_path / "unrelated"
    other.write_text("preserve")
    with host_lock(tmp_path / "host.lock"):
        receipt = api().execute_candidate_build(plan, directory=tmp_path / "out", lock_path=tmp_path / "host.lock")
    assert receipt["status"] == "error"
    assert receipt["reason"] == "candidate-build-host-lock-unavailable"
    assert other.read_text() == "preserve"


@pytest.mark.parametrize("limit", ["MAX_SOURCE_BYTES", "MAX_SOURCE_ENTRIES", "FREEZE_TIMEOUT_SECONDS"])
def test_freeze_budget_failure_is_retained_without_processes(tmp_path, monkeypatch, limit):
    plan = builds.plan_candidate_builds(source_candidate(tmp_path / "submitted"))[0]
    executor = api()
    monkeypatch.setattr(executor, limit, 0)
    forbid_processes(monkeypatch)
    receipt = executor.execute_candidate_build(plan, directory=tmp_path / "out", lock_path=tmp_path / "host.lock")
    assert receipt["status"] == "error"
    assert "limit" in receipt["detail"]
    assert receipt["source_copy_verified"] is False
    assert receipt["observations"] == []


def test_copy_digest_is_independent_of_original_source_revalidation(tmp_path, monkeypatch):
    plan = builds.plan_candidate_builds(source_candidate(tmp_path / "submitted"))[0]
    executor = api()
    original = executor._freeze_source

    def corrupt(candidate, destination, **kwargs):
        original(candidate, destination, **kwargs)
        path = destination / "Cargo.lock"
        path.chmod(0o600)
        path.write_text("corrupt copied tree")

    monkeypatch.setattr(executor, "_freeze_source", corrupt)
    receipt = executor.execute_candidate_build(plan, directory=tmp_path / "out", lock_path=tmp_path / "host.lock")
    assert receipt["status"] == "error"
    assert receipt["source_copy_verified"] is False


def test_atomic_update_failure_preserves_previous_partial_receipt(tmp_path, monkeypatch):
    plan = builds.plan_candidate_builds(source_candidate(tmp_path / "submitted"))[0]
    executor = api()
    original = os.replace
    calls = []

    def replace_once(source, target):
        calls.append(target)
        if len(calls) > 1:
            raise OSError("simulated atomic publication failure")
        return original(source, target)

    monkeypatch.setattr(os, "replace", replace_once)
    with pytest.raises(OSError, match="publication failure"):
        executor.execute_candidate_build(plan, directory=tmp_path / "out", lock_path=tmp_path / "host.lock")
    retained = json.loads((tmp_path / "out/receipt.json").read_text())
    assert retained["status"] == "partial"
    assert retained["observations"] == []
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["receipt.json"]


def test_receipt_write_failure_is_not_misreported_as_lock_contention(tmp_path, monkeypatch):
    plan = builds.plan_candidate_builds(source_candidate(tmp_path / "submitted"))[0]
    executor = api()
    original = os.replace
    calls = []

    def fail_final_once(source, target):
        calls.append(target)
        if len(calls) == 2:
            raise OSError("simulated receipt disk failure")
        return original(source, target)

    monkeypatch.setattr(os, "replace", fail_final_once)
    with pytest.raises(OSError, match="disk failure"):
        executor.execute_candidate_build(plan, directory=tmp_path / "out", lock_path=tmp_path / "host.lock")
    retained = json.loads((tmp_path / "out/receipt.json").read_text())
    assert retained["status"] == "partial"


def test_existing_output_and_symlink_parent_are_never_reused(tmp_path):
    plan = builds.plan_candidate_builds(source_candidate(tmp_path / "submitted"))[0]
    output = tmp_path / "out"
    output.mkdir()
    (output / "preserve").write_text("existing")
    with pytest.raises(ValueError, match="already exists"):
        api().execute_candidate_build(plan, directory=output, lock_path=tmp_path / "host.lock")
    (tmp_path / "alias").symlink_to(output, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        api().execute_candidate_build(plan, directory=tmp_path / "alias/child", lock_path=tmp_path / "host.lock")
    assert (output / "preserve").read_text() == "existing"
    assert not (output / "child").exists()


def test_original_source_is_never_discovered_or_revalidated_by_path(tmp_path, monkeypatch):
    plan = builds.plan_candidate_builds(source_candidate(tmp_path / "submitted"))[0]
    executor = api()
    original = builds.discover_candidates

    def only_owned(repository, **kwargs):
        assert Path(repository) != tmp_path / "submitted", "untrusted pathname rescan before bounded freeze"
        return original(repository, **kwargs)

    monkeypatch.setattr(builds, "discover_candidates", only_owned)
    receipt = executor.execute_candidate_build(plan, directory=tmp_path / "out", lock_path=tmp_path / "host.lock")
    assert receipt["status"] == "blocked"


@pytest.mark.parametrize("limit", ["entries", "bytes", "timeout"])
def test_untrusted_limits_apply_before_any_recognition_or_digest(tmp_path, monkeypatch, limit):
    path, doc = discovery_inputs(tmp_path)
    candidate_root = tmp_path / "submitted" / doc["candidates"][0]["source_path"]
    if limit == "entries":
        for index in range(20):
            write(candidate_root, f"extra-{index}", "data")
    executor = api()
    if limit == "entries": monkeypatch.setattr(executor, "MAX_SOURCE_ENTRIES", 3)
    if limit == "bytes": monkeypatch.setattr(executor, "MAX_SOURCE_BYTES", 3)
    if limit == "timeout": monkeypatch.setattr(executor, "FREEZE_TIMEOUT_SECONDS", 0)
    original = executor.discover_candidates

    def only_owned(repository, **kwargs):
        assert Path(repository) != tmp_path / "submitted", "unbounded discovery reached original source"
        return original(repository, **kwargs)

    monkeypatch.setattr(executor, "discover_candidates", only_owned)
    result = executor.preflight_discovery(path, source_repository=tmp_path / "submitted", directory=tmp_path / "out",
                                         expected_commit=COMMIT, expected_trusted_revision="b" * 40, lock_path=tmp_path / "host.lock")
    assert result["status"] == "error"
    assert "limit" in result["detail"]
    assert result["receipts"] == []


def test_hard_link_source_is_rejected_before_recognition(tmp_path, monkeypatch):
    candidate = source_candidate(tmp_path / "submitted")
    os.link(candidate.source_root / "Cargo.lock", tmp_path / "external-hardlink")
    plan = builds.plan_candidate_builds(candidate)[0]
    forbid_processes(monkeypatch)
    receipt = api().execute_candidate_build(plan, directory=tmp_path / "out", lock_path=tmp_path / "host.lock")
    assert receipt["status"] == "error"
    assert "hardlink" in receipt["detail"]


@pytest.mark.parametrize("replacement", ["fifo", "symlink", "regular"])
def test_file_replacement_between_stat_and_open_fails_without_blocking(tmp_path, monkeypatch, replacement):
    candidate = source_candidate(tmp_path / "submitted")
    plan = builds.plan_candidate_builds(candidate)[0]
    original = os.open
    replaced = []

    def swap(name, flags, *args, **kwargs):
        if name == "Cargo.lock" and kwargs.get("dir_fd") is not None and not replaced:
            replaced.append(True)
            target = candidate.source_root / name
            target.unlink()
            if replacement == "fifo": os.mkfifo(target)
            elif replacement == "symlink": target.symlink_to("Cargo.toml")
            else: target.write_text("replacement")
        return original(name, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swap)
    receipt = api().execute_candidate_build(plan, directory=tmp_path / "out", lock_path=tmp_path / "host.lock")
    assert replaced
    assert receipt["status"] == "error"
    assert not receipt["source_copy_verified"]


@pytest.mark.parametrize("mode", [stat.S_IFSOCK, stat.S_IFCHR, stat.S_IFBLK])
def test_socket_and_device_source_are_rejected_without_opening(tmp_path, monkeypatch, mode):
    candidate = source_candidate(tmp_path / "submitted")
    plan = builds.plan_candidate_builds(candidate)[0]
    original_stat, original_open = os.stat, os.open

    def special(name, *args, **kwargs):
        value = original_stat(name, *args, **kwargs)
        if name == "Cargo.lock" and kwargs.get("dir_fd") is not None:
            fields = list(value)
            fields[0] = mode | 0o600
            return os.stat_result(fields)
        return value

    def no_special_open(name, *args, **kwargs):
        assert not (name == "Cargo.lock" and kwargs.get("dir_fd") is not None), "special file opened"
        return original_open(name, *args, **kwargs)

    monkeypatch.setattr(os, "stat", special)
    monkeypatch.setattr(os, "open", no_special_open)
    receipt = api().execute_candidate_build(plan, directory=tmp_path / "out", lock_path=tmp_path / "host.lock")
    assert receipt["status"] == "error"


def discovery_inputs(tmp_path):
    candidate = source_candidate(tmp_path / "submitted")
    plans = builds.plan_candidate_builds(candidate)
    doc = {"schema_version": "v1", "kind": "security-campaign-discovery", "status": "validated",
           "repository_commit": COMMIT, "trusted_revision": "b" * 40, "official_selection": [],
           "candidates": [{"implementation": candidate.implementation, "candidate_name": candidate.candidate_name,
                           "repository_commit": COMMIT, "source_tree_digest": candidate.source_tree_digest,
                           "source_path": "dwarf/testplease/amaru/" + candidate.candidate_name,
                           "build_plans": [{"variant": p.variant, "plan_digest": p.plan_digest, "recipe_digest": p.recipe_digest} for p in plans]}]}
    path = tmp_path / "discovery.json"
    save_discovery(path, doc)
    return path, doc


def save_discovery(path, doc):
    path.write_text(json.dumps(doc))
    path.with_suffix(".sha256").write_text(hashlib.sha256(path.read_bytes()).hexdigest() + "\n")


def test_discovery_normalizes_trusted_relative_repository_before_snapshot(tmp_path, monkeypatch):
    _, doc = discovery_inputs(tmp_path)
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    monkeypatch.chdir(trusted)

    selected = api()._discovered_candidates(doc, Path("../submitted"), COMMIT, "b" * 40)
    candidate, _ = selected[0]

    assert ".." not in candidate.source_root.parts
    with api()._snapshot(candidate, api()._SourceBudget()) as frozen:
        assert frozen.source_tree_digest == candidate.source_tree_digest


def test_discovery_gate_revalidates_plans_and_emits_no_observed_builds(tmp_path, monkeypatch):
    path, _ = discovery_inputs(tmp_path)
    forbid_processes(monkeypatch)
    result = api().preflight_discovery(path, source_repository=tmp_path / "submitted", directory=tmp_path / "output",
                                       expected_commit=COMMIT, expected_trusted_revision="b" * 40, lock_path=tmp_path / "host.lock")
    assert result["status"] == "blocked"
    assert result["input_integrity"] == "sha256-matched"
    assert len(result["receipts"]) == 3
    assert result["verified_artifacts"] is False
    assert json.loads((tmp_path / "output/build-observations.json").read_text()) == []
    assert "campaign-signing-and-artifact-transfer-unconfigured" in result["blockers"]


@pytest.mark.parametrize("phase", ["recognition-and-digest", "plan-validation"])
def test_snapshot_deadline_covers_recognition_digest_and_plan_checks(tmp_path, monkeypatch, phase):
    path, _ = discovery_inputs(tmp_path)
    executor = api()
    clock = [0.0]
    monkeypatch.setattr(executor.time, "monotonic", lambda: clock[0])
    method = "discover_candidates" if phase == "recognition-and-digest" else "validate_candidate_build_plan"
    original = getattr(executor, method)

    def expire(*args, **kwargs):
        result = original(*args, **kwargs)
        clock[0] = executor.FREEZE_TIMEOUT_SECONDS + 1
        return result

    monkeypatch.setattr(executor, method, expire)
    result = executor.preflight_discovery(path, source_repository=tmp_path / "submitted", directory=tmp_path / "out",
                                         expected_commit=COMMIT, expected_trusted_revision="b" * 40, lock_path=tmp_path / "host.lock")
    assert result["status"] == "error"
    assert "limit" in result["detail"]
    assert json.loads((tmp_path / "out/build-observations.json").read_text()) == []


def test_three_lanes_use_one_source_snapshot_and_ignore_unselected_trees(tmp_path, monkeypatch):
    path, _ = discovery_inputs(tmp_path)
    unselected = tmp_path / "submitted/dwarf/testplease/amaru/unselected"
    unselected.mkdir()
    os.mkfifo(unselected / "must-not-inspect")
    executor = api()
    original = executor._freeze_source
    calls = []

    def count(candidate, destination, **kwargs):
        calls.append(candidate.source_root)
        return original(candidate, destination, **kwargs)

    monkeypatch.setattr(executor, "_freeze_source", count)
    result = executor.preflight_discovery(path, source_repository=tmp_path / "submitted", directory=tmp_path / "out",
                                         expected_commit=COMMIT, expected_trusted_revision="b" * 40, lock_path=tmp_path / "host.lock")
    assert result["status"] == "blocked"
    assert len(calls) == 1
    assert len(result["receipts"]) == 3


@pytest.mark.parametrize("change", ["source", "plan"])
def test_source_and_plan_digest_mismatches_are_checked_against_owned_snapshot(tmp_path, monkeypatch, change):
    path, doc = discovery_inputs(tmp_path)
    if change == "source": doc["candidates"][0]["source_tree_digest"] = "c" * 64
    else: doc["candidates"][0]["build_plans"][0]["plan_digest"] = "d" * 64
    save_discovery(path, doc)
    executor = api()
    original = executor._freeze_source
    calls = []

    def count(candidate, destination, **kwargs):
        calls.append(destination)
        return original(candidate, destination, **kwargs)

    monkeypatch.setattr(executor, "_freeze_source", count)
    result = executor.preflight_discovery(path, source_repository=tmp_path / "submitted", directory=tmp_path / "out",
                                         expected_commit=COMMIT, expected_trusted_revision="b" * 40, lock_path=tmp_path / "host.lock")
    assert result["status"] == "error"
    assert len(calls) == 1
    assert not calls[0].exists()
    assert result["receipts"] == []


def test_failed_lane_preserves_prior_and_later_lane_receipts(tmp_path, monkeypatch):
    path, _ = discovery_inputs(tmp_path)
    executor = api()
    original = executor.validate_candidate_build_plan

    def fail_one(plan):
        if plan.variant == "patched":
            raise OSError("simulated validation failure")
        return original(plan)

    monkeypatch.setattr(executor, "validate_candidate_build_plan", fail_one)
    result = executor.preflight_discovery(path, source_repository=tmp_path / "submitted", directory=tmp_path / "output",
                                         expected_commit=COMMIT, expected_trusted_revision="b" * 40, lock_path=tmp_path / "host.lock")
    assert result["status"] == "error"
    assert [row["status"] for row in result["receipts"]] == ["blocked", "error", "blocked"]
    for row in result["receipts"]:
        assert hashlib.sha256((tmp_path / "output" / row["path"]).read_bytes()).hexdigest() == row["sha256"]


@pytest.mark.parametrize("change", ["digest", "path", "revision", "commit", "duplicate"])
def test_discovery_mismatches_never_reach_source_copy(tmp_path, monkeypatch, change):
    path, doc = discovery_inputs(tmp_path)
    row = doc["candidates"][0]
    if change == "path": row["source_path"] = "../../outside"
    if change == "revision": doc["trusted_revision"] = "e" * 40
    if change == "commit": doc["repository_commit"] = "f" * 40
    if change == "duplicate": doc["candidates"].append(row.copy())
    save_discovery(path, doc)
    if change == "digest": path.write_text("{}")
    executor = api()
    monkeypatch.setattr(executor, "_freeze_source", lambda *a, **k: pytest.fail("invalid discovery reached freeze"))
    result = executor.preflight_discovery(path, source_repository=tmp_path / "submitted", directory=tmp_path / "output",
                                         expected_commit=COMMIT, expected_trusted_revision="b" * 40, lock_path=tmp_path / "host.lock")
    assert result["status"] == "error"
    assert result["verified_artifacts"] is False
    assert (tmp_path / "output/blocked.json").is_file()
    assert json.loads((tmp_path / "output/build-observations.json").read_text()) == []


def worker_plan(tmp_path, monkeypatch, implementation="amaru", variant="stock"):
    from test_candidate_builds import trusted_patch
    from test_candidate_build_environments import environment_fixture
    candidate = source_candidate(tmp_path / "submitted", implementation)
    trusted_patch(tmp_path, monkeypatch, implementation)
    plan = next(p for p in builds.plan_candidate_builds(candidate) if p.variant == variant)
    path, manifest, _ = environment_fixture(builds.TRUSTED_ROOT, plan)
    for probe in manifest["toolchain_probes"]:
        probe["stdout_sha256"] = hashlib.sha256((probe["tool"] + " trusted probe\n").encode()).hexdigest()
    path.write_text(json.dumps(manifest))
    return next(p for p in builds.plan_candidate_builds(candidate) if p.variant == variant)


class FakeWorkerBackend:
    """Synthetic lifecycle only: never invokes a compiler, container or process."""
    def __init__(self, action=None, *, exit_code=0, cleanup_error=False):
        self.action, self.exit_code, self.cleanup_error = action, exit_code, cleanup_error
        self.requests, self.cleaned, self.timeouts = [], False, []

    def start(self, request):
        self.requests.append(request)
        identity = json.loads(request.identity_json)
        environment = json.loads(request.environment_json)
        assert request.source.source_root.exists()
        assert request.source.source_root != identity.get("source_root")
        for relative, expected, path in request.inputs:
            assert path.is_file() and not path.is_symlink()
            assert hashlib.sha256(path.read_bytes()).hexdigest() == expected
            assert builds.TRUSTED_ROOT not in path.parents
        write(request.output, "executable", "fake executable bytes")
        write(request.output, "image", "fake image artifact bytes")
        result = {key: identity[key] for key in ("plan_digest", "source_tree_digest", "repository_commit",
                   "build_environment_digest", "dependency_closure_digest")}
        result.update({name + "_sha256": hashlib.sha256((request.output / name).read_bytes()).hexdigest()
                       for name in ("executable", "image")})
        write(request.output, "build-result.json", json.dumps(result))
        for index, probe in enumerate(environment["toolchain_probes"]):
            write(request.output, f"probes/{index:04d}.stdout", probe["tool"] + " trusted probe\n")
        write(request.output, "build.log", "bounded partial log\n")
        return self

    def wait(self, *, timeout_seconds):
        self.timeouts.append(timeout_seconds)
        if self.action:
            self.action(self.requests[-1])
        return self.exit_code

    def cleanup(self):
        self.cleaned = True
        if self.cleanup_error:
            raise RuntimeError("synthetic cleanup failure")


def test_explicit_fake_backend_emits_only_independently_hashed_stock_observation(tmp_path, monkeypatch):
    plan = worker_plan(tmp_path, monkeypatch)
    backend = FakeWorkerBackend()
    forbid_processes(monkeypatch)
    receipt = api().execute_candidate_build(plan, directory=tmp_path / "out", lock_path=tmp_path / "lock", backend=backend)
    assert receipt["status"] == "built"
    assert receipt["executed"] is True and receipt["ready"] is False
    assert backend.cleaned and backend.timeouts == [600]
    request = backend.requests[0]
    assert not request.source.source_root.exists() and not request.output.exists()
    assert all(not path.exists() for _, _, path in request.inputs)
    expected = {name + "_sha256": hashlib.sha256((tmp_path / "out/worker" / filename).read_bytes()).hexdigest()
                for name, filename in (("executable", "executable"), ("image", "image"), ("build_result", "build-result.json"))}
    assert receipt["artifacts"] == expected
    assert receipt["observations"] == [{"mode": "stock", "source": {
        "implementation": "amaru", "candidate_name": plan.candidate.candidate_name,
        "repository_commit": COMMIT, "source_tree_digest": plan.source_tree_digest,
        "source_path": "dwarf/testplease/amaru/" + plan.candidate.candidate_name},
        "plan_digest": plan.plan_digest, "recipe_digest": plan.recipe_digest,
        "build_environment_digest": plan.build_environment_digest, "dependency_closure_digest": plan.dependency_closure_digest,
        "toolchain_digest": "sha256:" + hashlib.sha256(json.dumps(dict(plan.toolchain), sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "artifacts": {"executable_digest": "sha256:" + expected["executable_sha256"],
                      "binary_digest": "sha256:" + expected["executable_sha256"],
                      "image_digest": "sha256:" + expected["image_sha256"],
                      "build_result_digest": "sha256:" + expected["build_result_sha256"]}}]
    assert json.loads((tmp_path / "out/receipt.json").read_text()) == receipt
    with host_lock(tmp_path / "lock"):
        pass


@pytest.mark.parametrize("failure", ["nonzero", "timeout", "worker", "cleanup", "probe", "hash", "identity", "source", "closure", "recipe", "output-limit", "output-link", "output-fifo"])
def test_worker_failures_retain_bounded_evidence_without_observation(tmp_path, monkeypatch, failure):
    plan = worker_plan(tmp_path, monkeypatch)
    def fail(request):
        if failure == "timeout": raise TimeoutError("synthetic timeout")
        if failure == "worker": raise RuntimeError("synthetic worker failure")
        if failure == "probe": write(request.output, "probes/0000.stdout", "candidate claims exact version")
        if failure in {"hash", "identity"}:
            path = request.output / "build-result.json"
            result = json.loads(path.read_text())
            result["executable_sha256" if failure == "hash" else "plan_digest"] = "d" * 64
            path.write_text(json.dumps(result))
        if failure == "source":
            path = request.source.source_root / "Cargo.lock"
            path.chmod(0o600)
            path.write_text("changed")
        if failure in {"closure", "recipe"}:
            row = next(r for r in plan.inputs if r.origin == "trusted" and
                       (r.path.endswith(".fixture") if failure == "closure" else r.path.endswith("candidate_builds.py")))
            (builds.TRUSTED_ROOT / row.path).write_text("changed")
        if failure == "output-limit": write(request.output, "large", "x" * 10000)
        if failure == "output-link": (request.output / "unsafe").symlink_to("/etc/passwd")
        if failure == "output-fifo": os.mkfifo(request.output / "unsafe")
    backend = FakeWorkerBackend(fail, exit_code=9 if failure == "nonzero" else 0, cleanup_error=failure == "cleanup")
    if failure == "output-limit": monkeypatch.setattr(api(), "MAX_WORKER_OUTPUT_BYTES", 1024)
    receipt = api().execute_candidate_build(plan, directory=tmp_path / "out", lock_path=tmp_path / "lock", backend=backend)
    assert receipt["status"] == "error", receipt
    assert receipt["observations"] == [] and receipt["artifacts"] == {}
    assert receipt["executed"] is True and receipt["ready"] is False
    assert backend.cleaned
    assert not backend.requests[0].source.source_root.exists()
    assert not backend.requests[0].output.exists()
    assert (tmp_path / "out/receipt.json").is_file()
    assert sum(p.stat().st_size for p in (tmp_path / "out/worker").rglob("*") if p.is_file()) <= api().MAX_WORKER_OUTPUT_BYTES
    with host_lock(tmp_path / "lock"):
        pass


@pytest.mark.parametrize("implementation,variant", [("amaru", "patched"), ("amaru", "compiler-coverage"), ("cardano-node", "stock")])
def test_backend_cannot_enable_other_lanes(tmp_path, monkeypatch, implementation, variant):
    plan = worker_plan(tmp_path, monkeypatch, implementation, variant)
    backend = FakeWorkerBackend()
    receipt = api().execute_candidate_build(plan, directory=tmp_path / "out", lock_path=tmp_path / "lock", backend=backend)
    assert receipt["status"] == "blocked" and not backend.requests
    assert receipt["observations"] == []


def test_backend_without_real_trusted_environment_is_never_started(tmp_path):
    plan = builds.plan_candidate_builds(source_candidate(tmp_path / "submitted"))[0]
    backend = FakeWorkerBackend()
    receipt = api().execute_candidate_build(plan, directory=tmp_path / "out", lock_path=tmp_path / "lock", backend=backend)
    assert receipt["status"] == "blocked" and not backend.requests


@pytest.mark.parametrize("failure", ["start", "missing-output", "input-copy", "late-exit", "error-size"])
def test_worker_protocol_edge_failures_never_authorize_builds(tmp_path, monkeypatch, failure):
    plan = worker_plan(tmp_path, monkeypatch)
    executor = api()
    clock = [0.0]
    if failure == "late-exit": monkeypatch.setattr(executor.time, "monotonic", lambda: clock[0])
    def fail(request):
        if failure == "missing-output": (request.output / "image").unlink()
        if failure == "input-copy":
            path = request.inputs[0][2]
            path.chmod(0o600)
            path.write_text("changed private input")
        if failure == "late-exit": clock[0] = 601
        if failure == "error-size": raise RuntimeError("x" * 20000)
    backend = FakeWorkerBackend(fail)
    if failure == "start":
        original = backend.start
        def failed_start(request):
            original(request)
            backend.cleanup()  # failed starts own their partial-resource cleanup
            raise RuntimeError("synthetic start failure")
        backend.start = failed_start
    receipt = executor.execute_candidate_build(plan, directory=tmp_path / "out", lock_path=tmp_path / "lock", backend=backend)
    assert receipt["status"] == "error" and receipt["observations"] == []
    assert receipt["executed"] and backend.cleaned
    assert len(receipt["detail"]) <= executor.MAX_ERROR_DETAIL
    assert (tmp_path / "out/worker/build.log").read_text() == "bounded partial log\n"
    assert not backend.requests[0].output.exists()


@pytest.mark.parametrize("workspace", ["dwarf-stock-worker-", "dwarf-candidate-freeze-"])
def test_temporary_workspace_cleanup_failure_cannot_publish_observation(tmp_path, monkeypatch, workspace):
    plan = worker_plan(tmp_path, monkeypatch)
    executor = api()
    original = executor.tempfile.TemporaryDirectory.cleanup
    def failed_cleanup(directory):
        original(directory)
        if Path(directory.name).name.startswith(workspace):
            raise OSError("synthetic workspace cleanup failure")
    monkeypatch.setattr(executor.tempfile.TemporaryDirectory, "cleanup", failed_cleanup)
    backend = FakeWorkerBackend()
    receipt = executor.execute_candidate_build(plan, directory=tmp_path / "out", lock_path=tmp_path / "lock", backend=backend)
    assert receipt["status"] == "error" and receipt["observations"] == []
    assert receipt["artifacts"] == {} and backend.cleaned
    assert "cleanup" in receipt["detail"]
    with host_lock(tmp_path / "lock"):
        pass


def test_closed_environment_preflight_stays_data_only(tmp_path, monkeypatch):
    plan = worker_plan(tmp_path, monkeypatch)
    executor = api()
    plans = builds.plan_candidate_builds(plan.candidate)
    doc = {"schema_version": "v1", "kind": "security-campaign-discovery", "status": "validated",
           "repository_commit": COMMIT, "trusted_revision": "b" * 40, "official_selection": [],
           "candidates": [{"implementation": "amaru", "candidate_name": plan.candidate.candidate_name,
                           "repository_commit": COMMIT, "source_tree_digest": plan.source_tree_digest,
                           "source_path": "dwarf/testplease/amaru/" + plan.candidate.candidate_name,
                           "build_plans": [{"variant": p.variant, "plan_digest": p.plan_digest, "recipe_digest": p.recipe_digest} for p in plans]}]}
    path = tmp_path / "discovery.json"
    save_discovery(path, doc)
    forbid_processes(monkeypatch)
    monkeypatch.setattr(executor, "_run_stock_worker", lambda *a, **k: pytest.fail("preflight launched worker"))
    summary = executor.preflight_discovery(path, source_repository=tmp_path / "submitted", directory=tmp_path / "out",
                                          expected_commit=COMMIT, expected_trusted_revision="b" * 40, lock_path=tmp_path / "lock")
    assert summary["status"] == "blocked" and summary["executed"] is False
    assert summary["verified_artifacts"] is False
    assert json.loads((tmp_path / "out/build-observations.json").read_text()) == []
    assert not list((tmp_path / "out").rglob("*.sig"))
