from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import stat
import threading
import time
from types import SimpleNamespace

import pytest

import controller.library_target_builds as library_builds
from controller.library_target_builds import (
    LibraryTargetBuildError,
    prepare_library_targets,
)
from controller.docker_runtime import DockerRuntimeError
from profile_manager.run_plan import RunPlanRequest, resolve_run_plan


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


def _requirement(version: str = "10.11.20260925") -> dict:
    plan = resolve_run_plan(
        RunPlanRequest(
            scenario_id="amaru-cbor-tx-body-fuzz",
            version_policy="exact",
            amaru_version=version,
            acknowledge_unknown_versions=True,
        ),
        capability_context={"host_os": "linux", "host_arch": "amd64"},
    )
    return plan["scenario"]["library_target_requirements"][0]


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

    first = prepare_library_targets(
        runtime,
        [requirement],
        execution_id="library-build-first",
        timeout=30,
        cancel_event=threading.Event(),
        builder=_builder(calls),
    )[0]
    second = prepare_library_targets(
        runtime,
        [requirement],
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


def test_published_cache_contains_evidence_only(tmp_path):
    runtime = FakeRuntime(tmp_path)
    requirement = _requirement()
    build = _builder([])

    def build_with_disposable_files(*args, **kwargs):
        workspace = args[1]
        for name in ("source", "target", "home", ".cargo", ".rustup"):
            path = workspace / name
            path.mkdir()
            (path / "discard-me").write_text("not retained\n")
        return build(*args, **kwargs)

    prepared = prepare_library_targets(
        runtime,
        [requirement],
        execution_id="library-evidence-only",
        timeout=30,
        cancel_event=threading.Event(),
        builder=build_with_disposable_files,
    )[0]

    assert sorted(path.name for path in Path(prepared["binary"]).parent.iterdir()) == [
        "build.log",
        "effective-Cargo.lock",
        "executable",
        "receipt.json",
        "upstream-Cargo.lock",
    ]


def test_default_builder_runs_in_pinned_execution_host_container(tmp_path):
    runtime = FakeDockerRuntime(tmp_path)
    requirement = _requirement()

    result = prepare_library_targets(
        runtime,
        [requirement],
        execution_id="library-default-builder",
        timeout=30,
        cancel_event=threading.Event(),
    )[0]

    command = next(item for item in runtime.commands if item[0] == "run")
    command_index = runtime.commands.index(command)
    command_kwargs = runtime.command_kwargs[command_index]
    assert command[command.index("--platform") + 1] == "linux/amd64"
    assert requirement["builder_image"] in command
    assert command[-2:] == ("python", "/work/builder.py")
    assert runtime.workspace_modes == [0o755]
    assert command_kwargs["execution_id"] == "library-default-builder"
    assert Path(command_kwargs["output_path"]).name == "build.log"
    assert result["cache_status"] == "built"
    assert result["source_revision"] == requirement["source_revision"]
    assert result["source_tree"] == "2" * 40
    assert Path(result["upstream_lock"]).is_file()


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


def test_same_artifact_reuses_cache_across_scenario_locations(tmp_path):
    runtime = FakeRuntime(tmp_path)
    first_requirement = _requirement()
    second_requirement = deepcopy(first_requirement)
    second_requirement["location"] = ["phases", 2, "load", 7]
    calls: list[str] = []

    first = prepare_library_targets(
        runtime,
        [first_requirement],
        execution_id="library-location-first",
        timeout=30,
        cancel_event=threading.Event(),
        builder=_builder(calls),
    )[0]
    second = prepare_library_targets(
        runtime,
        [second_requirement],
        execution_id="library-location-second",
        timeout=30,
        cancel_event=threading.Event(),
        builder=_builder(calls),
    )[0]

    assert calls == [first_requirement["cache_key"]]
    assert first["cache_status"] == "built"
    assert second["cache_status"] == "verified-hit"
    assert first["location"] == ["load", 0]
    assert second["location"] == ["phases", 2, "load", 7]


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
    assert Path(first["binary"]).parent != Path(second["binary"]).parent


def test_failed_build_retains_log_and_never_publishes_cache_entry(tmp_path):
    runtime = FakeRuntime(tmp_path)
    requirement = _requirement()

    def fail(_requirement, _workspace, log_path, **_kwargs):
        log_path.write_text("cargo: incompatible amaru source\n", encoding="utf-8")
        raise RuntimeError("selected Amaru source is incompatible with the packaged shim")

    with pytest.raises(LibraryTargetBuildError, match="incompatible"):
        prepare_library_targets(
            runtime,
            [requirement],
            execution_id="library-build-failed",
            timeout=30,
            cancel_event=threading.Event(),
            builder=fail,
        )

    failures = list(
        (runtime.state_dir / "library-targets" / "failures").rglob("build.log")
    )
    assert len(failures) == 1
    assert "incompatible amaru source" in failures[0].read_text()
    cache = runtime.state_dir / "library-targets" / "cache"
    assert not (cache / requirement["cache_key"].removeprefix("sha256:")).exists()


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


def test_frozen_target_manifest_rejects_binary_tampering_before_execution(tmp_path):
    from profile_manager.primitives import load_target_manifest

    manifests = tmp_path / "manifests"
    manifests.mkdir()
    binary = tmp_path / "executable"
    binary.write_bytes(b"verified executable")
    binary.chmod(0o500)
    digest = "sha256:" + hashlib.sha256(binary.read_bytes()).hexdigest()
    (manifests / "amaru-cbor-decode-tx-body.yaml").write_text(
        json.dumps(
            {
                "id": "amaru-cbor-decode-tx-body",
                "binary": str(binary),
                "input_format": "stdin_bytes",
                "implementation": "amaru",
                "language": "rust",
                "upstream_commit": "b" * 40,
                "invariants": [],
                "dwarf_build_evidence": {"binary_sha256": digest},
            }
        ),
        encoding="utf-8",
    )

    assert load_target_manifest(
        "amaru-cbor-decode-tx-body", manifests_dir=manifests
    ).binary == str(binary)
    binary.chmod(0o700)
    binary.write_bytes(b"changed after freeze")

    with pytest.raises(ValueError, match="binary digest"):
        load_target_manifest(
            "amaru-cbor-decode-tx-body", manifests_dir=manifests
        )
