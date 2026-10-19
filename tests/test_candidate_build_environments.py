"""Trusted closure fixtures are declarations, never observed builds."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from profile_manager import candidate_builds as builds
from test_candidate_builds import source_candidate, trusted_patch, write


def api():
    from profile_manager import candidate_build_environments
    return candidate_build_environments


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def file_record(root, relative, content):
    path = write(root, relative, content)
    return {"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def environment_fixture(root, plan):
    """Invented fixture bytes exercise validation; they cannot enable execution."""
    env = api()
    for name in ("candidate-build-environment", "candidate-dependency-closure"):
        relative = f"dwarf/spec/v1/{name}.schema.json"
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if builds.TRUSTED_ROOT / relative != target:
            shutil.copyfile(builds.TRUSTED_ROOT / relative, target)
    recipe = env.environment_recipe_digest(plan)
    build_image = "example.invalid/build@sha256:" + "a" * 64
    prefix = f"dwarf/build-environments/v1/{plan.candidate.implementation}/{plan.source_tree_digest}"
    roles = {"package-manager-config", "solver-plan", "native-dependencies"}
    if plan.candidate.implementation == "amaru":
        roles.add("cargo-vendor-archive")
    else:
        roles.update(("cabal-store-archive", "repository-index", "runtime-root"))
    if plan.variant == "compiler-coverage":
        roles.add("coverage-tools")
    files = [{"role": role, **file_record(root, f"{prefix}/{role}.fixture", role)} for role in sorted(roles)]
    closure = {
        "schema_version": "v1", "kind": "candidate-dependency-closure",
        "source_tree_digest": plan.source_tree_digest, "recipe_digest": recipe,
        "build_image": build_image,
        "format": "cargo-vendor-v1" if plan.candidate.implementation == "amaru" else "cabal-store-v1",
        "locks": [{"path": row.path, "sha256": row.sha256} for row in plan.inputs
                  if row.origin == "source" and row.path in {"Cargo.lock", "cabal.project.freeze", "cabal.project"}],
        "files": files,
    }
    closure["offline_verification"] = {
        "inputs_sha256": digest(closure), "network": "none", "exit_code": 0,
        "observed_toolchain": dict(plan.toolchain),
        "log": file_record(root, f"{prefix}/offline.fixture.log", "synthetic fixture, not a build"),
    }
    closure_ref = file_record(root, f"{prefix}/{plan.variant}.closure.json", json.dumps(closure))
    manifest = {
        "schema_version": "v1", "kind": "candidate-build-environment",
        "implementation": plan.candidate.implementation, "variant": plan.variant,
        "source_tree_digest": plan.source_tree_digest, "recipe_digest": recipe,
        "platform": "linux/amd64", "build_image": build_image,
        "runtime_base_image": plan.base_image or "example.invalid/runtime@sha256:" + "b" * 64,
        "toolchain": dict(plan.toolchain),
        "toolchain_probes": [{"tool": tool, "argv": [tool, "--version"], "stdout_sha256": "c" * 64}
                             for tool, _ in plan.toolchain],
        "dependency_closure": closure_ref,
        "packaging": {"method": "copy-static-elf-v1" if plan.candidate.implementation == "amaru" else "copy-elf-runtime-closure-v1",
                      "executable_path": "/usr/local/bin/node", "entrypoint": ["/usr/local/bin/node"]},
        "isolation": {"network": "none", "host_socket": False, "secrets": False,
                      "source_read_only": True, "outputs": "owned-writable", "root_filesystem_read_only": True,
                      "privileged": False, "capabilities": [], "no_new_privileges": True,
                      "uid": 65532, "gid": 65532, "cpus": 2, "memory_bytes": 2147483648,
                      "pids": 256, "timeout_seconds": 600, "output_bytes": 1073741824},
    }
    path = write(root, f"{prefix}/{plan.variant}.json", json.dumps(manifest))
    return path, manifest, closure


def update_closure(root, path, manifest, closure):
    closure_path = root / manifest["dependency_closure"]["path"]
    closure_path.write_text(json.dumps(closure))
    manifest["dependency_closure"]["sha256"] = hashlib.sha256(closure_path.read_bytes()).hexdigest()
    path.write_text(json.dumps(manifest))


def test_all_lanes_identify_the_missing_trusted_manifest(tmp_path):
    for implementation in ("amaru", "cardano-node"):
        for plan in builds.plan_candidate_builds(source_candidate(tmp_path / implementation, implementation)):
            expected = f"dwarf/build-environments/v1/{implementation}/{plan.source_tree_digest}/{plan.variant}.json"
            assert f"trusted-build-environment-missing:{expected}" in plan.build_environment_blockers
            assert plan.build_environment_digest is plan.dependency_closure_digest is None


@pytest.mark.parametrize("implementation,variant", [
    ("amaru", "stock"), ("amaru", "compiler-coverage"), ("cardano-node", "stock"),
])
def test_closed_fixture_is_bound_but_never_enables_execution(tmp_path, monkeypatch, implementation, variant):
    candidate = source_candidate(tmp_path / "source", implementation)
    # Use an isolated trusted catalog; source metadata cannot select it.
    trusted_patch(tmp_path, monkeypatch, implementation)
    root = builds.TRUSTED_ROOT
    plan = next(p for p in builds.plan_candidate_builds(candidate) if p.variant == variant)
    path, manifest, closure = environment_fixture(root, plan)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("command execution forbidden"))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: pytest.fail("process execution forbidden"))
    bound = next(p for p in builds.plan_candidate_builds(candidate) if p.variant == variant)
    assert bound.build_container_image == manifest["build_image"]
    assert bound.base_image == manifest["runtime_base_image"]
    assert bound.build_environment_digest == hashlib.sha256(path.read_bytes()).hexdigest()
    assert bound.dependency_closure_digest == manifest["dependency_closure"]["sha256"]
    assert bound.build_environment_blockers == ()
    assert not bound.ready
    builds.validate_candidate_build_plan(bound)
    from profile_manager.candidate_build_executor import execute_candidate_build
    receipt = execute_candidate_build(bound, directory=tmp_path / "output", lock_path=tmp_path / "lock")
    assert receipt["status"] == "blocked" and receipt["executed"] is False
    assert {"disposable-build-backend-unimplemented", "observed-toolchain-unverified", "artifact-verification-unimplemented"} <= set(receipt["blockers"])
    assert "dependency-closure-unfrozen" not in receipt["blockers"]
    assert receipt["identity"]["dependency_closure_digest"] == bound.dependency_closure_digest
    (root / closure["files"][0]["path"]).write_text("changed")
    with pytest.raises(builds.CandidateBuildError, match="trusted.*changed"):
        builds.validate_candidate_build_plan(bound)


@pytest.mark.parametrize("field,value", [
    ("build_image", "mutable:latest"), ("source_tree_digest", "d" * 64),
    ("recipe_digest", "d" * 64), ("toolchain", {"rust": "stable"}),
    ("toolchain_probes", []), ("platform", "linux/arm64"),
])
def test_environment_rejects_unpinned_or_mismatched_identity(tmp_path, field, value):
    plan = builds.plan_candidate_builds(source_candidate(tmp_path / "source"))[0]
    path, manifest, _ = environment_fixture(tmp_path / "trusted", plan)
    manifest[field] = value
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        api().resolve_build_environment(plan, trusted_root=tmp_path / "trusted")


@pytest.mark.parametrize("field,value", [
    ("network", "bridge"), ("host_socket", True), ("secrets", True),
    ("source_read_only", False), ("privileged", True), ("capabilities", ["SYS_ADMIN"]),
    ("root_filesystem_read_only", False), ("no_new_privileges", False),
    ("uid", 0), ("timeout_seconds", 0), ("memory_bytes", 0), ("cpus", 0), ("pids", 0),
    ("output_bytes", 0),
])
def test_isolation_contract_rejects_weakened_boundary(tmp_path, field, value):
    plan = builds.plan_candidate_builds(source_candidate(tmp_path / "source"))[0]
    path, manifest, _ = environment_fixture(tmp_path / "trusted", plan)
    manifest["isolation"][field] = value
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        api().resolve_build_environment(plan, trusted_root=tmp_path / "trusted")


@pytest.mark.parametrize("change", ["lock", "missing-native", "missing-offline", "offline-identity", "offline-toolchain", "offline-network", "extra-command", "traversal", "symlink", "missing-file"])
def test_incomplete_or_unsafe_closure_stays_blocked(tmp_path, change):
    root = tmp_path / "trusted"
    plan = builds.plan_candidate_builds(source_candidate(tmp_path / "source"))[0]
    path, manifest, closure = environment_fixture(root, plan)
    if change == "lock": closure["locks"][0]["sha256"] = "d" * 64
    elif change == "missing-native": closure["files"] = [f for f in closure["files"] if f["role"] != "native-dependencies"]
    elif change == "missing-offline": closure.pop("offline_verification")
    elif change == "offline-identity": closure["offline_verification"]["inputs_sha256"] = "d" * 64
    elif change == "offline-toolchain": closure["offline_verification"]["observed_toolchain"] = {"rust": "stable"}
    elif change == "offline-network": closure["offline_verification"]["network"] = "bridge"
    elif change == "extra-command": manifest["command"] = ["sh", "evil.sh"]
    elif change == "traversal": closure["files"][0]["path"] = "../outside"
    elif change == "symlink":
        retained = root / closure["files"][0]["path"]
        retained.unlink()
        retained.symlink_to(write(root, "other", "outside"))
    elif change == "missing-file": (root / closure["files"][0]["path"]).unlink()
    update_closure(root, path, manifest, closure)
    with pytest.raises(ValueError):
        api().resolve_build_environment(plan, trusted_root=root)


def test_candidate_manifest_is_never_read_as_environment(tmp_path):
    candidate = source_candidate(tmp_path / "source")
    write(candidate.source_root, "build-environment.json", '{"command":["sh","evil.sh"]}')
    from profile_manager.testplease_candidates import discover_candidates
    candidate = discover_candidates(tmp_path / "source", repository_commit=candidate.repository_commit)[0]
    plan = builds.plan_candidate_builds(candidate)[0]
    assert any(reason.startswith("trusted-build-environment-missing:") for reason in plan.build_environment_blockers)


def test_cardano_coverage_requires_the_exact_pinned_archive(tmp_path):
    plan = builds.plan_candidate_builds(source_candidate(tmp_path / "source", "cardano-node"))[-1]
    root = tmp_path / "trusted"
    environment_fixture(root, plan)
    with pytest.raises(ValueError, match="missing archive: cardano-diffusion-1.1.1.0.tar.gz"):
        api().resolve_build_environment(plan, trusted_root=root)


def test_closure_cannot_resolve_an_unknown_compiler(tmp_path):
    plan = builds.plan_candidate_builds(source_candidate(tmp_path / "source", toolchain="stable"))[0]
    root = tmp_path / "trusted"
    environment_fixture(root, plan)
    with pytest.raises(ValueError, match="exact source compiler"):
        api().resolve_build_environment(plan, trusted_root=root)


def test_manifest_digest_binds_the_same_bytes_that_are_parsed(tmp_path, monkeypatch):
    payload = '{"identity":"retained"}'
    write(tmp_path, "manifest.json", payload)
    # A second pathname read could see replaced bytes after the hashing pass.
    monkeypatch.setattr(Path, "read_text", lambda *a, **kw: '{"identity":"replacement"}')
    actual, document = api()._retained(tmp_path, "manifest.json", document=True)
    assert actual == hashlib.sha256(payload.encode()).hexdigest()
    assert document == {"identity": "retained"}
