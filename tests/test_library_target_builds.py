from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import stat
import threading
import time
from types import SimpleNamespace

import pytest

import controller.library_target_builds as library_builds
import profile_manager.launch_store as launch_store
from controller.library_target_builds import (
    LibraryTargetBuildError,
    prepare_library_targets,
)
from controller.docker_runtime import DockerRuntimeError
from profile_manager.launch_store import (
    LaunchStoreError,
    _freeze_prepared_library_targets,
    materialized_scenario,
)
from profile_manager.library_targets import (
    LibraryTargetError,
    library_target_requirements,
)
from profile_manager.primitives import load_target_manifest
from profile_manager.run_plan import RunPlanRequest, resolve_run_plan
from profile_manager.scenario import PrimitiveRef
from scripts import amaru_library_target_builder


NEW_AMARU_LIBRARY_TARGETS = (
    ("amaru-cbor-block-fuzz", "amaru-cbor-decode-block"),
    ("amaru-cbor-block-header-fuzz", "amaru-cbor-decode-block-header"),
    ("amaru-cbor-certificate-fuzz", "amaru-cbor-decode-certificate"),
    ("amaru-cbor-auxiliary-data-fuzz", "amaru-cbor-decode-auxiliary-data"),
)


class FakeRuntime:
    def __init__(self, root: Path):
        self.state_dir = root / "controller-state"
        self.host_state_dir = self.state_dir
        self.installation_id = "test-installation"


class FakeDockerRuntime(FakeRuntime):
    def __init__(self, root: Path):
        super().__init__(root)
        self.commands = []
        self.command_kwargs = []
        self.labels = {}
        self.workspace_modes = []
        self.build_command = [
            "cargo",
            "+nightly-2026-09-04",
            "build",
            "--locked",
            "--release",
            "-p",
            "dwarf-amaru-library-target",
        ]
        self.wrong_upstream_lock_digest = False

    def _run(self, arguments, **_kwargs):
        arguments = tuple(arguments)
        self.commands.append(arguments)
        self.command_kwargs.append(dict(_kwargs))
        if arguments[0] == "run" and "--entrypoint" in arguments:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if arguments[0] == "run":
            mount = arguments[arguments.index("--mount") + 1]
            workspace = Path(mount.split("src=", 1)[1].split(",dst=", 1)[0])
            self.workspace_modes.append(stat.S_IMODE(workspace.stat().st_mode))
            request = json.loads((workspace / "request.json").read_text())
            assert (workspace / "builder.py").is_file()
            assert (workspace / "target.rs").is_file()
            binary = workspace / "executable"
            binary.write_bytes(b"\x7fELF execution-host builder")
            binary.chmod(0o755)
            effective_lock = workspace / "effective-Cargo.lock"
            effective_lock.write_text("version = 4\n# selected source lock\n")
            upstream_lock = workspace / "upstream-Cargo.lock"
            upstream_lock.write_text("version = 4\n")
            result = {
                "schema_version": "dwarf.library-target-builder-result.v1",
                "transform_version": request["transform_version"],
                "source_commit": request["source_revision"],
                "source_tree": "2" * 40,
                "target_id": request["target_id"],
                "toolchain": "nightly-2026-09-04",
                "rustc_version": "rustc 1.93.0-nightly (test)",
                "cargo_version": "cargo 1.93.0-nightly (test)",
                "upstream_lock_sha256": (
                    "sha256:" + "3" * 64
                    if self.wrong_upstream_lock_digest
                    else "sha256:"
                    + hashlib.sha256(upstream_lock.read_bytes()).hexdigest()
                ),
                "effective_lock_sha256": "sha256:"
                + hashlib.sha256(effective_lock.read_bytes()).hexdigest(),
                "build_command": self.build_command,
                "binary_sha256": "sha256:"
                + hashlib.sha256(binary.read_bytes()).hexdigest(),
                "smoke": {"return_code": 1, "stdout": "ERR eof", "stderr": ""},
            }
            (workspace / "result.json").write_text(json.dumps(result))
            name = arguments[arguments.index("--name") + 1]
            labels = {}
            for index, value in enumerate(arguments):
                if value == "--label":
                    key, selected = arguments[index + 1].split("=", 1)
                    labels[key] = selected
            self.labels[name] = labels
            return SimpleNamespace(returncode=0, stdout="build ok\n", stderr="")
        if arguments[:2] == ("container", "inspect"):
            name = arguments[2]
            if name not in self.labels:
                return SimpleNamespace(returncode=1, stdout="[]", stderr="missing")
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps([{"Config": {"Labels": self.labels[name]}}]),
                stderr="",
            )
        if arguments[0] == "rm":
            self.labels.pop(arguments[-1], None)
            return SimpleNamespace(returncode=0, stdout=arguments[-1], stderr="")
        raise AssertionError(arguments)


class OwnershipBoundaryRuntime(FakeDockerRuntime):
    def __init__(self, root: Path):
        super().__init__(root)
        self.ownership_restored = False

    def _run(self, arguments, **kwargs):
        arguments = tuple(arguments)
        if arguments[0] == "run" and "--entrypoint" in arguments:
            self.commands.append(arguments)
            self.command_kwargs.append(dict(kwargs))
            self.ownership_restored = True
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        result = super()._run(arguments, **kwargs)
        if arguments[0] == "run":
            mount = arguments[arguments.index("--mount") + 1]
            workspace = Path(mount.split("src=", 1)[1].split(",dst=", 1)[0])
            disposable = workspace / "source" / "target"
            disposable.mkdir(parents=True)
            (disposable / "builder-owned").write_text("root-or-nobody-owned\n")
        return result


def _requirement(
    version: str = "10.11.20260925",
    *,
    scenario_id: str = "amaru-cbor-tx-body-fuzz",
) -> dict:
    plan = resolve_run_plan(
        RunPlanRequest(
            scenario_id=scenario_id,
            version_policy="exact",
            amaru_version=version,
            acknowledge_unknown_versions=True,
        ),
        capability_context={"host_os": "linux", "host_arch": "amd64"},
    )
    return plan["scenario"]["library_target_requirements"][0]


def _differential_candidate_scenario(
    candidate_target_id: str,
    *,
    manifests_dir: str = "dwarf/targets/manifests",
):
    empty = []
    return SimpleNamespace(
        runtime="library",
        target={"implementation": "amaru", "version": "any"},
        phases=[],
        setup=empty,
        load=[
            PrimitiveRef(
                primitive="runtime_cardano_cbor_dataset_differential",
                params={
                    "candidate_target_id": candidate_target_id,
                    "reference_target_id": "cardano-node-cbor-decode-tx-body",
                    "manifests_dir": manifests_dir,
                },
            )
        ],
        faults=empty,
        probes=empty,
        assertions=empty,
        teardown=empty,
    )


def _candidate_requirements(candidate_target_id: str, **scenario_options):
    return library_target_requirements(
        _differential_candidate_scenario(
            candidate_target_id,
            **scenario_options,
        ),
        release={
            "implementation": "amaru",
            "version": "10.11.20261029",
            "source_revision": "23031e0ed4423b9cb899da030d515cb69d203749",
        },
        catalog={"sources": {"amaru": "https://github.com/pragma-org/amaru.git"}},
        platform="linux/amd64",
    )


def test_differential_candidate_yields_one_on_demand_library_requirement():
    requirements = _candidate_requirements("amaru-cbor-decode-tx-body")

    assert len(requirements) == 1
    assert requirements[0]["target_id"] == "amaru-cbor-decode-tx-body"
    assert requirements[0]["location"] == ["load", 0]


def test_differential_candidate_rejects_unsupported_amaru_target():
    with pytest.raises(LibraryTargetError, match="explicitly approved Amaru targets"):
        _candidate_requirements("amaru-cbor-decode-plutus-data")


def test_differential_candidate_requires_canonical_manifest_catalog():
    with pytest.raises(LibraryTargetError, match="packaged target manifest catalog"):
        _candidate_requirements(
            "amaru-cbor-decode-tx-body",
            manifests_dir="targets/manifests",
        )


def _materialize_library_binding(
    tmp_path, monkeypatch, entry, *, binding_target="amaru-cbor-decode-tx-body"
):
    scenario_id = "differential-library-binding-regression"
    original = {
        "id": scenario_id,
        "target": {"implementation": "amaru", "version": "any"},
        "seed": 1,
        "load": [entry],
    }
    body = json.dumps(original).encode()
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    (scenarios / f"{scenario_id}.yaml").write_bytes(body)
    monkeypatch.setattr(launch_store, "_scenarios_dir", lambda: scenarios)
    plan = {
        "scenario": {
            "id": scenario_id,
            "digest": "sha256:" + hashlib.sha256(body).hexdigest(),
            "target": original["target"],
            "seed": original["seed"],
            "library_target_bindings": [
                {
                    "location": ["load", 0],
                    "target_id": binding_target,
                    "manifests_dir": str(tmp_path / "frozen-manifests"),
                }
            ],
        }
    }
    return json.loads(materialized_scenario(plan))


def test_differential_candidate_binding_rejects_mismatched_candidate_target(
    tmp_path, monkeypatch
):
    with pytest.raises(LaunchStoreError, match="library target declaration changed"):
        _materialize_library_binding(
            tmp_path,
            monkeypatch,
            {
                "primitive": "runtime_cardano_cbor_dataset_differential",
                "candidate_target_id": "amaru-cbor-decode-block",
                "manifests_dir": "dwarf/targets/manifests",
            },
        )


def test_differential_reference_target_is_never_used_as_candidate_binding(
    tmp_path, monkeypatch
):
    with pytest.raises(LaunchStoreError, match="library target declaration changed"):
        _materialize_library_binding(
            tmp_path,
            monkeypatch,
            {
                "primitive": "runtime_cardano_cbor_dataset_differential",
                "reference_target_id": "amaru-cbor-decode-tx-body",
                "manifests_dir": "dwarf/targets/manifests",
            },
        )


def test_normal_library_target_binding_materialization_is_unchanged(
    tmp_path, monkeypatch
):
    materialized = _materialize_library_binding(
        tmp_path,
        monkeypatch,
        {
            "primitive": "cbor_fuzz",
            "target_id": "amaru-cbor-decode-tx-body",
            "manifests_dir": "dwarf/targets/manifests",
        },
    )

    assert materialized["load"][0]["target_id"] == "amaru-cbor-decode-tx-body"
    assert materialized["load"][0]["manifests_dir"] == str(
        tmp_path / "frozen-manifests"
    )


@pytest.mark.parametrize(
    "target_id,supported",
    [
        *[(item[1], True) for item in NEW_AMARU_LIBRARY_TARGETS],
        ("amaru-cbor-decode-plutus-data", False),
    ],
)
def test_builder_request_accepts_only_explicitly_supported_amaru_targets(
    tmp_path, target_id, supported
):
    (tmp_path / "request.json").write_text(
        json.dumps(
            {
                "schema_version": "dwarf.library-target-builder-request.v1",
                "transform_version": "amaru-library-target-v2",
                "source_repository": "https://github.com/pragma-org/amaru.git",
                "source_revision": "a" * 40,
                "target_id": target_id,
                "target_source_sha256": "sha256:" + "b" * 64,
            }
        ),
        encoding="utf-8",
    )

    if supported:
        assert amaru_library_target_builder._request(tmp_path)["target_id"] == target_id
    else:
        with pytest.raises(ValueError, match="builder request identity"):
            amaru_library_target_builder._request(tmp_path)


def _builder(calls: list[str], *, delay: float = 0):
    lock = threading.Lock()

    def build(requirement, workspace, log_path, **_kwargs):
        with lock:
            calls.append(requirement["cache_key"])
        if delay:
            time.sleep(delay)
        executable = workspace / "executable"
        executable.write_bytes(
            b"\x7fELF exact amaru library target "
            + requirement["source_revision"].encode("ascii")
        )
        executable.chmod(0o755)
        effective_lock = workspace / "effective-Cargo.lock"
        effective_lock.write_text("version = 4\n# selected source lock\n")
        upstream_lock = workspace / "upstream-Cargo.lock"
        upstream_lock.write_text("version = 4\n")
        log_path.write_text("compiled exact source\n", encoding="utf-8")
        return {
            "source_commit": requirement["source_revision"],
            "source_tree": "1" * 40,
            "selected_toolchain": "nightly-2026-09-04",
            "rustc_version": "rustc 1.93.0-nightly (test)",
            "cargo_version": "cargo 1.93.0-nightly (test)",
            "upstream_lock_sha256": "sha256:"
            + hashlib.sha256(upstream_lock.read_bytes()).hexdigest(),
            "effective_lock_sha256": "sha256:"
            + hashlib.sha256(effective_lock.read_bytes()).hexdigest(),
            "build_command": [
                "cargo",
                "+nightly-2026-09-04",
                "build",
                "--locked",
                "--release",
                "-p",
                "dwarf-amaru-library-target",
            ],
        }

    return build


def test_cache_miss_builds_then_verified_hit_reuses_without_rebuild(tmp_path):
    runtime = FakeRuntime(tmp_path)
    requirement = _requirement()
    calls: list[str] = []

    build = _builder(calls)

    def build_with_disposable_files(*args, **kwargs):
        for name in ("source", "target", "home", ".cargo", ".rustup"):
            path = args[1] / name
            path.mkdir()
            (path / "discard-me").write_text("not retained\n")
        return build(*args, **kwargs)

    second_requirement = deepcopy(requirement)
    second_requirement["location"] = ["phases", 2, "load", 7]
    first = prepare_library_targets(
        runtime,
        [requirement],
        execution_id="library-build-first",
        timeout=30,
        cancel_event=threading.Event(),
        builder=build_with_disposable_files,
    )[0]
    second = prepare_library_targets(
        runtime,
        [second_requirement],
        execution_id="library-build-second",
        timeout=30,
        cancel_event=threading.Event(),
        builder=_builder(calls),
    )[0]

    assert calls == [requirement["cache_key"]]
    assert first["cache_status"] == "built"
    assert second["cache_status"] == "verified-hit"
    assert first["binary_sha256"] == second["binary_sha256"]
    assert Path(second["binary"]).is_file()
    assert Path(second["receipt"]).is_file()
    assert Path(second["build_log"]).read_text() == "compiled exact source\n"
    assert second["source_revision"] == requirement["source_revision"]
    assert second["recipe_digest"] == requirement["recipe_digest"]
    assert second["builder_image"] == requirement["builder_image"]

    assert first["location"] == ["load", 0]
    assert second["location"] == second_requirement["location"]
    assert sorted(path.name for path in Path(first["binary"]).parent.iterdir()) == [
        "build.log",
        "effective-Cargo.lock",
        "executable",
        "receipt.json",
        "upstream-Cargo.lock",
    ]


@pytest.mark.parametrize(
    "changed_input",
    (
        "dwarf/targets/amaru/src/bin/decode_tx_body.rs",
        "dwarf/scripts/amaru_library_target_builder.py",
        "dwarf/targets/amaru/Cargo.toml",
    ),
)
def test_cache_hit_revalidates_every_trusted_recipe_input(
    tmp_path, monkeypatch, changed_input
):
    requirement = _requirement()
    trusted_root = tmp_path / "trusted-root"
    for item in requirement["recipe_inputs"]:
        source = library_builds.ROOT / item["path"]
        destination = trusted_root / item["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
    monkeypatch.setattr(library_builds, "ROOT", trusted_root)
    runtime = FakeRuntime(tmp_path)
    prepare_library_targets(
        runtime,
        [requirement],
        execution_id="library-recipe-build",
        timeout=30,
        cancel_event=threading.Event(),
        builder=_builder([]),
    )
    changed = trusted_root / changed_input
    changed.write_bytes(changed.read_bytes() + b"\n# changed after cache publication\n")

    with pytest.raises(LibraryTargetBuildError, match="recipe identity changed"):
        prepare_library_targets(
            runtime,
            [requirement],
            execution_id="library-recipe-hit",
            timeout=30,
            cancel_event=threading.Event(),
            builder=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("changed recipe must not reuse or rebuild this key")
            ),
        )


def test_default_builder_restores_builder_owned_workspace_before_cleanup(
    tmp_path, monkeypatch
):
    runtime = OwnershipBoundaryRuntime(tmp_path)
    requirement = _requirement()
    original_rmtree = library_builds.shutil.rmtree

    def require_ownership_restore(path, *args, **kwargs):
        selected = Path(path)
        if (
            selected.name.startswith("library-ownership-boundary-")
            and (selected / "source" / "target" / "builder-owned").exists()
            and not runtime.ownership_restored
        ):
            raise PermissionError("builder-owned workspace cannot be removed")
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(library_builds.shutil, "rmtree", require_ownership_restore)

    prepared = prepare_library_targets(
        runtime,
        [requirement],
        execution_id="library-ownership-boundary",
        timeout=30,
        cancel_event=threading.Event(),
    )[0]

    cleanup = next(
        command
        for command in runtime.commands
        if command[0] == "run" and "--entrypoint" in command
    )
    assert cleanup[cleanup.index("--entrypoint") + 1] == "chown"
    assert "--no-dereference" in cleanup
    assert cleanup[cleanup.index("--name") + 1].endswith("-ownership")
    capabilities = [
        cleanup[index + 1]
        for index, value in enumerate(cleanup)
        if value == "--cap-add"
    ]
    assert capabilities == ["CHOWN", "DAC_OVERRIDE"]
    assert cleanup[cleanup.index("--network") + 1] == "none"
    assert runtime.ownership_restored is True
    assert prepared["cache_status"] == "built"
    assert not any((runtime.state_dir / "library-targets" / "staging").iterdir())

    command = next(item for item in runtime.commands if item[0] == "run")
    command_index = runtime.commands.index(command)
    command_kwargs = runtime.command_kwargs[command_index]
    assert command[command.index("--platform") + 1] == "linux/amd64"
    assert requirement["builder_image"] in command
    assert command[-2:] == ("python", "/work/builder.py")
    assert runtime.workspace_modes == [0o755]
    assert command_kwargs["execution_id"] == "library-ownership-boundary"
    assert Path(command_kwargs["output_path"]).name == "build.log"
    assert prepared["cache_status"] == "built"
    assert prepared["source_revision"] == requirement["source_revision"]
    assert prepared["source_tree"] == "2" * 40
    assert Path(prepared["upstream_lock"]).is_file()


def test_cleanup_failure_keeps_cache_unpublished_and_retains_complete_build_log(
    tmp_path, monkeypatch
):
    runtime = FakeRuntime(tmp_path)
    requirement = _requirement()
    original_rmtree = library_builds.shutil.rmtree
    cleanup_failed = False

    def fail_after_log_would_be_lost(path, *args, **kwargs):
        nonlocal cleanup_failed
        selected = Path(path)
        if (
            not cleanup_failed
            and selected.name.startswith("library-cleanup-evidence-")
        ):
            cleanup_failed = True
            (selected / "build.log").unlink()
            raise PermissionError("disposable workspace cleanup refused")
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(library_builds.shutil, "rmtree", fail_after_log_would_be_lost)

    with pytest.raises(
        LibraryTargetBuildError, match="disposable workspace cleanup refused"
    ):
        prepare_library_targets(
            runtime,
            [requirement],
            execution_id="library-cleanup-evidence",
            timeout=30,
            cancel_event=threading.Event(),
            builder=_builder([]),
        )

    cache = runtime.state_dir / "library-targets" / "cache"
    assert list(cache.iterdir()) == []
    retained_logs = list(
        (runtime.state_dir / "library-targets" / "failures").rglob("build.log")
    )
    assert len(retained_logs) == 1
    assert retained_logs[0].read_text() == "compiled exact source\n"


def test_default_builder_rejects_command_without_locked_selected_toolchain(tmp_path):
    runtime = FakeDockerRuntime(tmp_path)
    runtime.build_command = ["cargo", "build", "--release"]

    with pytest.raises(LibraryTargetBuildError, match="build command"):
        prepare_library_targets(
            runtime,
            [_requirement()],
            execution_id="library-unlocked-command",
            timeout=30,
            cancel_event=threading.Event(),
        )


def test_default_builder_rejects_upstream_lock_digest_without_retained_file_match(
    tmp_path,
):
    runtime = FakeDockerRuntime(tmp_path)
    runtime.wrong_upstream_lock_digest = True

    with pytest.raises(LibraryTargetBuildError, match="upstream Cargo lock"):
        prepare_library_targets(
            runtime,
            [_requirement()],
            execution_id="library-upstream-lock-mismatch",
            timeout=30,
            cancel_event=threading.Event(),
        )


def test_cancellation_during_build_prevents_cache_publication(tmp_path):
    runtime = FakeRuntime(tmp_path)
    requirement = _requirement()
    cancel_event = threading.Event()
    calls: list[str] = []
    build = _builder(calls)

    def cancel_before_return(*args, **kwargs):
        result = build(*args, **kwargs)
        cancel_event.set()
        return result

    with pytest.raises(LibraryTargetBuildError, match="cancelled"):
        prepare_library_targets(
            runtime,
            [requirement],
            execution_id="library-build-cancelled",
            timeout=30,
            cancel_event=cancel_event,
            builder=cancel_before_return,
        )

    destination = (
        runtime.state_dir
        / "library-targets"
        / "cache"
        / requirement["cache_key"].removeprefix("sha256:")
    )
    assert not destination.exists()


class PartialLogDockerRuntime(FakeDockerRuntime):
    def _run(self, arguments, **kwargs):
        arguments = tuple(arguments)
        if arguments[0] != "run":
            return super()._run(arguments, **kwargs)
        if "--entrypoint" in arguments:
            return super()._run(arguments, **kwargs)
        self.commands.append(arguments)
        self.command_kwargs.append(dict(kwargs))
        mount = arguments[arguments.index("--mount") + 1]
        workspace = Path(mount.split("src=", 1)[1].split(",dst=", 1)[0])
        self.workspace_modes.append(stat.S_IMODE(workspace.stat().st_mode))
        name = arguments[arguments.index("--name") + 1]
        labels = {}
        for index, value in enumerate(arguments):
            if value == "--label":
                key, selected = arguments[index + 1].split("=", 1)
                labels[key] = selected
        self.labels[name] = labels
        output_path = kwargs.get("output_path")
        if output_path is not None:
            Path(output_path).write_text("partial compiler diagnostic\n")
        raise DockerRuntimeError("Docker operation timed out")


def test_default_builder_retains_partial_log_when_runtime_raises(tmp_path):
    runtime = PartialLogDockerRuntime(tmp_path)
    requirement = _requirement()

    with pytest.raises(LibraryTargetBuildError, match="timed out"):
        prepare_library_targets(
            runtime,
            [requirement],
            execution_id="library-build-partial-log",
            timeout=30,
            cancel_event=threading.Event(),
        )

    retained = list(
        (runtime.state_dir / "library-targets" / "failures").rglob("build.log")
    )
    assert len(retained) == 1
    assert retained[0].read_text() == "partial compiler diagnostic\n"

    cache = runtime.state_dir / "library-targets" / "cache"
    assert not (cache / requirement["cache_key"].removeprefix("sha256:")).exists()


def test_exact_version_change_uses_a_distinct_cache_entry(tmp_path):
    runtime = FakeRuntime(tmp_path)
    calls: list[str] = []
    first_requirement = _requirement("10.11.20260918")
    second_requirement = _requirement("10.11.20260925")

    first = prepare_library_targets(
        runtime,
        [first_requirement],
        execution_id="library-version-first",
        timeout=30,
        cancel_event=threading.Event(),
        builder=_builder(calls),
    )[0]
    second = prepare_library_targets(
        runtime,
        [second_requirement],
        execution_id="library-version-second",
        timeout=30,
        cancel_event=threading.Event(),
        builder=_builder(calls),
    )[0]

    assert len(calls) == 2
    assert first["cache_key"] != second["cache_key"]
    assert first["source_revision"] != second["source_revision"]
    assert first_requirement["version"] != second_requirement["version"]
    assert Path(first["binary"]).parent != Path(second["binary"]).parent


def test_tampered_cached_binary_is_rejected_without_rebuild(tmp_path):
    runtime = FakeRuntime(tmp_path)
    requirement = _requirement()
    calls: list[str] = []
    built = prepare_library_targets(
        runtime,
        [requirement],
        execution_id="library-build-tamper-source",
        timeout=30,
        cancel_event=threading.Event(),
        builder=_builder(calls),
    )[0]
    binary = Path(built["binary"])
    binary.chmod(0o700)
    binary.write_bytes(b"tampered")

    with pytest.raises(LibraryTargetBuildError, match="binary digest"):
        prepare_library_targets(
            runtime,
            [requirement],
            execution_id="library-build-tamper-check",
            timeout=30,
            cancel_event=threading.Event(),
            builder=_builder(calls),
        )

    assert calls == [requirement["cache_key"]]


def test_wrong_source_cache_receipt_is_rejected_without_rebuild(tmp_path):
    runtime = FakeRuntime(tmp_path)
    requirement = _requirement()
    calls: list[str] = []
    built = prepare_library_targets(
        runtime,
        [requirement],
        execution_id="library-build-receipt-source",
        timeout=30,
        cancel_event=threading.Event(),
        builder=_builder(calls),
    )[0]
    receipt_path = Path(built["receipt"])
    receipt = json.loads(receipt_path.read_text())
    receipt["source_revision"] = "0" * 40
    receipt_path.chmod(0o600)
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(LibraryTargetBuildError, match="source identity"):
        prepare_library_targets(
            runtime,
            [requirement],
            execution_id="library-build-receipt-check",
            timeout=30,
            cancel_event=threading.Event(),
            builder=_builder(calls),
        )

    assert calls == [requirement["cache_key"]]


def test_concurrent_same_requirement_performs_one_build(tmp_path):
    runtime = FakeRuntime(tmp_path)
    requirement = _requirement()
    calls: list[str] = []
    build = _builder(calls, delay=0.1)

    def prepare(index):
        return prepare_library_targets(
            runtime,
            [requirement],
            execution_id=f"library-concurrent-{index}",
            timeout=30,
            cancel_event=threading.Event(),
            builder=build,
        )[0]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(prepare, (1, 2)))

    assert calls == [requirement["cache_key"]]
    assert {result["cache_status"] for result in results} == {
        "built",
        "verified-hit",
    }
    assert results[0]["binary_sha256"] == results[1]["binary_sha256"]


@pytest.mark.parametrize("scenario_id,target_id", NEW_AMARU_LIBRARY_TARGETS)
def test_new_target_generated_manifest_freezes_identity_and_verifies_binary_before_execution(
    tmp_path, scenario_id, target_id
):
    plan = resolve_run_plan(
        RunPlanRequest(
            scenario_id=scenario_id,
            version_policy="exact",
            amaru_version="10.11.20260925",
            acknowledge_unknown_versions=True,
        ),
        capability_context={"host_os": "linux", "host_arch": "amd64"},
    )
    requirement = plan["scenario"]["library_target_requirements"][0]
    prepared = prepare_library_targets(
        FakeRuntime(tmp_path),
        [requirement],
        execution_id=f"library-freeze-{target_id}",
        timeout=30,
        cancel_event=threading.Event(),
        builder=_builder([]),
    )
    stage = tmp_path / "private-launch-stage"
    destination = tmp_path / "private-launch"
    stage.mkdir()

    derived = _freeze_prepared_library_targets(
        plan,
        prepared,
        stage=stage,
        destination=destination,
    )
    os.replace(stage, destination)
    binding = derived["scenario"]["library_target_bindings"][0]
    manifest = load_target_manifest(
        target_id,
        manifests_dir=binding["manifests_dir"],
    )

    assert manifest.id == target_id
    assert manifest.upstream_commit == requirement["source_revision"]
    assert Path(manifest.binary).is_file()
    assert binding["identity"]["binary_sha256"] == prepared[0]["binary_sha256"]

    binary = Path(manifest.binary)
    binary.chmod(0o700)
    binary.write_bytes(b"tampered after the exact target was frozen")
    with pytest.raises(ValueError, match="binary digest"):
        load_target_manifest(target_id, manifests_dir=binding["manifests_dir"])
