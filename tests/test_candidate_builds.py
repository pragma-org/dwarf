"""Candidate planning never compiles sources or executes candidate commands."""
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from profile_manager import candidate_builds as builds
from profile_manager.testplease_candidates import discover_candidates


COMMIT = "0123456789abcdef0123456789abcdef01234567"


def write(root, relative, content):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def source_candidate(tmp_path, implementation="amaru", toolchain="nightly-2026-09-04"):
    root = tmp_path / "dwarf/testplease" / implementation / "arbitrary-future-version"
    if implementation == "amaru":
        write(root, "Cargo.toml", '[workspace]\nmembers=["crates/amaru"]\n')
        write(root, "Cargo.lock", "version = 4\n")
        write(root, "crates/amaru/Cargo.toml", '[package]\nname="amaru"\nversion="999.0.0"\n')
        write(root, "crates/amaru/src/main.rs", "fn main() {}\n")
        write(root, "rust-toolchain.toml", f'[toolchain]\nchannel="{toolchain}"\n')
    else:
        write(root, "cabal.project", "packages: cardano-node\nwith-compiler: ghc-9.6.7\n")
        write(root, "cabal.project.freeze", "constraints: any.base ==4.18.3.0\n")
        write(root, "cardano-node/cardano-node.cabal", "name: cardano-node\nversion: 999.0.0\n")
        write(root, "cardano-node/src/Cardano/Node.hs", "module Cardano.Node where\n")
    return discover_candidates(tmp_path, repository_commit=COMMIT)[0]


@pytest.mark.parametrize("implementation", ["amaru", "cardano-node"])
def test_matrix_binds_one_frozen_source_without_version_defaults(tmp_path, implementation):
    candidate = source_candidate(tmp_path, implementation)
    plans = builds.plan_candidate_builds(candidate)
    assert tuple(plan.variant for plan in plans) == ("stock", "patched", "compiler-coverage")
    for plan in plans:
        assert plan.candidate == candidate
        assert plan.source_tree_digest == candidate.source_tree_digest
        assert plan.repository_commit == COMMIT
        assert plan.status == "unresolved"
        assert not plan.ready
        assert plan.executable_digest is None
        assert plan.image_digest is None
        assert len(plan.recipe_digest) == len(plan.plan_digest) == 64
        assert plan.commands
        assert plan.required_outputs == ("executable_sha256", "image_sha256", "build_result_sha256")
        assert plan.unresolved
        with pytest.raises(FrozenInstanceError):
            plan.variant = "other"
    coverage = plans[-1]
    assert coverage.performance_authority == "non-authoritative"
    assert coverage.coverage_envelope_fields == (
        "source_tree_digest", "repository_commit", "plan_digest", "toolchain",
        "compiler_flags", "harness_digest", "executable_digest", "image_digest",
        "corpus_digest", "raw_profile_digests", "coverage_report_digest",
    )
    assert coverage.harness_digest
    assert coverage.compiler_flags
    assert "coverage" in " ".join(" ".join(command) for command in coverage.commands)


@pytest.mark.parametrize("implementation", ["amaru", "cardano-node"])
def test_planning_is_read_only_and_ignores_candidate_commands(tmp_path, implementation, monkeypatch):
    candidate = source_candidate(tmp_path, implementation)
    sentinel = tmp_path / "executed"
    write(candidate.source_root, "candidate.yaml", f'command: touch {sentinel}\n')
    write(candidate.source_root, "scripts/build.sh", f'touch {sentinel}\n')
    candidate = discover_candidates(tmp_path, repository_commit=COMMIT)[0]
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("planner executed a command"))
    before = sorted(path.relative_to(candidate.source_root) for path in candidate.source_root.rglob("*"))
    plans = builds.plan_candidate_builds(candidate)
    assert sorted(path.relative_to(candidate.source_root) for path in candidate.source_root.rglob("*")) == before
    assert all(str(sentinel) not in str(plan.commands) for plan in plans)
    assert not sentinel.exists()


def test_rejects_changed_or_fabricated_source_identity(tmp_path):
    candidate = source_candidate(tmp_path)
    write(candidate.source_root, "Cargo.lock", "changed\n")
    with pytest.raises(builds.CandidateBuildError, match="source.*changed"):
        builds.plan_candidate_builds(candidate)
    with pytest.raises(builds.CandidateBuildError, match="source.*identity"):
        builds.plan_candidate_builds(replace(candidate, source_tree_digest="invalid"))


def test_rejects_unsupported_source_layout(tmp_path):
    candidate = source_candidate(tmp_path)
    (candidate.source_root / "crates/amaru/src/main.rs").unlink()
    with pytest.raises(builds.CandidateBuildError, match="unsupported layout"):
        builds.plan_candidate_builds(candidate)


@pytest.mark.parametrize("implementation", ["amaru", "cardano-node"])
def test_required_patch_rejects_incompatible_source_without_version_restriction(tmp_path, implementation):
    candidate = source_candidate(tmp_path, implementation)
    plans = builds.plan_candidate_builds(candidate)
    assert any("patch" in reason for reason in plans[1].unresolved)
    with pytest.raises(builds.CandidateBuildError, match="compatible.*patch"):
        builds.plan_candidate_builds(candidate, require_patch=True)


def test_toolchain_is_source_input_and_unknown_toolchain_stays_unresolved(tmp_path):
    candidate = source_candidate(tmp_path, toolchain="nightly-2028-01-01")
    plans = builds.plan_candidate_builds(candidate)
    assert ("rust", "nightly-2028-01-01") in plans[0].toolchain
    assert "nightly-2028-01-01" in " ".join(plans[0].commands[0])
    (candidate.source_root / "rust-toolchain.toml").unlink()
    candidate = discover_candidates(tmp_path, repository_commit=COMMIT)[0]
    plan = builds.plan_candidate_builds(candidate)[0]
    assert any("toolchain" in reason for reason in plan.unresolved)


def test_builtin_recipe_and_assets_are_retained_and_revalidated(tmp_path):
    candidate = source_candidate(tmp_path)
    plan = builds.plan_candidate_builds(candidate)[-1]
    assert any(row.path.endswith("build_amaru_coverage_target.py") for row in plan.inputs)
    assert any(row.path.endswith("fuzz_targets/plutus_data.rs") for row in plan.inputs)
    for row in plan.inputs:
        if row.origin == "trusted":
            assert hashlib.sha256((builds.TRUSTED_ROOT / row.path).read_bytes()).hexdigest() == row.sha256
    builds.validate_candidate_build_plan(plan)
    with pytest.raises(builds.CandidateBuildError, match="plan.*digest"):
        builds.validate_candidate_build_plan(replace(plan, commands=(("sh", "evil.sh"),)))


def test_missing_trusted_patch_is_rejected(tmp_path, monkeypatch):
    candidate = source_candidate(tmp_path)
    patch = next((builds.TRUSTED_ROOT / "dwarf/targets/amaru/measurement-patches-nanoseconds-v3").glob("*/*.patch"))
    original = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda path: False if path == patch else original(path))
    with pytest.raises(builds.CandidateBuildError, match="missing.*patch"):
        builds.plan_candidate_builds(candidate)


def trusted_patch(tmp_path, monkeypatch, implementation="amaru"):
    """A small DWARF-owned patch catalog; exercise real digest/preimage helpers."""
    trusted = tmp_path / "trusted"
    for relative in (
        "dwarf/profile_manager/candidate_builds.py", "dwarf/profile_manager/testplease_candidates.py",
        "dwarf/profile_manager/candidate_build_environments.py",
        "dwarf/spec/v1/candidate-build-environment.schema.json",
        "dwarf/spec/v1/candidate-dependency-closure.schema.json",
        f"dwarf/scripts/build_{'amaru' if implementation == 'amaru' else 'cardano'}_measurement_target.py",
        f"dwarf/scripts/build_{'amaru' if implementation == 'amaru' else 'cardano'}_coverage_target.py",
    ):
        dest = trusted / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(builds.TRUSTED_ROOT / relative, dest)
    coverage = builds._COVERAGE[implementation]
    shutil.copytree((builds.TRUSTED_ROOT / coverage).parent, (trusted / coverage).parent)
    patch_path = "dwarf/targets/test-patch/patches/change.patch"
    patch = write(trusted, patch_path, "--- a/input.txt\n+++ b/input.txt\n@@ -1 +1 @@\n-before\n+after\n")
    patch_row = {"path": "patches/change.patch", "sha256": hashlib.sha256(patch.read_bytes()).hexdigest(),
                 "preimages": [{"path": "input.txt", "sha256": hashlib.sha256(b"before\n").hexdigest()}]}
    dependencies = []
    if implementation == "cardano-node":
        patch_row["root"] = "dependency:example-1.0.0"
        dependencies = [{"package": "example", "version": "1.0.0", "archive": "example-1.0.0.tar.gz", "sha256": "a" * 64}]
    manifest = {
        "patches": [patch_row],
        "patch_set_sha256": hashlib.sha256(f'{patch_row["sha256"]}  {patch_row["path"]}\n'.encode()).hexdigest(),
        "source": {"revision": "a" * 40},
        "toolchain": "nightly-2026-09-04" if implementation == "amaru" else {"ghc": "9.6.7", "cabal": "3.16.0.0"},
        "base_image": "example.invalid/runtime@sha256:" + "b" * 64,
        "dependencies": dependencies,
    }
    path = write(trusted, "dwarf/targets/test-patch/manifest.json", json.dumps(manifest))
    monkeypatch.setattr(builds, "TRUSTED_ROOT", trusted)
    monkeypatch.setattr(builds, "_PATCHES", {implementation: (path.relative_to(trusted).as_posix(),)})
    return manifest, patch


@pytest.mark.parametrize("implementation", ["amaru", "cardano-node"])
def test_content_compatible_patch_retains_exact_reference_and_inputs(tmp_path, monkeypatch, implementation):
    candidate = source_candidate(tmp_path / "repo", implementation)
    manifest, _ = trusted_patch(tmp_path, monkeypatch, implementation)
    if implementation == "amaru":
        write(candidate.source_root, "input.txt", "before\n")
    else:
        write(candidate.source_root, "cabal.project.freeze", "constraints: any.example ==1.0.0\n")
    candidate = discover_candidates(tmp_path / "repo", repository_commit=COMMIT)[0]
    patched = builds.plan_candidate_builds(candidate, require_patch=True)[1]
    assert patched.patch_set_digest == manifest["patch_set_sha256"]
    assert patched.asset_reference_revision == "a" * 40
    assert patched.repository_commit == COMMIT  # Never substitute asset upstream revision.
    assert patched.base_image == manifest["base_image"]
    assert any(row.path == "patches/change.patch" or row.path.endswith("/patches/change.patch") for row in patched.inputs)
    assert any(row.path == "input.txt" for row in patched.inputs)
    assert patched.status == "unresolved"
    builds.validate_candidate_build_plan(patched)


def test_required_patch_rejects_toolchain_incompatibility(tmp_path, monkeypatch):
    candidate = source_candidate(tmp_path / "repo", toolchain="nightly-2028-01-01")
    trusted_patch(tmp_path, monkeypatch)
    write(candidate.source_root, "input.txt", "before\n")
    candidate = discover_candidates(tmp_path / "repo", repository_commit=COMMIT)[0]
    with pytest.raises(builds.CandidateBuildError, match="patch.*toolchain"):
        builds.plan_candidate_builds(candidate, require_patch=True)


def test_checked_in_amaru_candidate_selects_its_exact_reviewed_patch():
    source_root = builds.TRUSTED_ROOT / "dwarf/testplease/amaru/v10.11.20260925"
    if not source_root.is_dir():
        pytest.skip("public testplease candidate is not mirrored in this repository")
    candidates = discover_candidates(builds.TRUSTED_ROOT, repository_commit=COMMIT)
    candidate = next(
        row
        for row in candidates
        if row.source_root == source_root
    )

    patched = builds.plan_candidate_builds(candidate, require_patch=True)[1]

    assert patched.asset_reference_revision == "eaf8ac3fa309f2a174c045454c276194c9312b8d"
    assert patched.patch_set_digest is not None
    assert any(
        row.path.endswith(
            "measurement-patches-nanoseconds-v3/"
            "eaf8ac3fa309f2a174c045454c276194c9312b8d/"
            "0001-dwarf-measurement-nanoseconds-v3.patch"
        )
        for row in patched.inputs
    )


def test_changed_trusted_recipe_and_patch_are_rejected(tmp_path, monkeypatch):
    candidate = source_candidate(tmp_path / "repo")
    _, patch = trusted_patch(tmp_path, monkeypatch)
    plan = builds.plan_candidate_builds(candidate)[0]
    write(builds.TRUSTED_ROOT, "dwarf/scripts/build_amaru_measurement_target.py", "changed trusted recipe\n")
    with pytest.raises(builds.CandidateBuildError, match="trusted.*changed"):
        builds.validate_candidate_build_plan(plan)
    patch.write_text("changed patch\n")
    with pytest.raises(builds.CandidateBuildError, match="patch digest mismatch"):
        builds.plan_candidate_builds(candidate)


def test_patch_and_harness_staging_commands_have_explicit_working_directories(tmp_path, monkeypatch):
    candidate = source_candidate(tmp_path / "repo")
    trusted_patch(tmp_path, monkeypatch)
    write(candidate.source_root, "input.txt", "before\n")
    candidate = discover_candidates(tmp_path / "repo", repository_commit=COMMIT)[0]
    stock, patched, coverage = builds.plan_candidate_builds(candidate)
    for plan in (stock, patched, coverage):
        assert len(plan.command_workdirs) == len(plan.commands)
        assert all(workdir.startswith("{source}") for workdir in plan.command_workdirs)
    assert patched.commands[0][:3] == ("git", "apply", "--check")
    assert patched.commands[1][:2] == ("git", "apply")
    assert coverage.staging_inputs
    assert all(row.origin == "trusted" for row in coverage.staging_inputs)
    assert coverage.staging_directory == "dwarf-coverage-fuzz"


@pytest.mark.parametrize("implementation", ["amaru", "cardano-node"])
def test_output_receipt_binds_all_build_inputs_and_separates_envelope_from_build(tmp_path, implementation):
    coverage = builds.plan_candidate_builds(source_candidate(tmp_path, implementation))[-1]
    assert set(coverage.receipt_identity_fields) >= {
        "plan_digest", "source_tree_digest", "repository_commit", "variant", "recipe_digest",
        "toolchain", "commands", "command_workdirs", "environment", "inputs", "compiler_flags",
        "patch_set_digest", "harness_digest", "base_image", "build_container_image",
    }
    assert coverage.coverage_envelope_digest is None
    assert "coverage_envelope_sha256" in coverage.required_runtime_outputs
    assert coverage.required_runtime_outputs == ("coverage_envelope_sha256",)
    assert not hasattr(coverage, "mark_built")


def test_cardano_plans_retain_generated_project_and_dependency_patch_environment(tmp_path, monkeypatch):
    candidate = source_candidate(tmp_path / "repo", "cardano-node")
    trusted_patch(tmp_path, monkeypatch, "cardano-node")
    write(candidate.source_root, "cabal.project.freeze", "constraints: any.example ==1.0.0\n")
    candidate = discover_candidates(tmp_path / "repo", repository_commit=COMMIT)[0]
    _, patched, coverage = builds.plan_candidate_builds(candidate, require_patch=True)
    assert patched.generated_inputs == (("cabal.project.local", "packages: vendor/example-1.0.0\n"),)
    assert patched.command_environments[0] == (("GIT_CEILING_DIRECTORIES", "{source}/vendor"),)
    assert patched.command_environments[1] == patched.command_environments[0]
    assert patched.command_environments[-1] == ()
    project = dict(coverage.generated_inputs)["cabal.project.local"]
    assert "dwarf-coverage-harness vendor/cardano-diffusion-1.1.1.0" in project
    assert "package cardano-diffusion\n  ghc-options: -fhpc\n" in project
    assert "generated_inputs" in coverage.receipt_identity_fields


def test_refuses_to_replace_existing_generated_project(tmp_path):
    candidate = source_candidate(tmp_path, "cardano-node")
    write(candidate.source_root, "cabal.project.local", "packages: some-custom-package\n")
    candidate = discover_candidates(tmp_path, repository_commit=COMMIT)[0]
    coverage = builds.plan_candidate_builds(candidate)[-1]
    assert any("cabal.project.local already exists" in reason for reason in coverage.unresolved)


def test_cardano_binary_queries_match_trusted_manifest_compiler_and_build_directory(tmp_path):
    manifest = json.loads((builds.TRUSTED_ROOT / builds._PATCHES["cardano-node"][0]).read_text())
    candidate = source_candidate(tmp_path, "cardano-node")
    write(candidate.source_root, "cabal.project", f'packages: cardano-node\nwith-compiler: ghc-{manifest["toolchain"]["ghc"]}\n')
    write(candidate.source_root, "cabal.project.freeze", "constraints: " + ",\n".join(
        f'any.{row["package"]} =={row["version"]}' for row in manifest["dependencies"]
    ) + "\n")
    candidate = discover_candidates(tmp_path, repository_commit=COMMIT)[0]
    stock, patched, coverage = builds.plan_candidate_builds(candidate, require_patch=True)
    assert patched.patch_set_digest == manifest["patch_set_sha256"]
    for plan in (stock, patched):
        build = plan.commands[-1]
        builddir = next(arg for arg in build if arg.startswith("--builddir="))
        expected = tuple(builddir if arg.startswith("--builddir=") else arg
                         for arg in manifest["build"]["binary_query"])
        assert plan.binary_query == expected
        assert plan.binary_query[2:] == build[2:]
        assert "binary_query" in plan.receipt_identity_fields
        assert "binary" in plan.receipt_identity_fields
        builds.validate_candidate_build_plan(plan)
        with pytest.raises(builds.CandidateBuildError, match="plan.*digest"):
            builds.validate_candidate_build_plan(replace(plan, binary_query=("cabal", "list-bin", "exe:cardano-node")))
    assert coverage.binary_query == (
        "cabal", "list-bin", "-w", f'ghc-{manifest["toolchain"]["ghc"]}',
        "exe:cardano-node-production-handshake-coverage",
    )
    assert all(plan.status == "unresolved" for plan in (stock, patched, coverage))
