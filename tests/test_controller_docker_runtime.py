from __future__ import annotations

import hashlib
import json
import shutil
import stat
import sys
import threading
import time
import uuid
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from controller.docker_runtime import DockerResult, DockerRuntime, DockerRuntimeError
from controller.service import ControllerService
from controller.contracts import parse_request
from profile_manager.data.operate_topology_health import _verified_retained_desired
from profile_manager.deployment_versions import build_deployment_version_preview
from profile_manager.measurement_execution import prepare_scenario_measurements
from profile_manager.operator_version_decisions import OperatorVersionDecisionLedger
from profile_manager.profiles import desired_topology_for_profile, find_profile
from profile_manager.topology_actions import TopologyActionService
from profile_manager.scenario import load_scenario
from profile_manager.launch_store import create_launch, record_launch_preflight
from profile_manager.run_plan import digest_run_plan, resolve_run_plan
from scripts.runtime_amaru_control_substrate import (
    BASE_PACKAGE as AMARU_CONTROL_BASE_PACKAGE,
    RuntimeControlError,
    _stage_runtime_assets,
    build_runtime_metadata,
    prepare_runtime_model,
    validate_runtime_request,
)
from scripts.qualify_node_versions import CONTROL_AMARU_IMAGE


DIGEST = "sha256:" + "a" * 64
IMAGE = "ghcr.io/pragma-org/amaru@" + DIGEST
PACKAGE_BASE = (
    "ghcr.io/pragma-org/amaru@sha256:"
    "45d46a6ba7147bfa95d96c103820542a9e3ac3602c4c316cc0d04bbd6d71489e"
)
CARDANO_PACKAGE_BASE = (
    "ghcr.io/intersectmbo/cardano-node@sha256:"
    "6365403f44713d0a046865fb0466503ef207b71beae1b1ffece7f4399356db9f"
)


def _package_elf(*, machine=62):
    header = bytearray(64)
    header[:4] = b"\x7fELF"
    header[4] = 2
    header[5] = 1
    header[6] = 1
    header[18:20] = machine.to_bytes(2, "little")
    return bytes(header) + b"fixture"


def _stage_package(
    root,
    *,
    body=None,
    import_id="import-0123456789abcdef01234567",
    execution_id="execution-0123456789abcdef0123456789abcdef",
):
    body = _package_elf() if body is None else body
    directory = root / import_id
    directory.mkdir(parents=True)
    digest = hashlib.sha256(body).hexdigest()
    (directory / "executable").write_bytes(body)
    (directory / "request.json").write_text(
        json.dumps(
            {
                "schema_version": "v1",
                "import_id": import_id,
                "implementation": "amaru",
                "version": "10.11.local-test",
                "declared_source_revision": None,
                "platform": "linux/amd64",
                "upload_sha256": digest,
                "executable_sha256": digest,
                "executable_size": len(body),
                "created_at": "2026-10-13T12:00:00Z",
                "expires_at": "2099-10-13T13:00:00Z",
                "state": "staged",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "import_id": import_id,
        "execution_id": execution_id,
        "implementation": "amaru",
        "version": "10.11.local-test",
        "platform": "linux/amd64",
        "upload_sha256": "sha256:" + digest,
        "executable_sha256": "sha256:" + digest,
        "executable_size": len(body),
    }


def test_package_artifact_copies_verified_bytes_and_uses_fixed_isolated_recipe(
    tmp_path, monkeypatch
):
    imports = tmp_path / "imports"
    parameters = _stage_package(imports)
    state = tmp_path / "controller-state"
    host_state = tmp_path / "host-controller-state"
    calls = []
    captured = {}

    def runner(argv, timeout, execution_id):
        calls.append((argv, timeout, execution_id))
        if argv[1:3] == ("image", "inspect") and argv[-1] == PACKAGE_BASE:
            return DockerResult(
                0,
                json.dumps(
                    [
                        {
                            "Id": "sha256:" + "a" * 64,
                            "RepoDigests": [PACKAGE_BASE],
                            "Os": "linux",
                            "Architecture": "amd64",
                            "Config": {},
                        }
                    ]
                ),
                "",
            )
        if argv[1] == "build":
            context = Path(argv[-1])
            captured["dockerfile"] = (context / "Dockerfile").read_text()
            captured["executable"] = (context / "executable").read_bytes()
            captured["executable_mode"] = stat.S_IMODE(
                (context / "executable").stat().st_mode
            )
            return DockerResult(0, "", "")
        if argv[1:3] == ("image", "inspect"):
            return DockerResult(
                0,
                json.dumps(
                    [
                        {
                            "Id": "sha256:" + "b" * 64,
                            "RepoDigests": [],
                            "Os": "linux",
                            "Architecture": "amd64",
                            "Config": {
                                "Entrypoint": ["/usr/local/bin/amaru"],
                                "Cmd": ["run"],
                            },
                        }
                    ]
                ),
                "",
            )
        if argv[1] == "run":
            return DockerResult(0, "amaru local build\n", "")
        if argv[1:4] == ("container", "rm", "--force"):
            return DockerResult(0, "", "")
        if argv[1:3] == ("image", "rm"):
            return DockerResult(0, "", "")
        raise AssertionError(argv)

    monkeypatch.setenv("DWARF_HOST_ARCH", "amd64")
    monkeypatch.setenv("DWARF_ARTIFACT_PACKAGE_AMARU_BASE", PACKAGE_BASE)
    monkeypatch.setenv("DWARF_GOLDEN_IMAGES", PACKAGE_BASE)
    runtime = DockerRuntime(
        installation_id="install",
        state_dir=state,
        host_state_dir=tmp_path / "daemon-host-state",
        version_imports_dir=imports,
        runner=runner,
    )

    result = runtime.package_artifact(
        parameters,
        parameters["execution_id"],
        timeout=120,
        cancel_event=threading.Event(),
    )

    assert captured["executable"] == (imports / parameters["import_id"] / "executable").read_bytes()
    assert f"FROM {PACKAGE_BASE}" in captured["dockerfile"]
    assert "COPY executable /usr/local/bin/amaru" in captured["dockerfile"]
    assert "--chmod" not in captured["dockerfile"]
    assert captured["executable_mode"] == 0o555
    assert 'ENTRYPOINT ["/usr/local/bin/amaru"]' in captured["dockerfile"]
    assert 'CMD ["run"]' in captured["dockerfile"]
    assert "measurement" not in captured["dockerfile"].lower()
    build = next(argv for argv, _, _ in calls if argv[1] == "build")
    assert "--pull=false" in build and "--network=none" in build
    assert Path(build[-1]).is_relative_to(state)
    assert not Path(build[-1]).is_relative_to(tmp_path / "daemon-host-state")
    probe = next(argv for argv, _, _ in calls if argv[1] == "run")
    assert "--network=none" in probe
    assert "--read-only" in probe
    assert ("--cap-drop", "ALL") == probe[probe.index("--cap-drop") : probe.index("--cap-drop") + 2]
    assert ("--pids-limit", "64") == probe[probe.index("--pids-limit") : probe.index("--pids-limit") + 2]
    assert "--privileged" not in probe and "/var/run/docker.sock" not in probe
    assert not any(item in {"--volume", "-v"} for item in probe)
    probes = [argv for argv, _, _ in calls if argv[1] == "run"]
    assert len(probes) == 2
    assert probes[0][-1] == "--version"
    assert probes[1][-2:] == ("run", "--help")
    assert result["status"] == "packaged"
    assert result["config_image_id"] == "sha256:" + "b" * 64
    assert result["runtime_interface"] == "extracted-binary"
    assert result["qualification"] == "unverified"
    assert result["compatibility"] == "unknown-until-run"
    assert "target_mode" not in result and "tap_capabilities" not in result
    assert not (state / "controller/artifact-packages" / parameters["execution_id"]).exists()


def test_package_artifact_rejects_changed_stage_before_docker(tmp_path, monkeypatch):
    imports = tmp_path / "imports"
    parameters = _stage_package(imports)
    (imports / parameters["import_id"] / "executable").write_bytes(b"changed")
    calls = []
    monkeypatch.setenv("DWARF_HOST_ARCH", "amd64")
    monkeypatch.setenv("DWARF_ARTIFACT_PACKAGE_AMARU_BASE", PACKAGE_BASE)
    monkeypatch.setenv("DWARF_GOLDEN_IMAGES", PACKAGE_BASE)
    runtime = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path / "state",
        host_state_dir=tmp_path / "host-state",
        version_imports_dir=imports,
        runner=lambda *args: calls.append(args),
    )

    with pytest.raises(DockerRuntimeError, match="staged artifact identity changed"):
        runtime.package_artifact(
            parameters,
            parameters["execution_id"],
            timeout=120,
            cancel_event=threading.Event(),
        )
    assert calls == []


def test_package_artifact_revalidates_elf_machine_after_copy(tmp_path, monkeypatch):
    imports = tmp_path / "imports"
    parameters = _stage_package(imports, body=_package_elf(machine=183))
    # Simulate an untrusted descriptor claiming the controller platform.
    descriptor_path = imports / parameters["import_id"] / "request.json"
    descriptor = json.loads(descriptor_path.read_text())
    descriptor["platform"] = "linux/amd64"
    descriptor_path.write_text(json.dumps(descriptor) + "\n")
    parameters["platform"] = "linux/amd64"
    calls = []
    monkeypatch.setenv("DWARF_HOST_ARCH", "amd64")
    monkeypatch.setenv("DWARF_ARTIFACT_PACKAGE_AMARU_BASE", PACKAGE_BASE)
    monkeypatch.setenv("DWARF_GOLDEN_IMAGES", PACKAGE_BASE)
    runtime = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path / "state",
        host_state_dir=tmp_path / "state",
        version_imports_dir=imports,
        runner=lambda *args: calls.append(args),
    )

    with pytest.raises(DockerRuntimeError, match="ELF platform differs"):
        runtime.package_artifact(
            parameters,
            parameters["execution_id"],
            timeout=120,
            cancel_event=threading.Event(),
        )
    assert calls == []


def test_docker_runner_stops_and_rejects_output_above_fixed_ceiling(tmp_path):
    runtime = DockerRuntime(
        installation_id="install",
        docker_binary=sys.executable,
        state_dir=tmp_path / "state",
    )

    with pytest.raises(DockerRuntimeError, match="output exceeded limit"):
        runtime._run(
            ("-c", "import sys; sys.stdout.write('x' * 131072)"),
            timeout=10,
            output_limit=4096,
        )


def test_package_artifact_probe_failure_cleans_container_and_workspace(
    tmp_path, monkeypatch
):
    imports = tmp_path / "imports"
    parameters = _stage_package(imports)
    calls = []

    def runner(argv, timeout, execution_id):
        calls.append(argv)
        if argv[1:3] == ("image", "inspect") and argv[-1] == PACKAGE_BASE:
            return DockerResult(
                0,
                json.dumps([{
                    "Id": "sha256:" + "a" * 64,
                    "RepoDigests": [PACKAGE_BASE],
                    "Os": "linux",
                    "Architecture": "amd64",
                    "Config": {},
                }]),
                "",
            )
        if argv[1] == "build":
            return DockerResult(0, "", "")
        if argv[1:3] == ("image", "inspect"):
            return DockerResult(
                0,
                json.dumps([{
                    "Id": "sha256:" + "b" * 64,
                    "RepoDigests": [],
                    "Os": "linux",
                    "Architecture": "amd64",
                    "Config": {
                        "Entrypoint": ["/usr/local/bin/amaru"],
                        "Cmd": ["run"],
                    },
                }]),
                "",
            )
        if argv[1] == "run":
            return DockerResult(2, "", "untrusted failure details")
        if argv[1:4] == ("container", "rm", "--force"):
            return DockerResult(0, "", "")
        if argv[1:3] == ("image", "rm"):
            return DockerResult(0, "", "")
        raise AssertionError(argv)

    monkeypatch.setenv("DWARF_HOST_ARCH", "amd64")
    monkeypatch.setenv("DWARF_ARTIFACT_PACKAGE_AMARU_BASE", PACKAGE_BASE)
    monkeypatch.setenv("DWARF_GOLDEN_IMAGES", PACKAGE_BASE)
    state = tmp_path / "state"
    runtime = DockerRuntime(
        installation_id="install",
        state_dir=state,
        version_imports_dir=imports,
        runner=runner,
    )

    with pytest.raises(DockerRuntimeError, match="executable smoke failed"):
        runtime.package_artifact(
            parameters,
            parameters["execution_id"],
            timeout=120,
            cancel_event=threading.Event(),
        )

    assert any(argv[1:4] == ("container", "rm", "--force") for argv in calls)
    assert any(argv[1:3] == ("image", "rm") for argv in calls)
    assert not (state / "controller/artifact-packages" / parameters["execution_id"]).exists()


def test_failed_reimport_removes_only_its_execution_tag_not_prior_registration(
    tmp_path, monkeypatch
):
    imports = tmp_path / "imports"
    first = _stage_package(imports)
    second = _stage_package(
        imports,
        import_id="import-1123456789abcdef01234567",
        execution_id="execution-1123456789abcdef0123456789abcdef",
    )
    build_tags = []
    removed = []
    run_count = 0

    def runner(argv, timeout, execution_id):
        nonlocal run_count
        if argv[1:3] == ("image", "inspect") and argv[-1] == PACKAGE_BASE:
            return DockerResult(
                0,
                json.dumps([{
                    "Id": "sha256:" + "a" * 64,
                    "RepoDigests": [PACKAGE_BASE],
                    "Os": "linux",
                    "Architecture": "amd64",
                    "Config": {},
                }]),
                "",
            )
        if argv[1] == "build":
            build_tags.append(argv[argv.index("--tag") + 1])
            return DockerResult(0, "", "")
        if argv[1:3] == ("image", "inspect"):
            return DockerResult(
                0,
                json.dumps([{
                    "Id": "sha256:" + "b" * 64,
                    "RepoDigests": [],
                    "Os": "linux",
                    "Architecture": "amd64",
                    "Config": {
                        "Entrypoint": ["/usr/local/bin/amaru"],
                        "Cmd": ["run"],
                    },
                }]),
                "",
            )
        if argv[1] == "run":
            run_count += 1
            return DockerResult(0 if run_count <= 2 else 2, "version/help", "")
        if argv[1:4] == ("container", "rm", "--force"):
            return DockerResult(0, "", "")
        if argv[1:3] == ("image", "rm"):
            removed.append(argv[3])
            return DockerResult(0, "", "")
        raise AssertionError(argv)

    monkeypatch.setenv("DWARF_HOST_ARCH", "amd64")
    monkeypatch.setenv("DWARF_ARTIFACT_PACKAGE_AMARU_BASE", PACKAGE_BASE)
    monkeypatch.setenv("DWARF_GOLDEN_IMAGES", PACKAGE_BASE)
    runtime = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path / "state",
        version_imports_dir=imports,
        runner=runner,
    )

    accepted = runtime.package_artifact(
        first,
        first["execution_id"],
        timeout=120,
        cancel_event=threading.Event(),
    )
    with pytest.raises(DockerRuntimeError, match="executable smoke failed"):
        runtime.package_artifact(
            second,
            second["execution_id"],
            timeout=120,
            cancel_event=threading.Event(),
        )

    assert accepted["config_image_id"] == "sha256:" + "b" * 64
    assert len(set(build_tags)) == 2
    assert removed == [build_tags[1]]
    assert build_tags[0] not in removed
    assert accepted["config_image_id"] not in removed
    assert PACKAGE_BASE not in removed


def test_artifact_cleanup_refuses_to_hide_retained_probe_container(tmp_path):
    calls = []

    def runner(argv, timeout, execution_id):
        calls.append(argv)
        if argv[1:4] == ("container", "rm", "--force"):
            return DockerResult(1, "", "remove failed")
        if argv[1:3] == ("container", "inspect"):
            return DockerResult(0, "[]", "")
        if argv[1:3] == ("image", "rm"):
            return DockerResult(0, "", "")
        raise AssertionError(argv)

    runtime = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path / "state",
        runner=runner,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(DockerRuntimeError) as failure:
        runtime._cleanup_artifact_package_resources(
            tag="dwarf/local-amaru-import:test",
            probe_name="dwarf-package-probe-test",
            work_root=workspace,
            remove_image=True,
        )

    assert failure.value.failure_code == "artifact_cleanup_failed"
    assert failure.value.failure_stage == "artifact_cleanup"
    assert any(argv[1:3] == ("container", "inspect") for argv in calls)


def test_package_cancel_during_build_returns_cancelled_after_exact_cleanup(
    tmp_path, monkeypatch
):
    imports = tmp_path / "imports"
    parameters = _stage_package(imports)
    cancellation = threading.Event()
    removed = []

    def runner(argv, timeout, execution_id):
        if argv[1:3] == ("image", "inspect") and argv[-1] == PACKAGE_BASE:
            return DockerResult(
                0,
                json.dumps([{
                    "Id": "sha256:" + "a" * 64,
                    "RepoDigests": [PACKAGE_BASE],
                    "Os": "linux",
                    "Architecture": "amd64",
                    "Config": {},
                }]),
                "",
            )
        if argv[1] == "build":
            cancellation.set()
            return DockerResult(143, "", "cancelled")
        if argv[1:4] == ("container", "rm", "--force"):
            return DockerResult(0, "", "")
        if argv[1:3] == ("image", "rm"):
            removed.append(argv[3])
            return DockerResult(0, "", "")
        raise AssertionError(argv)

    monkeypatch.setenv("DWARF_HOST_ARCH", "amd64")
    monkeypatch.setenv("DWARF_ARTIFACT_PACKAGE_AMARU_BASE", PACKAGE_BASE)
    monkeypatch.setenv("DWARF_GOLDEN_IMAGES", PACKAGE_BASE)
    result = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path / "state",
        version_imports_dir=imports,
        runner=runner,
    ).package_artifact(
        parameters,
        parameters["execution_id"],
        timeout=120,
        cancel_event=cancellation,
    )

    assert result == {"status": "cancelled"}
    assert len(removed) == 1
    assert parameters["execution_id"].removeprefix("execution-") in removed[0]


def test_package_cardano_artifact_uses_fixed_direct_node_recipe(tmp_path, monkeypatch):
    imports = tmp_path / "imports"
    parameters = _stage_package(imports)
    parameters.update(implementation="cardano-node", version="11.1.local-test")
    descriptor_path = imports / parameters["import_id"] / "request.json"
    descriptor = json.loads(descriptor_path.read_text())
    descriptor.update(implementation="cardano-node", version="11.1.local-test")
    descriptor_path.write_text(json.dumps(descriptor) + "\n")
    captured = {}

    def runner(argv, timeout, execution_id):
        if argv[1:3] == ("image", "inspect") and argv[-1] == CARDANO_PACKAGE_BASE:
            return DockerResult(
                0,
                json.dumps([{
                    "Id": "sha256:" + "a" * 64,
                    "RepoDigests": [CARDANO_PACKAGE_BASE],
                    "Os": "linux",
                    "Architecture": "amd64",
                    "Config": {},
                }]),
                "",
            )
        if argv[1] == "build":
            captured["dockerfile"] = (Path(argv[-1]) / "Dockerfile").read_text()
            return DockerResult(0, "", "")
        if argv[1:3] == ("image", "inspect"):
            return DockerResult(
                0,
                json.dumps([{
                    "Id": "sha256:" + "c" * 64,
                    "RepoDigests": [],
                    "Os": "linux",
                    "Architecture": "amd64",
                    "Config": {
                        "Entrypoint": ["/usr/local/bin/cardano-node"],
                        "Cmd": ["run"],
                    },
                }]),
                "",
            )
        if argv[1] == "run" or argv[1:4] == ("container", "rm", "--force"):
            return DockerResult(0, "cardano-node local build\n", "")
        raise AssertionError(argv)

    monkeypatch.setenv("DWARF_HOST_ARCH", "amd64")
    monkeypatch.setenv("DWARF_ARTIFACT_PACKAGE_CARDANO_BASE", CARDANO_PACKAGE_BASE)
    monkeypatch.setenv("DWARF_GOLDEN_IMAGES", CARDANO_PACKAGE_BASE)
    result = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path / "state",
        host_state_dir=tmp_path / "state",
        version_imports_dir=imports,
        runner=runner,
    ).package_artifact(
        parameters,
        parameters["execution_id"],
        timeout=120,
        cancel_event=threading.Event(),
    )

    assert f"FROM {CARDANO_PACKAGE_BASE}" in captured["dockerfile"]
    assert 'ENTRYPOINT ["/usr/local/bin/cardano-node"]' in captured["dockerfile"]
    assert result["implementation"] == "cardano-node"
    assert result["runtime_interface"] is None
    assert result["compatibility"] == "unknown-until-run"


def _qualified_target_fixture(state, *, implementation="amaru"):
    digest = "sha256:" + "f" * 64
    manifest_digest = "sha256:" + "1" * 64
    image = f"dwarf/{implementation}-measurement@{manifest_digest}"
    revision = "b" * 40
    patch_set = "d" * 64
    record = {
        "schema_version": 1,
        "implementation": implementation,
        "mode": "patched",
        "version": "10.11.0" if implementation == "amaru" else "11.1.2",
        "source_revision": revision,
        "patch_set_sha256": patch_set,
        "image_reference": image,
        "image_digest": digest,
        "executable_digest": "sha256:" + "e" * 64,
        "build_result_sha256": "sha256:" + "a" * 64,
        "runtime_probe_log_sha256": "sha256:" + "9" * 64,
    }
    if implementation == "amaru":
        record["runtime_probe_image"] = "wrapper@sha256:" + "c" * 64
    path = (
        state
        / "measurement-targets"
        / implementation
        / revision
        / f"{patch_set}.json"
    )
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(record), encoding="utf-8")
    node = {
        "impl": implementation,
        "version": record["version"],
        "source_revision": revision,
        "target_mode": "patched",
        "patch_set_sha256": patch_set,
        "image": image,
        "image_digest": digest,
        "executable_digest": record["executable_digest"],
        "build_result_sha256": record["build_result_sha256"],
        "runtime_probe_log_sha256": record["runtime_probe_log_sha256"],
    }
    if record.get("runtime_probe_image"):
        node["runtime_probe_image"] = record["runtime_probe_image"]
    return record, node


def test_exact_profile_resolved_local_target_is_approved_without_expanding_goldens(
    tmp_path, monkeypatch
):
    state = tmp_path / "controller-state"
    record, node = _qualified_target_fixture(state)
    monkeypatch.setenv("DWARF_HOST_OS", "linux")
    monkeypatch.setenv("DWARF_HOST_ARCH", "amd64")
    calls = []

    def runner(argv, timeout, execution_id):
        calls.append(argv)
        return DockerResult(
            0,
            json.dumps(
                [{
                    "Id": record["image_digest"],
                    "RepoDigests": [record["image_reference"]],
                    "Os": "linux",
                    "Architecture": "amd64",
                }]
            ),
            "",
        )

    runtime = DockerRuntime(
        installation_id="install", state_dir=state, runner=runner
    )
    runtime._ensure_image(
        record["image_reference"],
        "execution-qualified-local",
        qualified_target=node,
    )
    assert calls == [
        ("docker", "image", "inspect", record["image_reference"])
    ]
    with pytest.raises(DockerRuntimeError, match="approved golden image"):
        runtime._ensure_image(record["runtime_probe_image"], "execution-probe")


def test_imported_qualified_target_uses_destination_config_id_without_repo_digest(
    tmp_path, monkeypatch
):
    state = tmp_path / "controller-state"
    record, node = _qualified_target_fixture(state)
    source_reference = record["image_reference"]
    manifest_digest = source_reference.rsplit("@", 1)[1]
    config_digest = record["image_digest"]
    record.update(
        {
            "qualification_image_id": "sha256:" + "2" * 64,
            "image_manifest_digest": manifest_digest,
            "image_config_digest": config_digest,
            "runtime_image_reference": config_digest,
            "transfer_source_installation_id": "install-source",
            "transfer_manifest_sha256": "sha256:" + "3" * 64,
        }
    )
    record_path = (
        state
        / "measurement-targets"
        / "amaru"
        / record["source_revision"]
        / f"{record['patch_set_sha256']}.json"
    )
    record_path.write_text(json.dumps(record), encoding="utf-8")
    node.update(
        {
            "image": config_digest,
            "source_image_reference": source_reference,
            "image_manifest_digest": manifest_digest,
            "image_config_digest": config_digest,
            "qualification_image_id": record["qualification_image_id"],
            "transfer_source_installation_id": record[
                "transfer_source_installation_id"
            ],
            "transfer_manifest_sha256": record["transfer_manifest_sha256"],
        }
    )
    monkeypatch.setenv("DWARF_HOST_OS", "linux")
    monkeypatch.setenv("DWARF_HOST_ARCH", "amd64")
    inspected_id = config_digest

    def runner(argv, timeout, execution_id):
        return DockerResult(
            0,
            json.dumps(
                [
                    {
                        "Id": inspected_id,
                        "RepoDigests": [],
                        "Os": "linux",
                        "Architecture": "amd64",
                    }
                ]
            ),
            "",
        )

    runtime = DockerRuntime(
        installation_id="install", state_dir=state, runner=runner
    )
    runtime._ensure_image(
        config_digest,
        "execution-imported-qualified-local",
        qualified_target=node,
    )

    node["transfer_source_installation_id"] = "install-other"
    with pytest.raises(DockerRuntimeError, match="imported provenance"):
        runtime._ensure_image(
            config_digest,
            "execution-imported-wrong-source-installation",
            qualified_target=node,
        )
    node["transfer_source_installation_id"] = record[
        "transfer_source_installation_id"
    ]

    inspected_id = manifest_digest
    with pytest.raises(DockerRuntimeError, match="qualified local target"):
        runtime._ensure_image(
            config_digest,
            "execution-imported-wrong-daemon-id",
            qualified_target=node,
        )


def test_bare_config_id_cannot_enter_unqualified_golden_image_path(
    tmp_path, monkeypatch
):
    config_digest = "sha256:" + "4" * 64
    monkeypatch.setenv("DWARF_GOLDEN_IMAGES", config_digest)
    runtime = DockerRuntime(installation_id="install", state_dir=tmp_path)

    with pytest.raises(DockerRuntimeError, match="qualified imported provenance"):
        runtime._ensure_image(config_digest, "execution-unqualified-config-id")


def test_read_only_image_inspect_uses_fixed_argv_and_returns_curated_identity(tmp_path):
    image = "local/amaru@sha256:" + "a" * 64
    config_id = "sha256:" + "b" * 64
    calls = []

    def runner(argv, timeout, execution_id):
        calls.append((argv, timeout, execution_id))
        return DockerResult(0, json.dumps([{
            "Id": config_id,
            "RepoDigests": [image],
            "Os": "linux",
            "Architecture": "amd64",
            "Config": {
                "Entrypoint": ["/usr/local/bin/amaru"],
                "Cmd": ["run"],
                "Env": ["SECRET=do-not-return"],
                "Labels": {"token": "no"},
            },
        }]), "")

    runtime = DockerRuntime(installation_id="install", state_dir=tmp_path, runner=runner)

    assert runtime.inspect_image(image, implementation="amaru") == {
        "config_image_id": config_id,
        "manifest_references": [image],
        "platform": "linux/amd64",
        "runtime_interface": "extracted-binary",
    }
    assert calls == [(('docker', 'image', 'inspect', image), 20, None)]


@pytest.mark.parametrize(
    ("image", "inspected", "message"),
    [
        (
            "local/amaru@sha256:" + "a" * 64,
            {"Id": "sha256:" + "b" * 64, "RepoDigests": [], "Os": "linux", "Architecture": "amd64"},
            "manifest",
        ),
        (
            "sha256:" + "b" * 64,
            {"Id": "sha256:" + "c" * 64, "RepoDigests": [], "Os": "linux", "Architecture": "amd64"},
            "config",
        ),
        (
            "sha256:" + "b" * 64,
            {"Id": "sha256:" + "b" * 64, "RepoDigests": [], "Os": "windows", "Architecture": "amd64"},
            "platform",
        ),
        (
            "sha256:" + "b" * 64,
            {"Id": "sha256:" + "b" * 64, "RepoDigests": ["not-a-digest"], "Os": "linux", "Architecture": "amd64"},
            "manifest",
        ),
    ],
)
def test_read_only_image_inspect_fails_closed(image, inspected, message, tmp_path):
    runtime = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path,
        runner=lambda *_args: DockerResult(0, json.dumps([inspected]), ""),
    )

    with pytest.raises(DockerRuntimeError, match=message):
        runtime.inspect_image(image, implementation="amaru")


@pytest.mark.parametrize(
    "config",
    [
        None,
        {"Entrypoint": "/usr/local/bin/amaru", "Cmd": ["run"]},
        {"Entrypoint": ["/opt/custom/start"], "Cmd": []},
        {"Entrypoint": ["/usr/local/bin/amaru"], "Cmd": ["serve"]},
    ],
)
def test_read_only_image_inspect_keeps_unknown_amaru_interface_unverified(
    tmp_path, config
):
    image = "local/amaru@sha256:" + "a" * 64
    config_id = "sha256:" + "b" * 64
    inspected = {
        "Id": config_id,
        "RepoDigests": [image],
        "Os": "linux",
        "Architecture": "amd64",
        "Config": config,
    }
    runtime = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path,
        runner=lambda *_args: DockerResult(0, json.dumps([inspected]), ""),
    )

    assert runtime.inspect_image(image, implementation="amaru")[
        "runtime_interface"
    ] is None


def test_profile_selected_patched_target_cannot_bypass_record_with_static_golden(
    tmp_path, monkeypatch
):
    state = tmp_path / "controller-state"
    record, node = _qualified_target_fixture(state)
    monkeypatch.setenv("DWARF_GOLDEN_IMAGES", record["image_reference"])
    (state / "measurement-targets").rename(state / "registry-removed")
    runtime = DockerRuntime(
        installation_id="install",
        state_dir=state,
        runner=lambda *_args: pytest.fail("record rejection must precede Docker inspect"),
    )

    with pytest.raises(DockerRuntimeError, match="qualified local target record"):
        runtime._ensure_image(
            record["image_reference"],
            "execution-record-missing",
            qualified_target=node,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("Id", "sha256:" + "0" * 64),
        ("RepoDigests", ["dwarf/other@sha256:" + "f" * 64]),
        ("Os", "windows"),
        ("Architecture", "arm64"),
    ],
)
def test_qualified_local_target_inspect_identity_and_platform_must_match(
    tmp_path, monkeypatch, field, value
):
    state = tmp_path / "controller-state"
    record, node = _qualified_target_fixture(state, implementation="cardano-node")
    monkeypatch.setenv("DWARF_HOST_OS", "linux")
    monkeypatch.setenv("DWARF_HOST_ARCH", "amd64")
    inspected = {
        "Id": record["image_digest"],
        "RepoDigests": [record["image_reference"]],
        "Os": "linux",
        "Architecture": "amd64",
    }
    inspected[field] = value
    runtime = DockerRuntime(
        installation_id="install",
        state_dir=state,
        runner=lambda *_args: DockerResult(0, json.dumps([inspected]), ""),
    )

    with pytest.raises(DockerRuntimeError, match="qualified local target"):
        runtime._ensure_image(
            record["image_reference"],
            "execution-mismatch",
            qualified_target=node,
        )


@pytest.fixture(autouse=True)
def _approved_golden_image(monkeypatch):
    monkeypatch.setenv("DWARF_GOLDEN_IMAGES", IMAGE)
    monkeypatch.setenv("DWARF_CONTROLLER_HOST_STATE_DIR", "/srv/dwarf")


def _container(name, labels, image=IMAGE, state="running", image_id=None):
    return {
        "Id": name + "-id",
        "Name": "/" + name,
        "Image": image_id or image.partition("@")[2],
        "Config": {"Image": image, "Labels": labels},
        "State": {"Status": state},
    }


def test_run_launch_revalidates_plan_and_uses_only_retained_identifier(
    tmp_path, monkeypatch
):
    launches = tmp_path / "launches"
    runs = tmp_path / "runs"
    state = tmp_path / "state"
    bundles = tmp_path / "bundles"
    for path in (runs, state, bundles):
        path.mkdir()
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(runs))
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(state))
    monkeypatch.setenv("ADA2_DWARF_BUNDLES_DIR", str(bundles))
    plan = resolve_run_plan({"scenario_id": "edge-cases-cbor-tx-body-amaru"})
    digest = digest_run_plan(plan)
    launch = create_launch(plan, root=launches)
    observed = []

    def adapter(argv, timeout, execution_id):
        observed.append((argv, timeout, execution_id))
        run_id = "20261008T120000Z-controller"
        run_dir = runs / run_id
        run_dir.mkdir()
        (run_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "exit_status": "pass",
                    "execution": {"security_verdict": "pass"},
                }
            )
        )
        return DockerResult(0, f"run_id: {run_id}\n", "")

    runtime = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path / "controller-state",
        host_state_dir="/srv/dwarf",
        launch_root=launches,
        adapter_runner=adapter,
    )
    result = runtime.run_launch(
        launch_id=launch["launch_id"],
        plan_digest=digest,
        execution_id="run-execution-0123456789abcdef",
        timeout=2400,
    )

    argv, timeout, execution_id = observed[0]
    assert result["run_id"] == "20261008T120000Z-controller"
    assert result["security_verdict"] == "pass"
    assert timeout == 2400
    assert execution_id == "run-execution-0123456789abcdef"
    assert f"ADA2_DWARF_LAUNCH_ID={launch['launch_id']}" in argv
    assert str(launch["path"] / "scenario.yaml") not in argv
    assert Path(argv[-3]).read_bytes() == (launch["path"] / "scenario.yaml").read_bytes()
    assert argv[-2:] == ("--origin", "dashboard")


def test_run_launch_reuses_controller_verified_local_registration_context(
    tmp_path, monkeypatch
):
    """Controller re-resolution must reproduce the dashboard's inspected plan."""
    from profile_manager.data.operate_run_wizard import (
        operator_registration_capability_context,
    )
    from profile_manager.run_plan import RunPlanRequest

    launches = tmp_path / "launches"
    runs = tmp_path / "runs"
    state = tmp_path / "state"
    bundles = tmp_path / "bundles"
    imports = tmp_path / "version-imports"
    for path in (runs, state, bundles):
        path.mkdir()
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(runs))
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(state))
    monkeypatch.setenv("ADA2_DWARF_BUNDLES_DIR", str(bundles))
    monkeypatch.setenv("ADA2_DWARF_VERSION_IMPORTS_DIR", str(imports))
    monkeypatch.setenv("DWARF_HOST_OS", "linux")
    monkeypatch.setenv("DWARF_HOST_ARCH", "amd64")

    import_id = "import-0123456789abcdef01234567"
    execution_id = "execution-" + "1" * 32
    registration_id = "registration-0123456789abcdef01234567"
    config_image_id = "sha256:" + "b" * 64
    executable_digest = "sha256:" + "9" * 64
    registration = {
        "registration_id": registration_id,
        "implementation": "amaru",
        "version": "10.11.20260925-local",
        "image_reference": config_image_id,
        "manifest_digest": None,
        "config_image_id": config_image_id,
        "executable_digest": executable_digest,
        "platform": "linux/amd64",
        "runtime_interface": "extracted-binary",
        "controller_installation_id": "install-test",
        "inspected_at": "2026-10-14T08:00:00Z",
        "provenance": {
            "source_revision": None,
            "public": None,
            "qualification": "unverified",
        },
    }
    ledger = OperatorVersionDecisionLedger(state)
    ledger.append_registration(registration, expected_revision=ledger.read()["revision"])
    monkeypatch.setenv("ADA2_DWARF_VERSION_CATALOG_STATE_DIR", str(state))

    job = imports / import_id
    job.mkdir(parents=True)
    job.joinpath("request.json").write_text(
        json.dumps(
            {
                "schema_version": "v1",
                "import_id": import_id,
                "execution_id": execution_id,
                "implementation": "amaru",
                "version": registration["version"],
                "platform": "linux/amd64",
                "upload_sha256": "9" * 64,
                "executable_sha256": "9" * 64,
                "executable_size": 64,
                "created_at": "2026-10-14T07:59:00Z",
                "expires_at": "2026-10-14T09:00:00Z",
                "state": "registered",
                "registration_id": registration_id,
                "config_image_id": config_image_id,
                "completed_at": "2026-10-14T08:00:00Z",
                "audit_receipt": "controller/controller-audit/audit-package.json",
                "declared_source_revision": None,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    package_result = {
        "status": "packaged",
        "implementation": "amaru",
        "version": registration["version"],
        "config_image_id": config_image_id,
        "manifest_references": [],
        "platform": "linux/amd64",
        "runtime_interface": "extracted-binary",
        "upload_sha256": executable_digest,
        "executable_sha256": executable_digest,
        "declared_source_revision": None,
        "provenance": "operator-upload",
        "qualification": "unverified",
        "compatibility": "unknown-until-run",
        "controller_installation_id": "install-test",
        "packaged_at": registration["inspected_at"],
    }

    def controller_request(operation, parameters, timeout, idempotency_key=None):
        assert idempotency_key is None
        if operation == "execution.status":
            assert parameters == {"execution_id": execution_id}
            assert timeout == 10
            return {
                "execution_id": execution_id,
                "state": "applied",
                "audit_receipt": "controller/controller-audit/audit-package.json",
                "result": package_result,
                "failure": None,
            }
        assert operation == "image.inspect"
        assert parameters["image"] == config_image_id
        return {
            "schema_version": "v1",
            "implementation": "amaru",
            "version": registration["version"],
            "requested_image": config_image_id,
            "config_image_id": config_image_id,
            "manifest_references": [],
            "platform": "linux/amd64",
            "runtime_interface": "extracted-binary",
            "declared_source_revision": None,
            "installation_id": "install-test",
        }

    request = RunPlanRequest.from_mapping(
        {
            "scenario_id": "amaru-measurement-e2e-stock",
            "profile_id": "profile-r-amaru-measurement-stock-control",
            "operator_registration_id": registration_id,
            "acknowledge_unknown_versions": True,
            "measurement_profile": "none",
        }
    )
    context = operator_registration_capability_context(
        request, request_controller=controller_request
    )
    with monkeypatch.context() as plan_context:
        plan_context.setattr(
            "profile_manager.topology_actions.controller_request",
            controller_request,
        )
        plan = resolve_run_plan(request, capability_context=context)
    digest = digest_run_plan(plan)
    launch = create_launch(plan, root=launches)
    observed = []
    observed_architecture = ["amd64"]

    def runner(argv, timeout, run_execution_id):
        assert argv[1:3] == ("image", "inspect")
        assert argv[3] == config_image_id
        return DockerResult(
            0,
            json.dumps(
                [
                    {
                        "Id": config_image_id,
                        "RepoDigests": [],
                        "Os": "linux",
                        "Architecture": observed_architecture[0],
                        "Config": {
                            "Entrypoint": ["/usr/local/bin/amaru"],
                            "Cmd": ["run"],
                        },
                    }
                ]
            ),
            "",
        )

    def adapter(argv, timeout, run_execution_id):
        observed.append((argv, timeout, run_execution_id))
        run_id = "20261014T080800Z-local-controller"
        run_dir = runs / run_id
        run_dir.mkdir()
        run_dir.joinpath("manifest.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "exit_status": "pass",
                    "execution": {"security_verdict": "pass"},
                }
            ),
            encoding="utf-8",
        )
        return DockerResult(0, f"run_id: {run_id}\n", "")

    runtime = DockerRuntime(
        installation_id="install-test",
        state_dir=tmp_path / "controller-state",
        host_state_dir="/srv/dwarf",
        launch_root=launches,
        runner=runner,
        adapter_runner=adapter,
    )
    monkeypatch.setattr(
        runtime,
        "_attach_run_network",
        lambda *_args, **_kwargs: {"network_name": "test-run-network"},
    )
    monkeypatch.setattr(runtime, "_detach_run_network", lambda *_args, **_kwargs: {})

    result = runtime.run_launch(
        launch_id=launch["launch_id"],
        plan_digest=digest,
        execution_id="run-execution-local-context",
        timeout=2400,
        request_controller=controller_request,
    )

    assert result["run_id"] == "20261014T080800Z-local-controller"
    assert len(observed) == 1

    observed_architecture[0] = "arm64"
    with pytest.raises(DockerRuntimeError, match="launch inputs are invalid or stale"):
        runtime.run_launch(
            launch_id=launch["launch_id"],
            plan_digest=digest,
            execution_id="run-execution-local-platform-mismatch",
            timeout=2400,
            request_controller=controller_request,
        )
    assert len(observed) == 1


def test_run_launch_missing_identity_retains_bounded_adapter_result(
    tmp_path, monkeypatch
):
    launches = tmp_path / "launches"
    runs = tmp_path / "runs"
    state = tmp_path / "state"
    bundles = tmp_path / "bundles"
    for path in (runs, state, bundles):
        path.mkdir()
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(runs))
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(state))
    monkeypatch.setenv("ADA2_DWARF_BUNDLES_DIR", str(bundles))
    plan = resolve_run_plan({"scenario_id": "edge-cases-cbor-tx-body-amaru"})
    launch = create_launch(plan, root=launches)
    runtime = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path / "controller-state",
        host_state_dir="/srv/dwarf",
        launch_root=launches,
        adapter_runner=lambda *_: DockerResult(
            37,
            "adapter started\n"
            "https://example.invalid/run?token=OUTPUT_SECRET\n"
            "postgresql://alice:s3cr3t@db/internal: not found\n"
            "AWS_ACCESS_KEY_ID=AKIA_PRIVATE: command not found\n",
            "/usr/bin/env: cardano-profile: Permission denied\n"
            "Authorization: Bearer HEADER_SECRET\n"
            "UNLABELED_PRIVATE /private/path: permission denied\n"
            "bad\x00interpreter: Exec format error\n",
        ),
    )

    with pytest.raises(
        DockerRuntimeError, match="scenario runner returned no canonical run identity"
    ) as raised:
        runtime.run_launch(
            launch_id=launch["launch_id"],
            plan_digest=digest_run_plan(plan),
            execution_id="run-execution-missing-identity",
            timeout=2400,
        )

    assert raised.value.failure_code == "run_adapter_identity_missing"
    assert raised.value.failure_stage == "adapter_execution"
    assert raised.value.return_code == 37
    assert raised.value.adapter_output["stdout"] == [
        "[redacted]",
        "[redacted]",
        "adapter executable or dependency is unavailable",
        "adapter executable or dependency is unavailable",
    ]
    assert raised.value.adapter_output["stderr"] == [
        "adapter process permission denied",
        "[redacted]",
        "adapter process permission denied",
        "adapter executable format is invalid",
    ]
    encoded_output = json.dumps(raised.value.adapter_output)
    for secret in (
        "OUTPUT_SECRET",
        "HEADER_SECRET",
        "s3cr3t",
        "AKIA_PRIVATE",
        "UNLABELED_PRIVATE",
        "/private/path",
        "/usr/bin/env",
    ):
        assert secret not in encoded_output


def test_real_run_dispatch_seals_missing_identity_failure_without_reexecution(
    tmp_path, monkeypatch
):
    launches = tmp_path / "launches"
    runs = tmp_path / "runs"
    state = tmp_path / "state"
    bundles = tmp_path / "bundles"
    controller_state = tmp_path / "controller-state"
    for path in (runs, state, bundles):
        path.mkdir()
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(runs))
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(state))
    monkeypatch.setenv("ADA2_DWARF_BUNDLES_DIR", str(bundles))
    plan = resolve_run_plan({"scenario_id": "edge-cases-cbor-tx-body-amaru"})
    launch = create_launch(plan, root=launches)
    calls = []

    def adapter(*args):
        calls.append(args)
        return DockerResult(
            37,
            "progress contains PRIVATE_STDOUT\n",
            "/usr/bin/env: cardano-profile: Permission denied\n",
        )

    runtime = DockerRuntime(
        installation_id="install",
        state_dir=controller_state,
        host_state_dir="/srv/dwarf",
        launch_root=launches,
        adapter_runner=adapter,
    )
    token = "controller-token-" + "x" * 32
    service = ControllerService(token, controller_state, runtime, "rev", "install")
    request = {
        "schema_version": "v1",
        "request_id": str(uuid.uuid4()),
        "operation": "run.execute",
        "timeout_seconds": 2400,
        "launch_id": launch["launch_id"],
        "plan_digest": digest_run_plan(plan),
        "execution_id": "run-execution-real-missing-identity",
        "idempotency_key": "run-real-missing-identity",
    }

    status, response = service.dispatch(
        "POST",
        "/v1/requests",
        {"Authorization": "Bearer " + token},
        json.dumps(request).encode(),
    )

    assert status == 500
    assert response["failure"]["code"] == "run_adapter_identity_missing"
    assert response["failure"]["return_code"] == 37
    assert response["failure"]["adapter_output"]["stderr"] == [
        "adapter process permission denied"
    ]
    execution = service.dispatch(
        "POST",
        "/v1/requests",
        {"Authorization": "Bearer " + token},
        json.dumps(
            {
                "schema_version": "v1",
                "request_id": str(uuid.uuid4()),
                "operation": "execution.status",
                "timeout_seconds": 10,
                "execution_id": "run-execution-real-missing-identity",
            }
        ).encode(),
    )[1]
    assert execution["failure"] == response["failure"]
    status_again, _ = service.dispatch(
        "POST",
        "/v1/requests",
        {"Authorization": "Bearer " + token},
        json.dumps(request).encode(),
    )
    assert status_again == 409
    assert len(calls) == 1
    assert "PRIVATE_STDOUT" not in json.dumps(response)


def test_run_launch_revalidates_runtime_only_measurement_catalog(
    tmp_path, monkeypatch
):
    launches = tmp_path / "launches"
    runs = tmp_path / "runs"
    state = tmp_path / "state"
    bundles = tmp_path / "bundles"
    measurements = state / "measurements"
    measurement_profiles = state / "measurement-profiles"
    for path in (runs, bundles, measurements, measurement_profiles):
        path.mkdir(parents=True)

    package_root = Path(__file__).resolve().parents[1] / "dwarf"
    for packaged in (package_root / "measurements").glob("*.yaml"):
        shutil.copy2(packaged, measurements / packaged.name)
    for packaged in (package_root / "measurement-profiles").glob("*.yaml"):
        shutil.copy2(packaged, measurement_profiles / packaged.name)
    source = (
        package_root / "measurements" / "amaru-external-sync-speed.yaml"
    ).read_text(encoding="utf-8")
    measurements.joinpath("runtime-only-amaru-sync.yaml").write_text(
        source.replace(
            "id: amaru-external-sync-speed",
            "id: runtime-only-amaru-sync",
            1,
        ).replace(
            "measurements/amaru-external-sync-speed/result.json",
            "measurements/runtime-only-amaru-sync/result.json",
            1,
        ),
        encoding="utf-8",
    )
    measurement_profiles.joinpath("runtime-only-amaru-profile.yaml").write_text(
        """schema_version: v1
id: runtime-only-amaru-profile
title: Runtime-only Amaru profile
description: Proves controller re-resolution of a dashboard-managed catalog.
implementation: amaru
target_modes: [stock]
measurements:
  - {id: runtime-only-amaru-sync, enabled: true, parameters: {}, threshold_gate: {enabled: false, thresholds: []}}
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(runs))
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(state))
    monkeypatch.setenv("ADA2_DWARF_BUNDLES_DIR", str(bundles))
    monkeypatch.setenv("ADA2_DWARF_MEASUREMENTS_DIR", str(measurements))
    monkeypatch.setenv(
        "ADA2_DWARF_MEASUREMENT_PROFILES_DIR", str(measurement_profiles)
    )
    plan = resolve_run_plan(
        {
            "scenario_id": "amaru-measurement-e2e-stock",
            "measurement_profile": "runtime-only-amaru-profile",
        }
    )
    assert [item["id"] for item in plan["measurements"]["resolved"]] == [
        "runtime-only-amaru-sync"
    ]
    digest = digest_run_plan(plan)
    launch = create_launch(plan, root=launches)
    observed = []

    def adapter(argv, timeout, execution_id):
        observed.append((argv, timeout, execution_id))
        run_id = "20261010T235900Z-runtime-catalog"
        run_dir = runs / run_id
        run_dir.mkdir()
        (run_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "exit_status": "pass",
                    "execution": {"security_verdict": "pass"},
                }
            ),
            encoding="utf-8",
        )
        return DockerResult(0, f"run_id: {run_id}\n", "")

    runtime = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path / "controller-state",
        host_state_dir="/srv/dwarf",
        launch_root=launches,
        adapter_runner=adapter,
    )
    monkeypatch.setattr(
        runtime,
        "_attach_run_network",
        lambda *_args, **_kwargs: {"network_name": "owned-runtime-network"},
    )
    monkeypatch.setattr(runtime, "_detach_run_network", lambda *_args, **_kwargs: None)

    result = runtime.run_launch(
        launch_id=launch["launch_id"],
        plan_digest=digest,
        execution_id="run-execution-runtime-catalog",
        timeout=2400,
    )

    assert result["run_id"] == "20261010T235900Z-runtime-catalog"
    assert len(observed) == 1


def test_run_launch_revalidates_user_scenario_from_separate_controller_catalog(
    tmp_path, monkeypatch
):
    launches = tmp_path / "launches"
    dashboard_scenarios = tmp_path / "dashboard-scenarios"
    controller_scenarios = tmp_path / "controller-scenarios"
    runs = tmp_path / "runs"
    state = tmp_path / "state"
    bundles = tmp_path / "bundles"
    for path in (
        dashboard_scenarios,
        controller_scenarios,
        runs,
        state,
        bundles,
    ):
        path.mkdir()
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(runs))
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(state))
    monkeypatch.setenv("ADA2_DWARF_BUNDLES_DIR", str(bundles))

    scenario_id = "user-controller-catalog-cbor"
    source = (
        Path(__file__).resolve().parents[1]
        / "dwarf"
        / "scenarios"
        / "edge-cases-cbor-tx-body-amaru.yaml"
    ).read_text(encoding="utf-8")
    scenario_body = source.replace(
        '"id": "edge-cases-cbor-tx-body-amaru"',
        f'"id": "{scenario_id}"',
        1,
    )
    dashboard_source = dashboard_scenarios / f"{scenario_id}.yaml"
    controller_source = controller_scenarios / f"{scenario_id}.yaml"
    dashboard_source.write_text(scenario_body, encoding="utf-8")
    controller_source.write_text(scenario_body, encoding="utf-8")

    monkeypatch.setenv("ADA2_DWARF_SCENARIOS_DIR", str(dashboard_scenarios))
    plan = resolve_run_plan({"scenario_id": scenario_id})
    digest = digest_run_plan(plan)
    launch = create_launch(plan, root=launches)
    monkeypatch.setenv("ADA2_DWARF_SCENARIOS_DIR", str(controller_scenarios))
    observed = []

    def adapter(argv, timeout, execution_id):
        observed.append((argv, timeout, execution_id))
        run_id = "20261011T180000Z-user-scenario"
        run_dir = runs / run_id
        run_dir.mkdir()
        (run_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "exit_status": "pass",
                    "execution": {"security_verdict": "pass"},
                }
            ),
            encoding="utf-8",
        )
        return DockerResult(0, f"run_id: {run_id}\n", "")

    runtime = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path / "controller-state",
        host_state_dir="/srv/dwarf",
        launch_root=launches,
        adapter_runner=adapter,
    )
    result = runtime.run_launch(
        launch_id=launch["launch_id"],
        plan_digest=digest,
        execution_id="run-execution-user-scenario",
        timeout=2400,
    )

    assert result["run_id"] == "20261011T180000Z-user-scenario"
    assert len(observed) == 1
    assert plan["scenario"]["digest"] == (
        "sha256:" + hashlib.sha256(scenario_body.encode("utf-8")).hexdigest()
    )

    controller_source.write_text(
        scenario_body.replace('"title":', '"description": "tampered",\n  "title":', 1),
        encoding="utf-8",
    )
    with pytest.raises(DockerRuntimeError, match="active catalog"):
        runtime.run_launch(
            launch_id=launch["launch_id"],
            plan_digest=digest,
            execution_id="run-execution-user-scenario-tampered",
            timeout=2400,
        )
    assert len(observed) == 1


def test_run_launch_executes_controller_private_snapshot_not_mutable_shared_input(
    tmp_path, monkeypatch
):
    launches = tmp_path / "shared-launches"
    runs = tmp_path / "runs"
    state = tmp_path / "state"
    bundles = tmp_path / "bundles"
    for path in (runs, state, bundles):
        path.mkdir()
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(runs))
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(state))
    monkeypatch.setenv("ADA2_DWARF_BUNDLES_DIR", str(bundles))
    plan = resolve_run_plan({"scenario_id": "edge-cases-cbor-tx-body-amaru"})
    launch = create_launch(plan, root=launches)
    record_launch_preflight(
        launch["launch_id"],
        {"state": "ready", "checks": [{"id": "trusted-preflight", "passed": True}]},
        root=launches,
    )
    trusted_scenario = (launch["path"] / "scenario.yaml").read_bytes()
    observed = {}

    def adapter(argv, timeout, execution_id):
        (launch["path"] / "scenario.yaml").write_text(
            '{"id":"attacker-controlled","runtime":"library"}\n'
        )
        executed_path = Path(argv[-3])
        launch_root_argument = next(
            item for item in argv if item.startswith("ADA2_DWARF_LAUNCH_ROOT=")
        )
        observed.update(
            {
                "executed_path": executed_path,
                "executed_bytes": executed_path.read_bytes(),
                "launch_root": Path(launch_root_argument.split("=", 1)[1]),
                "preflight": json.loads(
                    (
                        Path(launch_root_argument.split("=", 1)[1])
                        / launch["launch_id"]
                        / "preflight.json"
                    ).read_text()
                ),
            }
        )
        run_id = "20261008T120000Z-private-snapshot"
        run_dir = runs / run_id
        run_dir.mkdir()
        (run_dir / "manifest.json").write_text(json.dumps({
            "run_id": run_id,
            "exit_status": "pass",
            "execution": {"security_verdict": "pass"},
        }))
        return DockerResult(0, f"run_id: {run_id}\n", "")

    runtime = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path / "controller-state",
        host_state_dir="/srv/dwarf",
        launch_root=launches,
        adapter_runner=adapter,
    )

    result = runtime.run_launch(
        launch_id=launch["launch_id"],
        plan_digest=digest_run_plan(plan),
        execution_id="run-execution-private-snapshot",
        timeout=2400,
    )

    assert result["run_id"] == "20261008T120000Z-private-snapshot"
    assert observed["executed_path"] != launch["path"] / "scenario.yaml"
    assert observed["executed_bytes"] == trusted_scenario
    assert observed["launch_root"].is_relative_to(
        tmp_path / "controller-state" / "controller" / "run-launches"
    )
    assert observed["preflight"]["checks"] == [
        {"id": "trusted-preflight", "passed": True}
    ]


def test_run_launch_rejects_self_consistent_but_catalog_divergent_inputs(
    tmp_path, monkeypatch
):
    launches = tmp_path / "launches"
    for name in ("runs", "state", "bundles"):
        path = tmp_path / name
        path.mkdir()
        monkeypatch.setenv(f"ADA2_DWARF_{name.upper()}_DIR", str(path))
    plan = resolve_run_plan({"scenario_id": "edge-cases-cbor-tx-body-amaru"})
    launch = create_launch(plan, root=launches)
    tampered = b'{"id":"attacker-controlled","runtime":"library"}\n'
    (launch["path"] / "scenario.yaml").write_bytes(tampered)
    retained = json.loads((launch["path"] / "plan.json").read_text())
    retained["artifacts"]["scenario.yaml"] = (
        "sha256:" + hashlib.sha256(tampered).hexdigest()
    )
    (launch["path"] / "plan.json").write_text(
        json.dumps(retained, indent=2, sort_keys=True) + "\n"
    )
    runtime = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path / "controller-state",
        host_state_dir="/srv/dwarf",
        launch_root=launches,
        adapter_runner=lambda *_: (_ for _ in ()).throw(
            AssertionError("tampered launch must not execute")
        ),
    )

    with pytest.raises(DockerRuntimeError, match="active catalog"):
        runtime.run_launch(
            launch_id=launch["launch_id"],
            plan_digest=digest_run_plan(plan),
            execution_id="run-execution-tampered-launch",
            timeout=2400,
        )


def test_run_launch_rejects_retained_profile_rebinding(tmp_path, monkeypatch):
    launches = tmp_path / "launches"
    for name in ("runs", "state", "bundles"):
        path = tmp_path / name
        path.mkdir()
        monkeypatch.setenv(f"ADA2_DWARF_{name.upper()}_DIR", str(path))
    plan = resolve_run_plan({"scenario_id": "cardano-measurement-e2e-stock"})
    launch = create_launch(plan, root=launches)
    retained = json.loads((launch["path"] / "plan.json").read_text())
    retained["profile"]["id"] = "victim-profile"
    (launch["path"] / "plan.json").write_text(
        json.dumps(retained, indent=2, sort_keys=True) + "\n"
    )
    runtime = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path / "controller-state",
        host_state_dir="/srv/dwarf",
        launch_root=launches,
        runner=lambda *_: (_ for _ in ()).throw(
            AssertionError("tampered profile must not reach Docker")
        ),
        adapter_runner=lambda *_: (_ for _ in ()).throw(
            AssertionError("tampered profile must not execute")
        ),
    )

    with pytest.raises(DockerRuntimeError, match="active catalog"):
        runtime.run_launch(
            launch_id=launch["launch_id"],
            plan_digest=digest_run_plan(plan),
            execution_id="run-execution-profile-rebinding",
            timeout=2400,
        )


def test_profile_run_temporarily_joins_only_the_owned_topology_network(
    tmp_path, monkeypatch
):
    launches = tmp_path / "launches"
    runs = tmp_path / "runs"
    state = tmp_path / "state"
    bundles = tmp_path / "bundles"
    runtime_base = tmp_path / "controller" / "topology-runtimes"
    profile_id = "profile-s-cardano-measurement-stock-control"
    current = runtime_base / profile_id / "current"
    for path in (runs, state, bundles, current):
        path.mkdir(parents=True)
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(runs))
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(state))
    monkeypatch.setenv("ADA2_DWARF_BUNDLES_DIR", str(bundles))
    monkeypatch.setenv("ADA2_DWARF_REMOTE_BASE", str(runtime_base))
    monkeypatch.setenv("HOSTNAME", "controller-container-id")
    (current / "runtime.json").write_text(
        json.dumps(
            {
                "profile_id": profile_id,
                "nodes": [{"id": "node1", "container_name": "owned-node1"}],
            }
        )
    )
    plan = resolve_run_plan({"scenario_id": "cardano-measurement-e2e-stock"})
    digest = digest_run_plan(plan)
    launch = create_launch(plan, root=launches)
    operations = []
    disconnect_execution_ids = []
    adapter_commands = []
    network_name = "dwarf-owned-topology-net"

    def docker_runner(argv, timeout, execution_id):
        operations.append(tuple(argv[1:]))
        if argv[1:4] == ("container", "inspect", "owned-node1"):
            return DockerResult(
                0,
                json.dumps(
                    [
                        {
                            "Config": {
                                "Labels": {
                                    "io.dwarf.managed": "true",
                                    "io.dwarf.installation": "install",
                                    "io.dwarf.topology": profile_id,
                                    "io.dwarf.component": "node",
                                }
                            },
                            "NetworkSettings": {
                                "Networks": {network_name: {"IPAddress": "10.20.0.2"}}
                            }
                        }
                    ]
                ),
                "",
            )
        if argv[1:4] == ("network", "inspect", network_name):
            return DockerResult(
                0,
                json.dumps(
                    [
                        {
                            "Labels": {
                                "io.dwarf.managed": "true",
                                "io.dwarf.installation": "install",
                                "io.dwarf.topology": profile_id,
                                "io.dwarf.component": "network",
                            }
                        }
                    ]
                ),
                "",
            )
        if argv[1:3] in {("network", "connect"), ("network", "disconnect")}:
            if argv[1:3] == ("network", "disconnect"):
                disconnect_execution_ids.append(execution_id)
            return DockerResult(0, "", "")
        raise AssertionError(argv)

    def adapter(argv, timeout, execution_id):
        operations.append(("scenario-adapter",))
        adapter_commands.append(argv)
        run_id = "20261008T120000Z-controller-network"
        run_dir = runs / run_id
        run_dir.mkdir()
        (run_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "exit_status": "pass",
                    "execution": {"security_verdict": "pass"},
                }
            )
        )
        return DockerResult(0, f"run_id: {run_id}\n", "")

    runtime = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path / "controller-state",
        host_state_dir="/srv/dwarf",
        launch_root=launches,
        runner=docker_runner,
        adapter_runner=adapter,
    )

    runtime.run_launch(
        launch_id=launch["launch_id"],
        plan_digest=digest,
        execution_id="run-execution-network-proof",
        timeout=2400,
    )

    assert ("network", "connect", network_name, "controller-container-id") in operations
    assert ("network", "disconnect", network_name, "controller-container-id") in operations
    assert operations.index(("network", "connect", network_name, "controller-container-id")) < operations.index(("scenario-adapter",))
    assert operations.index(("scenario-adapter",)) < operations.index(("network", "disconnect", network_name, "controller-container-id"))
    assert disconnect_execution_ids == [None]
    assert f"ADA2_DWARF_RUN_NETWORK={network_name}" in adapter_commands[0]


def test_amaru_control_run_joins_network_from_exact_selected_runtime_service(
    tmp_path, monkeypatch
):
    runtime_base = tmp_path / "controller" / "topology-runtimes"
    profile_id = "profile-o-amaru-target-latest-confirmed"
    compose_project = f"dwarf-profile-install-{profile_id}"
    current = runtime_base / profile_id / "current"
    current.mkdir(parents=True)
    monkeypatch.setenv("ADA2_DWARF_REMOTE_BASE", str(runtime_base))
    monkeypatch.setenv("HOSTNAME", "controller-container-id")
    (current / "runtime.json").write_text(
        json.dumps(
            {
                "schema_version": "v1",
                "profile_id": profile_id,
                "compose_project": compose_project,
                "lifecycle": "cardano_amaru_relay_bootstrap_control",
                "nodes": [
                    {
                        "id": "fake-legacy-node",
                        "container_name": "fake-legacy-container",
                    }
                ],
                "actual_topology": {
                    "selected_nodes": [
                        {
                            "id": "amaru1",
                            "implementation": "amaru",
                            "service": "amaru-relay-1",
                            "supporting": False,
                        },
                        {
                            "id": "bootstrap-cardano",
                            "implementation": "cardano-node",
                            "service": "p1",
                            "supporting": True,
                        },
                    ],
                    "selected_node_services": {
                        "amaru1": "amaru-relay-1",
                        "bootstrap-cardano": "p1",
                    },
                },
            }
        )
    )
    network_name = compose_project + "-default"
    consumer_network = compose_project + "-amaru-consumer-net"
    container_name = compose_project + "-amaru-relay-1-1"
    support_container_name = compose_project + "-p1-1"
    operations = []

    def docker_runner(argv, timeout, execution_id):
        operation = tuple(argv[1:])
        operations.append(operation)
        if operation in {
            ("container", "inspect", container_name),
            ("container", "inspect", support_container_name),
        }:
            service = "amaru-relay-1" if operation[-1] == container_name else "p1"
            name = container_name if service == "amaru-relay-1" else support_container_name
            networks = {network_name: {"IPAddress": "10.20.0.2"}}
            if service == "amaru-relay-1":
                networks[consumer_network] = {"IPAddress": "10.21.0.2"}
            return DockerResult(
                0,
                json.dumps(
                    [
                        {
                            "Name": "/" + name,
                            "Config": {
                                "Labels": {
                                    "io.dwarf.managed": "true",
                                    "io.dwarf.installation": "install",
                                    "io.dwarf.topology": profile_id,
                                    "io.dwarf.component": "node",
                                    "com.docker.compose.project": compose_project,
                                    "com.docker.compose.service": service,
                                }
                            },
                            "NetworkSettings": {"Networks": networks},
                        }
                    ]
                ),
                "",
            )
        if operation in {
            ("network", "inspect", network_name),
            ("network", "inspect", consumer_network),
        }:
            return DockerResult(
                0,
                json.dumps(
                    [
                        {
                            "Labels": {
                                "io.dwarf.managed": "true",
                                "io.dwarf.installation": "install",
                                "io.dwarf.topology": profile_id,
                                "io.dwarf.component": "network",
                            }
                        }
                    ]
                ),
                "",
            )
        if operation in {
            ("network", "connect", network_name, "controller-container-id"),
            ("network", "disconnect", network_name, "controller-container-id"),
        }:
            return DockerResult(0, "", "")
        raise AssertionError(argv)

    runtime = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path / "controller-state",
        host_state_dir="/srv/dwarf",
        runner=docker_runner,
    )
    verified = []

    def verify_retained_runtime(**kwargs):
        verified.append(kwargs)
        return {
            "lifecycle": "cardano_amaru_relay_bootstrap_control",
            "controller_node_services": {
                "amaru1": "amaru-relay-1",
                "bootstrap-cardano": "p1",
            },
            "substrate": {
                "nodes": [
                    {"id": "amaru1", "supporting": False},
                    {"id": "bootstrap-cardano", "supporting": True},
                ],
                "actual_topology": {
                    "network_names": ["default", "amaru-consumer-net"]
                },
            },
        }

    monkeypatch.setattr(runtime, "_verify_retained_runtime", verify_retained_runtime)

    attachment = runtime._attach_run_network(
        profile_id,
        "run-execution-amaru-control-network-proof",
    )
    runtime._detach_run_network(attachment, state="detached")

    assert attachment["network_name"] == network_name
    assert verified == [
        {
            "runtime_root": current,
            "topology_id": profile_id,
            "compose_project": compose_project,
            "enforce_golden": True,
        }
    ]
    assert ("container", "inspect", container_name) in operations
    assert ("container", "inspect", support_container_name) in operations
    assert (
        "network",
        "connect",
        network_name,
        "controller-container-id",
    ) in operations
    assert (
        "network",
        "disconnect",
        network_name,
        "controller-container-id",
    ) in operations


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("missing_mapping", "mapping is invalid"),
        ("duplicate_mapping", "mapping is invalid"),
        ("foreign_service", "service ownership is invalid"),
        ("ambiguous_networks", "exactly one owned run network"),
    ],
)
def test_amaru_control_run_network_fails_closed_on_untrusted_mapping_or_network(
    tmp_path, monkeypatch, case, message
):
    runtime_base = tmp_path / "controller" / "topology-runtimes"
    profile_id = "profile-o-amaru-target-latest-confirmed"
    compose_project = f"dwarf-profile-install-{profile_id}"
    current = runtime_base / profile_id / "current"
    current.mkdir(parents=True)
    monkeypatch.setenv("ADA2_DWARF_REMOTE_BASE", str(runtime_base))
    monkeypatch.setenv("HOSTNAME", "controller-container-id")
    (current / "runtime.json").write_text(
        json.dumps(
            {
                "profile_id": profile_id,
                "compose_project": compose_project,
                "lifecycle": "cardano_amaru_relay_bootstrap_control",
            }
        )
    )
    selected_nodes = [
        {"id": "amaru1", "supporting": False},
        {"id": "bootstrap-cardano", "supporting": True},
    ]
    mapping = {
        "amaru1": "amaru-relay-1",
        "bootstrap-cardano": "p1",
    }
    if case == "missing_mapping":
        mapping.pop("amaru1")
    elif case == "duplicate_mapping":
        mapping["bootstrap-cardano"] = "amaru-relay-1"
    network_keys = ["default"]
    if case == "ambiguous_networks":
        network_keys.append("amaru-consumer-net")
    network_names = [f"{compose_project}-{name}" for name in network_keys]
    container_name = f"{compose_project}-amaru-relay-1-1"

    def docker_runner(argv, timeout, execution_id):
        operation = tuple(argv[1:])
        support_container_name = f"{compose_project}-p1-1"
        if operation in {
            ("container", "inspect", container_name),
            ("container", "inspect", support_container_name),
        }:
            service = "amaru-relay-1" if operation[-1] == container_name else "p1"
            name = container_name if service == "amaru-relay-1" else support_container_name
            labels = {
                "io.dwarf.managed": "true",
                "io.dwarf.installation": "install",
                "io.dwarf.topology": profile_id,
                "io.dwarf.component": "node",
                "com.docker.compose.project": compose_project,
                "com.docker.compose.service": service,
            }
            if case == "foreign_service" and service == "amaru-relay-1":
                labels["com.docker.compose.project"] = "foreign-project"
            attached_networks = list(network_names)
            if case != "ambiguous_networks" and service == "p1":
                attached_networks = attached_networks[:1]
            return DockerResult(
                0,
                json.dumps(
                    [
                        {
                            "Name": "/" + name,
                            "Config": {"Labels": labels},
                            "NetworkSettings": {
                                "Networks": {name: {} for name in attached_networks}
                            },
                        }
                    ]
                ),
                "",
            )
        if operation[:2] == ("network", "inspect"):
            return DockerResult(
                0,
                json.dumps(
                    [
                        {
                            "Labels": {
                                "io.dwarf.managed": "true",
                                "io.dwarf.installation": "install",
                                "io.dwarf.topology": profile_id,
                                "io.dwarf.component": "network",
                            }
                        }
                    ]
                ),
                "",
            )
        raise AssertionError(argv)

    runtime = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path / "controller-state",
        host_state_dir="/srv/dwarf",
        runner=docker_runner,
    )
    monkeypatch.setattr(
        runtime,
        "_verify_retained_runtime",
        lambda **_: {
            "controller_node_services": mapping,
            "substrate": {
                "nodes": selected_nodes,
                "actual_topology": {"network_names": network_keys},
            },
        },
    )

    with pytest.raises(DockerRuntimeError, match=message):
        runtime._attach_run_network(
            profile_id,
            f"run-execution-{case}",
        )


def test_profile_run_cancelled_during_network_attach_never_starts_adapter(
    tmp_path, monkeypatch
):
    launches = tmp_path / "launches"
    runs = tmp_path / "runs"
    runtime_base = tmp_path / "controller" / "topology-runtimes"
    profile_id = "profile-s-cardano-measurement-stock-control"
    current = runtime_base / profile_id / "current"
    for path in (runs, tmp_path / "state", tmp_path / "bundles", current):
        path.mkdir(parents=True)
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(runs))
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ADA2_DWARF_BUNDLES_DIR", str(tmp_path / "bundles"))
    monkeypatch.setenv("ADA2_DWARF_REMOTE_BASE", str(runtime_base))
    monkeypatch.setenv("HOSTNAME", "controller-container-id")
    (current / "runtime.json").write_text(
        json.dumps(
            {
                "profile_id": profile_id,
                "nodes": [{"id": "node1", "container_name": "owned-node1"}],
            }
        )
    )
    plan = resolve_run_plan({"scenario_id": "cardano-measurement-e2e-stock"})
    launch = create_launch(plan, root=launches)
    network_name = "dwarf-owned-topology-net"
    cancellation = threading.Event()
    operations = []

    def docker_runner(argv, timeout, execution_id):
        operation = tuple(argv[1:])
        operations.append(operation)
        if operation == ("container", "inspect", "owned-node1"):
            return DockerResult(
                0,
                json.dumps(
                    [{
                        "Config": {"Labels": {
                            "io.dwarf.managed": "true",
                            "io.dwarf.installation": "install",
                            "io.dwarf.topology": profile_id,
                            "io.dwarf.component": "node",
                        }},
                        "NetworkSettings": {"Networks": {network_name: {}}},
                    }]
                ),
                "",
            )
        if operation == ("network", "inspect", network_name):
            return DockerResult(
                0,
                json.dumps([{"Labels": {
                    "io.dwarf.managed": "true",
                    "io.dwarf.installation": "install",
                    "io.dwarf.topology": profile_id,
                    "io.dwarf.component": "network",
                }}]),
                "",
            )
        if operation == ("network", "connect", network_name, "controller-container-id"):
            cancellation.set()
            return DockerResult(0, "", "")
        if operation == ("network", "disconnect", network_name, "controller-container-id"):
            return DockerResult(0, "", "")
        raise AssertionError(argv)

    runtime = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path / "controller-state",
        host_state_dir="/srv/dwarf",
        launch_root=launches,
        runner=docker_runner,
        adapter_runner=lambda *_: (_ for _ in ()).throw(
            AssertionError("cancelled launch must not start the adapter")
        ),
    )

    result = runtime.run_launch(
        launch_id=launch["launch_id"],
        plan_digest=digest_run_plan(plan),
        execution_id="run-execution-cancel-during-attach",
        timeout=2400,
        cancel_event=cancellation,
    )

    assert result["status"] == "cancelled"
    assert ("network", "disconnect", network_name, "controller-container-id") in operations


def test_restart_reconciles_a_crash_left_run_network_attachment(tmp_path, monkeypatch):
    launches = tmp_path / "launches"
    runs = tmp_path / "runs"
    controller_state = tmp_path / "controller-state"
    runtime_base = tmp_path / "controller" / "topology-runtimes"
    profile_id = "profile-s-cardano-measurement-stock-control"
    network_name = "dwarf-owned-topology-net"
    current = runtime_base / profile_id / "current"
    for path in (runs, tmp_path / "state", tmp_path / "bundles", current):
        path.mkdir(parents=True)
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(runs))
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ADA2_DWARF_BUNDLES_DIR", str(tmp_path / "bundles"))
    monkeypatch.setenv("ADA2_DWARF_REMOTE_BASE", str(runtime_base))
    monkeypatch.setenv("HOSTNAME", "controller-container-id")
    (current / "runtime.json").write_text(
        json.dumps({
            "profile_id": profile_id,
            "nodes": [{"id": "node1", "container_name": "owned-node1"}],
        })
    )
    plan = resolve_run_plan({"scenario_id": "cardano-measurement-e2e-stock"})
    launch = create_launch(plan, root=launches)
    attached = {"value": False}

    def inspect_result(operation):
        if operation == ("container", "inspect", "owned-node1"):
            return DockerResult(0, json.dumps([{
                "Config": {"Labels": {
                    "io.dwarf.managed": "true",
                    "io.dwarf.installation": "install",
                    "io.dwarf.topology": profile_id,
                    "io.dwarf.component": "node",
                }},
                "NetworkSettings": {"Networks": {network_name: {}}},
            }]), "")
        if operation == ("network", "inspect", network_name):
            return DockerResult(0, json.dumps([{
                "Labels": {
                    "io.dwarf.managed": "true",
                    "io.dwarf.installation": "install",
                    "io.dwarf.topology": profile_id,
                    "io.dwarf.component": "network",
                },
                "Containers": (
                    {"controller-container-id": {"Name": "controller-container-id"}}
                    if attached["value"] else {}
                ),
            }]), "")
        return None

    def crashing_runner(argv, timeout, execution_id):
        operation = tuple(argv[1:])
        inspected = inspect_result(operation)
        if inspected is not None:
            return inspected
        if operation == ("network", "connect", network_name, "controller-container-id"):
            attached["value"] = True
            raise SystemExit("simulated controller crash after Docker connected")
        raise AssertionError(argv)

    first = DockerRuntime(
        installation_id="install",
        state_dir=controller_state,
        host_state_dir="/srv/dwarf",
        launch_root=launches,
        runner=crashing_runner,
    )
    with pytest.raises(SystemExit, match="simulated controller crash"):
        first.run_launch(
            launch_id=launch["launch_id"],
            plan_digest=digest_run_plan(plan),
            execution_id="run-execution-crash-attachment",
            timeout=2400,
        )
    assert attached["value"] is True

    def recovery_runner(argv, timeout, execution_id):
        operation = tuple(argv[1:])
        inspected = inspect_result(operation)
        if inspected is not None:
            return inspected
        if operation == ("network", "disconnect", network_name, "controller-container-id"):
            attached["value"] = False
            return DockerResult(0, "", "")
        raise AssertionError(argv)

    restarted = DockerRuntime(
        installation_id="install",
        state_dir=controller_state,
        host_state_dir="/srv/dwarf",
        launch_root=launches,
        runner=recovery_runner,
    )

    reconciled = restarted.reconcile_run_attachments()

    assert attached["value"] is False
    assert reconciled == [{
        "execution_id": "run-execution-crash-attachment",
        "network_name": network_name,
        "state": "reconciled",
    }]


def test_restart_reconciliation_does_not_disconnect_absent_membership(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HOSTNAME", "controller-container-id")
    state = tmp_path / "controller-state"
    attachment_dir = state / "controller" / "run-network-attachments"
    attachment_dir.mkdir(parents=True)
    execution_id = "run-execution-connect-never-applied"
    profile_id = "profile-s-cardano-measurement-stock-control"
    network_name = "dwarf-owned-topology-net"
    (attachment_dir / f"{execution_id}.json").write_text(json.dumps({
        "schema_version": "v1",
        "execution_id": execution_id,
        "installation_id": "install",
        "profile_id": profile_id,
        "network_name": network_name,
        "controller_id": "controller-container-id",
        "state": "attach_failed",
        "created_at": "2026-10-08T12:00:00Z",
        "completed_at": "2026-10-08T12:00:01Z",
    }))

    def runner(argv, timeout, execution_id):
        assert tuple(argv[1:]) == ("network", "inspect", network_name)
        return DockerResult(0, json.dumps([{
            "Labels": {
                "io.dwarf.managed": "true",
                "io.dwarf.installation": "install",
                "io.dwarf.topology": profile_id,
                "io.dwarf.component": "network",
            },
            "Containers": {},
        }]), "")

    runtime = DockerRuntime(
        installation_id="install",
        state_dir=state,
        host_state_dir="/srv/dwarf",
        runner=runner,
    )

    assert runtime.reconcile_run_attachments() == [{
        "execution_id": execution_id,
        "network_name": network_name,
        "state": "reconciled",
    }]


def test_recreated_controller_ignores_terminal_attachment_from_prior_container(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HOSTNAME", "new-controller-id")
    state = tmp_path / "controller-state"
    attachment_dir = state / "controller" / "run-network-attachments"
    attachment_dir.mkdir(parents=True)
    execution_id = "run-execution-prior-container"
    (attachment_dir / f"{execution_id}.json").write_text(json.dumps({
        "schema_version": "v1",
        "execution_id": execution_id,
        "installation_id": "install",
        "profile_id": "profile-s-cardano-measurement-stock-control",
        "network_name": "dwarf-owned-topology-net",
        "controller_id": "old-controller-id",
        "state": "detached",
        "created_at": "2026-10-08T12:00:00Z",
        "completed_at": "2026-10-08T12:00:01Z",
    }))
    runtime = DockerRuntime(
        installation_id="install",
        state_dir=state,
        host_state_dir="/srv/dwarf",
        runner=lambda *_: (_ for _ in ()).throw(
            AssertionError("terminal attachment must require no Docker access")
        ),
    )

    assert runtime.reconcile_run_attachments() == []


@pytest.mark.parametrize("containers", [None, [], "unknown"])
def test_restart_reconciliation_rejects_missing_or_invalid_membership_evidence(
    tmp_path, monkeypatch, containers
):
    monkeypatch.setenv("HOSTNAME", "controller-container-id")
    state = tmp_path / "controller-state"
    attachment_dir = state / "controller" / "run-network-attachments"
    attachment_dir.mkdir(parents=True)
    execution_id = "run-execution-invalid-membership"
    profile_id = "profile-s-cardano-measurement-stock-control"
    network_name = "dwarf-owned-topology-net"
    (attachment_dir / f"{execution_id}.json").write_text(json.dumps({
        "schema_version": "v1",
        "execution_id": execution_id,
        "installation_id": "install",
        "profile_id": profile_id,
        "network_name": network_name,
        "controller_id": "controller-container-id",
        "state": "attached",
        "created_at": "2026-10-08T12:00:00Z",
        "completed_at": None,
    }))
    network = {"Labels": {
        "io.dwarf.managed": "true",
        "io.dwarf.installation": "install",
        "io.dwarf.topology": profile_id,
        "io.dwarf.component": "network",
    }}
    if containers is not None:
        network["Containers"] = containers
    runtime = DockerRuntime(
        installation_id="install",
        state_dir=state,
        host_state_dir="/srv/dwarf",
        runner=lambda *_: DockerResult(0, json.dumps([network]), ""),
    )

    with pytest.raises(DockerRuntimeError, match="membership is invalid"):
        runtime.reconcile_run_attachments()


def test_uncertain_connect_failure_immediately_removes_confirmed_membership(
    tmp_path, monkeypatch
):
    profile_id = "profile-s-cardano-measurement-stock-control"
    network_name = "dwarf-owned-topology-net"
    runtime_base = tmp_path / "controller" / "topology-runtimes"
    current = runtime_base / profile_id / "current"
    current.mkdir(parents=True)
    monkeypatch.setenv("ADA2_DWARF_REMOTE_BASE", str(runtime_base))
    monkeypatch.setenv("HOSTNAME", "controller-container-id")
    (current / "runtime.json").write_text(json.dumps({
        "profile_id": profile_id,
        "nodes": [{"id": "node1", "container_name": "owned-node1"}],
    }))
    attached = {"value": False}
    disconnect_execution_ids = []
    cancellation = threading.Event()

    def runner(argv, timeout, execution_id):
        operation = tuple(argv[1:])
        if operation == ("container", "inspect", "owned-node1"):
            return DockerResult(0, json.dumps([{
                "Config": {"Labels": {
                    "io.dwarf.managed": "true",
                    "io.dwarf.installation": "install",
                    "io.dwarf.topology": profile_id,
                    "io.dwarf.component": "node",
                }},
                "NetworkSettings": {"Networks": {network_name: {}}},
            }]), "")
        if operation == ("network", "inspect", network_name):
            return DockerResult(0, json.dumps([{
                "Labels": {
                    "io.dwarf.managed": "true",
                    "io.dwarf.installation": "install",
                    "io.dwarf.topology": profile_id,
                    "io.dwarf.component": "network",
                },
                "Containers": (
                    {"controller-container-id": {"Name": "controller-container-id"}}
                    if attached["value"] else {}
                ),
            }]), "")
        if operation == ("network", "connect", network_name, "controller-container-id"):
            attached["value"] = True
            cancellation.set()
            return DockerResult(130, "", "connect process cancelled after daemon mutation")
        if operation == ("network", "disconnect", network_name, "controller-container-id"):
            disconnect_execution_ids.append(execution_id)
            attached["value"] = False
            return DockerResult(0, "", "")
        raise AssertionError(argv)

    runtime = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path / "controller-state",
        host_state_dir="/srv/dwarf",
        runner=runner,
    )

    attachment = runtime._attach_run_network(
        profile_id,
        "run-execution-uncertain-connect-failure",
        cancel_event=cancellation,
    )

    assert attachment is None
    assert attached["value"] is False
    assert disconnect_execution_ids == [None]


def _plan(*, action="deploy", nodes=None):
    plan = {
        "action": action,
        "topology_id": "local-lab",
        "topology": {
            "topology_id": "local-lab",
            "network_magic": 42,
            "nodes": nodes or [
                {
                    "id": "amaru-1",
                    "implementation": "amaru",
                    "version": "0.3.0",
                    "image": IMAGE,
                }
            ],
        },
    }
    plan["deployment"] = {
        "schema_version": "v1",
        "adapter": "runtime_compose_substrate.py",
        "compose_project": "dwarf-profile-install-local-lab",
        "substrate": {
            "profile_id": "local-lab",
            "compose_mode": "docker",
            "deployment_adapter": "cardano-compose-local",
            "network": "testnet_42",
            "network_magic": 42,
            "docker_host_runtime_root": "/srv/dwarf/controller/topology-runtimes/local-lab/current",
            "nodes": [
                {
                    **node,
                    "impl": node["implementation"],
                    "role": "producer",
                    "target_mode": "stock",
                }
                for node in plan["topology"]["nodes"]
            ],
            "topology": {"edges": []},
            "controller_ownership": {
                "installation_id": "install",
                "topology_id": "local-lab",
            },
        },
    }
    return plan


def _emit_healthy_adapter_report(argv):
    config = json.loads(Path(argv[-1]).read_text(encoding="utf-8"))
    evidence = Path(config["output_dir"])
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "compose-report.json").write_text(
        json.dumps({"healthy": True, "node_count": len(config["substrate"]["nodes"])}),
        encoding="utf-8",
    )
    runtime = Path(config["runtime_root"])
    substrate = config["substrate"]
    ownership = substrate["controller_ownership"]
    project = config["compose_project"]
    host_root = substrate["docker_host_runtime_root"]
    services = {}
    for node in substrate["nodes"]:
        volumes = [f"{host_root}/env:/env", f"{host_root}/logs:/logs"]
        if node["impl"] == "amaru":
            volumes.append(f"{host_root}/amaru:/amaru")
        services[node["id"]] = {
            "image": node["image"],
            "container_name": f"{project}-{node['id']}-1",
            "labels": {
                "io.dwarf.managed": "true",
                "io.dwarf.installation": ownership["installation_id"],
                "io.dwarf.topology": ownership["topology_id"],
                "io.dwarf.component": "node",
                "io.dwarf.node_id": node["id"],
            },
            "volumes": volumes,
            "ports": ["127.0.0.1:39100:3001"],
            "read_only": True,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
        }
    compose = {
        "services": services,
        "networks": {
            "default": {
                "name": project + "-net",
                "labels": {
                    "io.dwarf.managed": "true",
                    "io.dwarf.installation": ownership["installation_id"],
                    "io.dwarf.topology": ownership["topology_id"],
                    "io.dwarf.component": "network",
                },
            }
        },
    }
    (runtime / "docker-compose.yml").write_text(json.dumps(compose), encoding="utf-8")


def _retain_runtime(runtime, plan, current):
    current.mkdir(parents=True, exist_ok=True)
    config = runtime._deployment_config(plan["deployment"], current)
    runtime._write_json(current / "versioned-deployment.json", config)
    _emit_healthy_adapter_report(("python", "adapter", "--config", str(current / "versioned-deployment.json")))
    runtime._write_runtime_integrity(
        runtime_root=current,
        topology_id=plan["topology_id"],
        compose_project=plan["deployment"]["compose_project"],
    )


def _retain_verified_amaru_control_runtime(runtime, deployment, current):
    """Materialize the production retained-contract shape for command-runner tests."""
    current.mkdir(parents=True, exist_ok=True)
    config = runtime._deployment_config(deployment, current)
    substrate = config["substrate"]
    topology_id = substrate["profile_id"]
    project = config["compose_project"]
    shape = substrate["actual_topology"]
    mapping = config["controller_node_services"]
    nodes = {node["id"]: node for node in substrate["nodes"]}
    node_for_service = {service: nodes[node_id] for node_id, service in mapping.items()}
    common = {
        "io.dwarf.managed": "true",
        "io.dwarf.installation": runtime.installation_id,
        "io.dwarf.topology": topology_id,
    }
    services = {}
    image_ids = {}
    for index, service_name in enumerate(shape["all_services"], start=1):
        node = node_for_service.get(service_name)
        image = str((node or nodes["bootstrap-cardano"])["image"])
        labels = {**common, "io.dwarf.component": "node" if node else "support"}
        if node:
            labels.update(
                {
                    "io.dwarf.node_id": node["id"],
                    "io.dwarf.implementation": node["impl"],
                    "io.dwarf.version": node["version"],
                }
            )
        services[service_name] = {
            "image": image,
            "labels": labels,
            "read_only": True,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
        }
        image_ids[service_name] = "sha256:" + f"{index:064x}"
    networks = {
        name: {
            "name": f"{project}-{name}",
            "labels": {**common, "io.dwarf.component": "network"},
        }
        for name in shape["network_names"]
    }
    volume_key = "p1-state"
    compose = {
        "services": services,
        "networks": networks,
        "volumes": {
            volume_key: {
                "labels": {**common, "io.dwarf.component": "volume"}
            }
        },
    }
    selected_nodes = [
        {
            "id": node_id,
            "service": service_name,
            "implementation": nodes[node_id]["impl"],
            "version": nodes[node_id]["version"],
            "image": nodes[node_id]["image"],
            "supporting": nodes[node_id].get("supporting") is True,
        }
        for node_id, service_name in sorted(mapping.items())
    ]
    identities = {
        service_name: {
            "matched": True,
            "expected_image": nodes[node_id]["image"],
            "expected_image_id": image_ids[service_name],
            "artifact_image_id": image_ids[service_name],
            "running_image_id": image_ids[service_name],
            "wrapper_expected_image": None,
            "wrapper_expected_image_id": None,
            "wrapper_matched": True,
        }
        for node_id, service_name in sorted(mapping.items())
    }
    report = {
        "schema_version": 1,
        "profile_id": topology_id,
        "passed": True,
        "classification": "retained-runtime-ready",
        "failed_gates": [],
        "retained": True,
        "profile_id": topology_id,
        "scope": config["scope"],
        "project": project,
        "identity": {"matched": True, "services": identities},
        "selected_nodes": selected_nodes,
        "actual_topology": shape,
        "observation": {
            "state": "healthy",
            "observation": {"samples": [{"slot": 1}, {"slot": 2}]},
        },
        "required_gates": ["chain_progress", "exact_identity"],
        "gates": {"chain_progress": True, "exact_identity": True},
    }
    runtime_metadata = {
        "profile_id": topology_id,
        "network_magic": substrate["network_magic"],
        "scope": config["scope"],
        "compose_project": project,
        "actual_topology": {**shape, "selected_nodes": selected_nodes},
        "images": {
            "cardano-node": config["cardano_image"],
            "amaru": config["amaru_image"],
        },
    }
    runtime._write_json(current / "versioned-deployment.json", config)
    runtime._write_json(current / "docker-compose.json", compose)
    runtime._write_json(current / "runtime.json", runtime_metadata)
    (current / "evidence").mkdir()
    runtime._write_json(current / "evidence" / "deployment-report.json", report)
    runtime._write_runtime_integrity(
        runtime_root=current,
        topology_id=topology_id,
        compose_project=project,
    )
    containers = {}
    for service_name, service in services.items():
        labels = {
            **service["labels"],
            "com.docker.compose.project": project,
            "com.docker.compose.service": service_name,
        }
        containers[service_name] = _container(
            f"{project}-{service_name}-1",
            labels,
            state="running",
            image=service["image"],
            image_id=image_ids[service_name],
        )
    volume_name = f"{project}_{volume_key}"
    volume = {
        "Name": volume_name,
        "Labels": {
            **common,
            "io.dwarf.component": "volume",
            "com.docker.compose.project": project,
            "com.docker.compose.volume": volume_key,
        },
    }
    return config, compose, report, containers, networks, volume


def test_profile_r_catalog_enrichment_reaches_real_controller_runtime_repair(
    monkeypatch, tmp_path
):
    """The native Amaru-control plan must share dashboard/controller equivalence."""
    from profile_manager import dashboard, deployment_versions, version_discovery
    from profile_manager.profiles import desired_topology_from_substrate
    from profile_manager.run_plan import RunPlanRequest
    from profile_manager.topology_snapshot import build_topology_snapshot

    catalog = version_discovery.load_effective_version_catalog()
    enriched_catalog = json.loads(json.dumps(catalog))
    enriched = False
    for release in enriched_catalog["releases"]:
        if release.get("version") not in {"10.7.1", "10.11.20260912"}:
            continue
        for artifact in release.get("artifacts") or []:
            if artifact.get("kind") == "oci":
                artifact.update(public=True, platforms=["linux/amd64", "linux/arm64"])
                enriched = True
    assert enriched is True
    monkeypatch.setattr(
        version_discovery,
        "load_effective_version_catalog",
        lambda *_args, **_kwargs: json.loads(json.dumps(enriched_catalog)),
    )
    monkeypatch.setattr(
        deployment_versions,
        "load_effective_version_catalog",
        lambda *_args, **_kwargs: json.loads(json.dumps(enriched_catalog)),
    )
    profile = find_profile("profile-r-amaru-measurement-stock-control")
    preview = build_deployment_version_preview(asdict(profile), catalog=enriched_catalog)
    desired = desired_topology_for_profile(profile, preview)
    topology = {
        "topology_id": profile.id,
        "network_magic": desired["network_magic"],
        "nodes": [
            {key: node[key] for key in ("id", "implementation", "version", "image")}
            for node in desired["nodes"]
        ],
    }
    monkeypatch.setenv(
        "DWARF_GOLDEN_IMAGES", ",".join(node["image"] for node in topology["nodes"])
    )
    monkeypatch.setenv("DWARF_HOST_OS", "linux")
    monkeypatch.setenv("DWARF_HOST_ARCH", "amd64")
    state = tmp_path / "state"

    class ChainRuntime(DockerRuntime):
        errors = []

        def snapshot(self, topology_id):
            return {"topology_id": topology_id, "resources": []}

        def apply(self, plan, execution_id, cancel_event):
            try:
                return super().apply(plan, execution_id, cancel_event)
            except BaseException as exc:
                self.errors.append(repr(exc))
                raise

    fixture = {}
    docker_calls = []
    adapter_calls = []

    def runner(argv, timeout, execution_id):
        docker_calls.append(argv)
        if argv[1:] == ("ps", "-aq"):
            return DockerResult(0, "\n".join(fixture.get("containers", {})) + "\n", "")
        if argv[1] == "inspect":
            return DockerResult(
                0,
                json.dumps([fixture["containers"][name] for name in argv[2:]]),
                "",
            )
        if argv[1:3] == ("network", "inspect"):
            network = next(
                value
                for value in fixture["networks"].values()
                if value["name"] == argv[3]
            )
            return DockerResult(0, json.dumps([{"Name": network["name"], "Labels": network["labels"]}]), "")
        if argv[1:3] == ("volume", "ls"):
            return DockerResult(0, fixture["volume"]["Name"] + "\n", "")
        if argv[1:3] == ("volume", "inspect"):
            return DockerResult(0, json.dumps([fixture["volume"]]), "")
        if argv[1] == "compose" and argv[-2:] == ("up", "-d"):
            return DockerResult(0, "ok", "")
        raise AssertionError(argv)

    def adapter_runner(argv, timeout, execution_id):
        adapter_calls.append(argv)
        output = Path(argv[argv.index("--health-output") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps({**fixture["report"], "fresh_observation": True}),
            encoding="utf-8",
        )
        return DockerResult(0, "healthy", "")

    runtime = ChainRuntime(
        installation_id="install",
        state_dir=state,
        host_state_dir="/srv/dwarf",
        runner=runner,
        adapter_runner=adapter_runner,
    )
    current_deployment = runtime.prepare_topology(topology)
    assert runtime.prepare_topology(
        {**topology, "catalog_revision": desired["catalog_revision"]}
    )["adapter"] == "runtime_amaru_control_substrate.py"
    assert current_deployment["adapter"] == "runtime_amaru_control_substrate.py"
    retained_deployment = json.loads(json.dumps(current_deployment))
    # Retained default deployments written before operator registrations did
    # not serialize empty operator fields.  Exercise that real migration
    # boundary rather than generating both sides with the current schema.
    retained_deployment["substrate"].pop("operator_registration_id", None)
    retained_deployment["substrate"].pop("operator_overlay_revision", None)
    retained_deployment["substrate"]["catalog_snapshot"].pop(
        "operator_registration_id", None
    )
    retained_deployment["substrate"]["catalog_snapshot"].pop(
        "operator_overlay_revision", None
    )
    current_artifacts = [
        artifact
        for release in current_deployment["substrate"]["catalog_snapshot"]["selected_releases"]
        for artifact in release.get("artifacts") or []
        if artifact.get("kind") == "oci"
    ]
    retained_artifacts = [
        artifact
        for release in retained_deployment["substrate"]["catalog_snapshot"]["selected_releases"]
        for artifact in release.get("artifacts") or []
        if artifact.get("kind") == "oci"
    ]
    enriched = False
    for current_artifact, retained_artifact in zip(current_artifacts, retained_artifacts):
        if current_artifact.get("public") is True and current_artifact.get("platforms"):
            retained_artifact.pop("public", None)
            retained_artifact.pop("platforms", None)
            enriched = True
    assert enriched is True

    current_root = runtime._runtime_parent(profile.id) / "current"
    retained_config, _compose, report, containers, networks, volume = (
        _retain_verified_amaru_control_runtime(runtime, retained_deployment, current_root)
    )
    fixture.update(
        report=report,
        containers={f"container-{index}": value for index, value in enumerate(containers.values())},
        networks=networks,
        volume=volume,
    )

    token = "controller-token-" + "x" * 32
    controller = ControllerService(token, state, runtime, "revision", "install")

    def request_controller(operation, parameters, timeout, idempotency_key=None):
        body = {
            "schema_version": "v1",
            "request_id": str(uuid.uuid4()),
            "operation": operation,
            "timeout_seconds": timeout,
            **parameters,
        }
        if idempotency_key:
            body["idempotency_key"] = idempotency_key
        status, payload = controller.dispatch(
            "POST",
            "/v1/requests",
            {"Authorization": f"Bearer {token}"},
            json.dumps(body).encode(),
        )
        assert status == 200, (payload, runtime.errors)
        return payload

    actions = TopologyActionService(request_controller, tmp_path / "actions")
    plan = resolve_run_plan(
        RunPlanRequest.from_mapping({"scenario_id": "amaru-measurement-e2e-stock"})
    )
    plan_digest = digest_run_plan(plan)
    retained_desired = desired_topology_from_substrate(
        profile.id, retained_config["substrate"]
    )
    checked_at = "2026-10-11T12:00:00Z"
    managed = [
        {
            "node_id": node["id"],
            "topology_id": profile.id,
            "implementation": node["implementation"],
            "version": node["version"],
            "image": node["image"],
            "role": node["role"],
            "supporting": node["supporting"],
            "state": "running",
        }
        for node in retained_desired["nodes"]
    ]
    initial = build_topology_snapshot(
        desired=retained_desired,
        persisted=retained_config,
        managed=managed,
        unmanaged=[],
        health={
            "state": "unhealthy",
            "reason_code": "fresh_chain_progress_failed",
            "checked_at": checked_at,
        },
        generated_at=checked_at,
    )
    assert initial["actions"]["repair"]["allowed"] is True
    current_desired = dashboard._current_run_plan_desired_topology(plan)
    healthy = build_topology_snapshot(
        desired=retained_desired,
        persisted=retained_config,
        managed=managed,
        unmanaged=[],
        health={
            "state": "healthy",
            "reason_code": "node_and_chain_healthy",
            "checked_at": checked_at,
        },
        generated_at=checked_at,
    )
    events = list(
        dashboard._prepare_run_plan(
            plan,
            plan_digest,
            action_service=actions,
            snapshot_loader=lambda _profile_id: initial,
            health_requester=lambda *, profile_id: {"state": "checking"},
            health_waiter=lambda *, timeout, profile_id: healthy,
            retry_sleeper=lambda _seconds: None,
        )
    )

    assert any(event.get("topology_action") == "repair" for event in events)
    assert events[-1]["stage"] == "ready"
    assert any(call[1:3] == ("volume", "inspect") for call in docker_calls)
    assert {
        call[3]
        for call in docker_calls
        if call[1:3] == ("network", "inspect")
    } == {network["name"] for network in networks.values()}
    assert any(call[1] == "compose" and call[-2:] == ("up", "-d") for call in docker_calls)
    assert len(adapter_calls) == 1 and "--verify-retained" in adapter_calls[0]


def test_profile_r_registered_identity_chooses_redeploy_before_native_repair(
    monkeypatch, tmp_path
):
    """A stock-to-registration transition must not enter the repair guard."""
    from profile_manager import dashboard
    from profile_manager.profiles import desired_topology_from_substrate
    from profile_manager.run_plan import RunPlanRequest
    from profile_manager.topology_snapshot import build_topology_snapshot

    profile = find_profile("profile-r-amaru-measurement-stock-control")
    stock_preview = build_deployment_version_preview(asdict(profile))
    stock_desired = desired_topology_for_profile(profile, stock_preview)
    stock_topology = {
        "topology_id": profile.id,
        "network_magic": stock_desired["network_magic"],
        "catalog_revision": stock_desired["catalog_revision"],
        "nodes": [
            {key: node[key] for key in ("id", "implementation", "version", "image")}
            for node in stock_desired["nodes"]
        ],
    }
    stock_images = [node["image"] for node in stock_topology["nodes"]]
    monkeypatch.setenv("DWARF_GOLDEN_IMAGES", ",".join(stock_images))
    monkeypatch.setenv("DWARF_HOST_OS", "linux")
    monkeypatch.setenv("DWARF_HOST_ARCH", "amd64")
    state = tmp_path / "controller-state"
    fixture = {}
    registered_interface_known = [True]

    def runner(argv, timeout, execution_id):
        if argv[1:] == ("ps", "-aq"):
            return DockerResult(0, "\n".join(fixture.get("containers", {})) + "\n", "")
        if argv[1] == "inspect":
            return DockerResult(
                0,
                json.dumps([fixture["containers"][name] for name in argv[2:]]),
                "",
            )
        if argv[1:3] == ("network", "inspect"):
            network = next(
                value
                for value in fixture["networks"].values()
                if value["name"] == argv[3]
            )
            return DockerResult(
                0,
                json.dumps([{"Name": network["name"], "Labels": network["labels"]}]),
                "",
            )
        if argv[1:3] == ("volume", "ls"):
            return DockerResult(0, fixture["volume"]["Name"] + "\n", "")
        if argv[1:3] == ("volume", "inspect"):
            return DockerResult(0, json.dumps([fixture["volume"]]), "")
        if argv[1:3] == ("image", "inspect"):
            reference = argv[3]
            if reference == fixture.get("registration_config_id"):
                return DockerResult(
                    0,
                    json.dumps(
                        [
                            {
                                "Id": reference,
                                "RepoDigests": [fixture["registration_reference"]],
                                "Os": "linux",
                                "Architecture": "amd64",
                                "Config": {
                                    "Entrypoint": (
                                        ["/usr/local/bin/amaru"]
                                        if registered_interface_known[0]
                                        else ["/opt/custom/start"]
                                    ),
                                    "Cmd": ["run"],
                                },
                            }
                        ]
                    ),
                    "",
                )
            return DockerResult(0, "[]", "")
        raise AssertionError(argv)

    runtime = DockerRuntime(
        installation_id="install",
        state_dir=state,
        host_state_dir="/srv/dwarf",
        runner=runner,
    )
    stock_deployment = runtime.prepare_topology(stock_topology)
    current_root = runtime._runtime_parent(profile.id) / "current"
    retained_config, _compose, report, containers, networks, volume = (
        _retain_verified_amaru_control_runtime(runtime, stock_deployment, current_root)
    )
    fixture.update(
        containers={
            f"container-{index}": value
            for index, value in enumerate(containers.values())
        },
        networks=networks,
        volume=volume,
    )
    registration_config_id = report["identity"]["services"]["amaru-relay-1"][
        "artifact_image_id"
    ]
    stock_registration_reference = next(
        node["image"]
        for node in stock_topology["nodes"]
        if node["implementation"] == "amaru"
    )
    tagged_repository, manifest_digest = stock_registration_reference.rsplit("@", 1)
    registration_reference = tagged_repository.rsplit(":", 1)[0] + "@" + manifest_digest
    registration = {
        "registration_id": "registration-profile-r-native-transition",
        "implementation": "amaru",
        "version": "10.11.20260912-local-test",
        "image_reference": registration_reference,
        "manifest_digest": manifest_digest,
        "config_image_id": registration_config_id,
        "platform": "linux/amd64",
        "runtime_interface": "extracted-binary",
        "inspected_at": "2026-10-11T20:00:00Z",
        "provenance": {
            "source_revision": None,
            "public": None,
            "qualification": "unverified",
        },
    }
    fixture.update(
        registration_config_id=registration_config_id,
        registration_reference=registration_reference,
    )
    overlay_root = tmp_path / "dashboard-state"
    ledger = OperatorVersionDecisionLedger(overlay_root)
    ledger.append_registration(registration, expected_revision=ledger.read()["revision"])
    monkeypatch.setenv("ADA2_DWARF_VERSION_CATALOG_STATE_DIR", str(overlay_root))

    plan = resolve_run_plan(
        RunPlanRequest.from_mapping(
            {
                "scenario_id": "amaru-measurement-e2e-stock",
                "operator_registration_id": registration["registration_id"],
                "acknowledge_unknown_versions": True,
                "measurement_profile": "none",
            }
        )
    )
    plan_digest = digest_run_plan(plan)
    retained_desired = desired_topology_from_substrate(
        profile.id, retained_config["substrate"]
    )
    managed = [
        {
            "node_id": node["id"],
            "topology_id": profile.id,
            "implementation": node["implementation"],
            "version": node["version"],
            "image": node["image"],
            "role": node["role"],
            "supporting": node["supporting"],
            "state": "running",
        }
        for node in retained_desired["nodes"]
    ]
    checked_at = "2026-10-11T20:01:00Z"
    snapshot = build_topology_snapshot(
        desired=retained_desired,
        persisted=retained_config,
        managed=managed,
        unmanaged=[],
        health={
            "state": "unhealthy",
            "reason_code": "fresh_chain_progress_failed",
            "checked_at": checked_at,
        },
        generated_at=checked_at,
    )
    assert snapshot["actions"]["repair"]["allowed"] is True
    assert snapshot["actions"]["redeploy"]["allowed"] is True

    token = "controller-token-" + "x" * 32
    controller = ControllerService(token, state, runtime, "revision", "install")

    def request_controller(operation, parameters, timeout, idempotency_key=None):
        body = {
            "schema_version": "v1",
            "request_id": str(uuid.uuid4()),
            "operation": operation,
            "timeout_seconds": timeout,
            **parameters,
        }
        if idempotency_key:
            body["idempotency_key"] = idempotency_key
        status, payload = controller.dispatch(
            "POST",
            "/v1/requests",
            {"Authorization": f"Bearer {token}"},
            json.dumps(body).encode(),
        )
        assert status == 200, payload
        return payload

    actions = TopologyActionService(request_controller, tmp_path / "actions")
    events = list(
        dashboard._prepare_run_plan(
            plan,
            plan_digest,
            action_service=actions,
            snapshot_loader=lambda _profile_id: snapshot,
        )
    )

    assert events[-1]["stage"] == "blocked"
    assert events[-1]["action"] == "redeploy"
    assert events[-1]["confirmation_required"] is True
    retained_after = json.loads(
        (current_root / "versioned-deployment.json").read_text(encoding="utf-8")
    )
    assert retained_after == retained_config

    plan_id = events[-1]["topology_plan_id"]
    controller_plan = json.loads(
        (state / "controller" / "plans" / f"{plan_id}.json").read_text(
            encoding="utf-8"
        )
    )["plan"]
    mutation_calls = []
    monkeypatch.setattr(
        runtime,
        "_compose",
        lambda *_args: mutation_calls.append("compose")
        or DockerResult(0, "", ""),
    )
    monkeypatch.setattr(
        runtime,
        "_quiesce_owned_containers",
        lambda **_kwargs: mutation_calls.append("quiesce"),
    )
    monkeypatch.setattr(
        runtime,
        "_snapshot_owned_volumes",
        lambda **_kwargs: mutation_calls.append("snapshot"),
    )
    monkeypatch.setattr(
        runtime,
        "_invoke_deployment",
        lambda *_args, **_kwargs: mutation_calls.append("invoke"),
    )
    registered_interface_known[0] = False

    with pytest.raises(
        DockerRuntimeError, match="identity or platform differs"
    ):
        runtime.apply(
            controller_plan,
            "execution-interface-changed-after-plan",
            threading.Event(),
        )

    assert mutation_calls == []


def test_apply_invokes_proven_adapter_with_complete_server_owned_config(tmp_path):
    docker_calls = []
    adapter_calls = []

    def docker_runner(argv, timeout, execution_id):
        docker_calls.append(argv)
        if argv[1:] == ("ps", "-aq"):
            return DockerResult(0, "", "")
        if argv[1:3] == ("image", "inspect"):
            return DockerResult(0, "{}", "")
        return DockerResult(1, "", "not found")

    def adapter_runner(argv, timeout, execution_id):
        adapter_calls.append(argv)
        _emit_healthy_adapter_report(argv)
        return DockerResult(0, "healthy=true nodes=1", "")

    result = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path,
        runner=docker_runner,
        adapter_runner=adapter_runner,
    ).apply(_plan(), "execution-adapter-1", threading.Event())

    assert result["status"] == "applied"
    assert result["semantic_health"] is True
    assert len(adapter_calls) == 1
    assert adapter_calls[0][-2] == "--config"
    assert adapter_calls[0][1].endswith("runtime_compose_substrate.py")
    assert not any(argv[1:3] == ("run", "--detach") for argv in docker_calls)


def test_adapter_preflight_failure_parsing_is_structured_and_allowlisted(tmp_path):
    runtime = DockerRuntime(installation_id="install", state_dir=tmp_path)
    result = DockerResult(
        2,
        json.dumps(
            {
                "passed": False,
                "error": "must not be surfaced",
                "failure": {
                    "code": "compose_project_not_fresh",
                    "stage": "preflight",
                },
            }
        ),
        "stderr password=must-not-be-surfaced",
    )

    failure = runtime._structured_adapter_failure(result)

    assert isinstance(failure, DockerRuntimeError)
    assert str(failure) == "Amaru deployment preflight found retained project resources"
    assert failure.failure_code == "compose_project_not_fresh"
    assert failure.failure_stage == "adapter_preflight"
    assert runtime._structured_adapter_failure(
        DockerResult(
            2,
            json.dumps(
                {
                    "failure": {
                        "code": "unreviewed_failure",
                        "stage": "preflight",
                    }
                }
            ),
            "stderr token=must-not-be-surfaced",
        )
    ) is None


@pytest.mark.parametrize(
    ("code", "stage", "message"),
    [
        (
            "runtime_assets_exist",
            "preflight",
            "Amaru deployment preflight found retained runtime assets",
        ),
        (
            "required_runtime_asset_missing",
            "asset_staging",
            "Amaru deployment runtime assets are unavailable",
        ),
        (
            "runtime_asset_permission_denied",
            "asset_staging",
            "Amaru deployment runtime assets are not writable",
        ),
    ],
)
def test_adapter_early_failure_codes_map_to_fixed_messages(
    tmp_path, code, stage, message
):
    runtime = DockerRuntime(installation_id="install", state_dir=tmp_path)
    failure = runtime._structured_adapter_failure(
        DockerResult(
            2,
            json.dumps(
                {
                    "passed": False,
                    "error": "must not be surfaced",
                    "failure": {"code": code, "stage": stage},
                }
            ),
            "password=must-not-be-surfaced",
        )
    )

    assert isinstance(failure, DockerRuntimeError)
    assert str(failure) == message
    assert failure.failure_code == code
    assert failure.failure_stage == "adapter_" + stage


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        (
            json.dumps(
                {
                    "passed": False,
                    "failure": {
                        "code": "compose_project_not_fresh",
                        "stage": "preflight",
                    },
                }
            ),
            "Amaru deployment preflight found retained project resources",
        ),
        ("not-json", "deployment adapter did not emit a semantic report"),
        (
            json.dumps(
                {
                    "failure": {
                        "code": "unknown",
                        "stage": "preflight",
                    }
                }
            ),
            "deployment adapter did not emit a semantic report",
        ),
    ],
)
def test_invoke_deployment_preserves_only_reviewed_early_json_without_report(
    tmp_path, monkeypatch, stdout, expected
):
    runtime = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path,
        adapter_runner=lambda *_args, **_kwargs: DockerResult(
            2, stdout, "Bearer must-not-be-surfaced"
        ),
    )
    config = {
        "adapter": "runtime_amaru_control_substrate.py",
        "runtime_root": str(tmp_path / "current"),
        "substrate": {"nodes": []},
    }
    monkeypatch.setattr(runtime, "_deployment_config", lambda *_args: config)
    runtime_root = tmp_path / "current"
    runtime_root.mkdir()

    with pytest.raises(DockerRuntimeError, match=expected):
        runtime._invoke_deployment(
            {"adapter": "runtime_amaru_control_substrate.py"},
            runtime_root,
            "execution-early-json",
        )


def test_generic_adapter_cannot_claim_amaru_structured_failure(tmp_path, monkeypatch):
    stdout = json.dumps(
        {
            "passed": False,
            "failure": {
                "code": "compose_project_not_fresh",
                "stage": "preflight",
            },
        }
    )
    runtime = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path,
        adapter_runner=lambda *_args, **_kwargs: DockerResult(2, stdout, ""),
    )
    config = {
        "adapter": "runtime_compose_substrate.py",
        "runtime_root": str(tmp_path / "current"),
        "substrate": {"nodes": []},
    }
    monkeypatch.setattr(runtime, "_deployment_config", lambda *_args: config)
    runtime_root = tmp_path / "current"
    runtime_root.mkdir()

    with pytest.raises(
        DockerRuntimeError,
        match="deployment adapter did not emit a semantic report",
    ):
        runtime._invoke_deployment(
            {"adapter": "runtime_compose_substrate.py"},
            runtime_root,
            "execution-generic-json",
        )


def test_health_fails_closed_when_no_golden_images_are_configured(monkeypatch):
    monkeypatch.delenv("DWARF_GOLDEN_IMAGES", raising=False)

    def runner(argv, timeout, execution_id):
        assert argv[1:] in {
            ("version", "--format", "{{json .Server}}"),
            ("compose", "version", "--format", "json"),
        }
        return DockerResult(0, "{}", "")

    health = DockerRuntime(installation_id="install", runner=runner).health()
    assert health["docker"] == "ok"
    assert health["golden_images"] == "error"
    assert health["golden_images_configured"] == 0


def test_health_fails_closed_when_docker_compose_is_unavailable(monkeypatch):
    monkeypatch.setenv("DWARF_GOLDEN_IMAGES", "example.invalid/node@" + DIGEST)

    def runner(argv, timeout, execution_id):
        if argv[1:] == ("compose", "version", "--format", "json"):
            return DockerResult(1, "", "compose unavailable")
        return DockerResult(0, "{}", "")

    health = DockerRuntime(installation_id="install", runner=runner).health()

    assert health["docker"] == "ok"
    assert health["compose"] == "error"


def test_controller_image_packages_checksum_verified_cardano_support_tools():
    """The retained adapter must not depend on binaries from the Docker host."""

    repository_root = Path(__file__).resolve().parents[1]
    dockerfile = (
        repository_root / "infrastructure/docker/dwarf-controller.Dockerfile"
    ).read_text(encoding="utf-8")
    build_script = (
        repository_root / "delivery/scripts/build-image.sh"
    ).read_text(encoding="utf-8")

    assert "CARDANO_SUPPORT_VERSION=11.1.2" in dockerfile
    assert "CARDANO_SUPPORT_AMD64_SHA256=" in dockerfile
    assert "CARDANO_SUPPORT_ARM64_SHA256=" in dockerfile
    assert "hashlib.sha256" in dockerfile
    assert "COPY --from=cardano-support /out/ /usr/local/bin/" in dockerfile
    for binary in ("cardano-testnet", "cardano-cli", "cardano-node"):
        assert binary in dockerfile
        assert f"command -v {binary}" in build_script
    assert "cardano-testnet version" in build_script
    assert "cardano-cli --version" in build_script
    assert "cardano-node --version" in build_script


def test_apply_refuses_digest_pinned_image_outside_golden_allowlist(monkeypatch, tmp_path):
    approved = "ghcr.io/pragma-org/amaru:approved@" + DIGEST
    unapproved = "ghcr.io/pragma-org/amaru@sha256:" + "b" * 64
    monkeypatch.setenv("DWARF_GOLDEN_IMAGES", approved)
    calls = []

    def runner(argv, timeout, execution_id):
        calls.append(argv)
        if argv[1:] == ("ps", "-aq"):
            return DockerResult(0, "", "")
        if argv[1:3] == ("image", "inspect"):
            return DockerResult(0, "{}", "")
        return DockerResult(1, "", "not found")

    with pytest.raises(DockerRuntimeError, match="approved golden image"):
        DockerRuntime(
            installation_id="install",
            state_dir=tmp_path,
            runner=runner,
            adapter_runner=lambda *_: (_ for _ in ()).throw(AssertionError("adapter called")),
        ).apply(
            _plan(nodes=[{
                "id": "amaru-1",
                "implementation": "amaru",
                "version": "0.3.0",
                "image": unapproved,
            }]),
            "execution-1",
            threading.Event(),
        )

    assert not any(argv[1] == "pull" for argv in calls)


def test_golden_allowlist_compares_repository_and_digest_not_optional_tag(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "DWARF_GOLDEN_IMAGES",
        "ghcr.io/pragma-org/amaru:approved@" + DIGEST,
    )
    calls = []

    def runner(argv, timeout, execution_id):
        calls.append(argv)
        if argv[1:] == ("ps", "-aq"):
            return DockerResult(0, "", "")
        if argv[1:3] == ("image", "inspect"):
            return DockerResult(0, "{}", "")
        return DockerResult(1, "", "not found")

    def adapter_runner(argv, timeout, execution_id):
        _emit_healthy_adapter_report(argv)
        return DockerResult(0, "healthy=true", "")

    result = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path,
        runner=runner,
        adapter_runner=adapter_runner,
    ).apply(
        _plan(), "execution-1", threading.Event()
    )

    assert result["status"] == "applied"


def test_adapter_apply_pulls_missing_approved_image_then_rechecks_digest(tmp_path):
    inspections = 0
    calls = []

    def runner(argv, timeout, execution_id):
        nonlocal inspections
        calls.append(argv)
        if argv[1:] == ("ps", "-aq"):
            return DockerResult(0, "", "")
        if argv[1:3] == ("network", "inspect"):
            return DockerResult(1, "", "not found")
        if argv[1:3] == ("image", "inspect"):
            inspections += 1
            return DockerResult(0 if inspections > 1 else 1, "{}", "")
        if argv[1] == "pull":
            return DockerResult(0, "pulled", "")
        raise AssertionError(argv)

    def adapter(argv, timeout, execution_id):
        _emit_healthy_adapter_report(argv)
        return DockerResult(0, "healthy=true", "")

    result = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path,
        runner=runner,
        adapter_runner=adapter,
    ).apply(_plan(), "execution-pull", threading.Event())

    assert result["status"] == "applied"
    assert ("docker", "pull", IMAGE) in calls
    assert inspections == 2


def test_prepare_topology_resolves_complete_server_owned_deployment(monkeypatch, tmp_path):
    profile = find_profile("profile-e-haskell-preview-proof")
    preview = build_deployment_version_preview(asdict(profile))
    desired = desired_topology_for_profile(profile, preview)
    topology = {
        "topology_id": desired["topology_id"],
        "network_magic": desired["network_magic"],
        "nodes": [
            {key: node[key] for key in ("id", "implementation", "version", "image")}
            for node in desired["nodes"]
        ],
    }
    monkeypatch.setenv(
        "DWARF_GOLDEN_IMAGES",
        ",".join(node["image"] for node in topology["nodes"]),
    )

    runtime = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path,
        host_state_dir="/srv/dwarf",
        runner=lambda *_: None,
    )
    deployment = runtime.prepare_topology(topology)

    assert deployment["adapter"] == "runtime_compose_substrate.py"
    assert deployment["compose_project"].startswith("dwarf-profile-install-")
    assert deployment["substrate"]["network"] == "preview"
    assert deployment["substrate"]["topology"] == {"edges": []}
    assert deployment["substrate"]["controller_ownership"] == {
        "installation_id": "install",
        "topology_id": "profile-e-haskell-preview-proof",
    }
    assert deployment["substrate"]["nodes"][0]["source_revision"]

    # The catalog carries a source-host path for this real public profile, but
    # an installed controller correctly clears that unavailable hint and lets
    # the adapter fetch public assets.  Both sides must still derive the same
    # immutable topology identity.
    assert profile.config_source_dir
    assert deployment["substrate"]["config_source_dir"] is None
    retained_desired = _verified_retained_desired(
        profile_id=profile.id,
        persisted={**deployment, "profile_id": profile.id},
        observed_nodes=[
            {
                "node_id": node["id"],
                "implementation": node["impl"],
                "version": node["version"],
                "image": node["image"],
            }
            for node in deployment["substrate"]["nodes"]
        ],
    )
    assert retained_desired is not None
    assert retained_desired["plan_sha256"] == desired["plan_sha256"]


def test_prepare_topology_preserves_dashboard_resolver_catalog_revision(
    monkeypatch, tmp_path
):
    profile = find_profile("profile-e-haskell-preview-proof")
    preview = build_deployment_version_preview(asdict(profile))
    desired = desired_topology_for_profile(profile, preview)
    caller_catalog_revision = preview["catalog_revision"]
    topology = {
        "topology_id": desired["topology_id"],
        "network_magic": desired["network_magic"],
        "catalog_revision": caller_catalog_revision,
        "nodes": [
            {key: node[key] for key in ("id", "implementation", "version", "image")}
            for node in desired["nodes"]
        ],
    }
    monkeypatch.setenv(
        "DWARF_GOLDEN_IMAGES",
        ",".join(node["image"] for node in topology["nodes"]),
    )

    deployment = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path,
        host_state_dir="/srv/dwarf",
        runner=lambda *_: None,
    ).prepare_topology(topology)

    assert deployment["substrate"]["catalog_revision"] == caller_catalog_revision


def test_prepare_topology_uses_exact_operator_registered_config_image(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("DWARF_HOST_OS", "linux")
    monkeypatch.setenv("DWARF_HOST_ARCH", "amd64")
    overlay_root = tmp_path / "dashboard-state"
    registration = {
        "registration_id": "local-amaru-config",
        "implementation": "amaru",
        "version": "10.12.0-local",
        "image_reference": "ghcr.io/pragma-org/amaru@sha256:" + "a" * 64,
        "manifest_digest": "sha256:" + "a" * 64,
        "config_image_id": "sha256:" + "b" * 64,
        "platform": "linux/amd64",
        "runtime_interface": "extracted-binary",
        "inspected_at": "2026-10-11T12:00:00Z",
        "provenance": {
            "source_revision": None,
            "public": None,
            "qualification": "unverified",
        },
    }
    ledger = OperatorVersionDecisionLedger(overlay_root)
    ledger.append_registration(registration, expected_revision=ledger.read()["revision"])
    monkeypatch.setenv("ADA2_DWARF_VERSION_CATALOG_STATE_DIR", str(overlay_root))
    profile = find_profile("profile-r-amaru-measurement-stock-control")
    preview = build_deployment_version_preview(
        asdict(profile), operator_registration_id=registration["registration_id"]
    )
    desired = desired_topology_for_profile(profile, preview)
    topology = {
        "topology_id": desired["topology_id"],
        "network_magic": desired["network_magic"],
        "catalog_revision": desired["catalog_revision"],
        "operator_registration_id": desired["operator_registration_id"],
        "operator_overlay_revision": desired["operator_overlay_revision"],
        "nodes": [
            {key: node[key] for key in ("id", "implementation", "version", "image")}
            for node in desired["nodes"]
        ],
    }
    support_images = [
        node["image"] for node in desired["nodes"]
        if node["implementation"] != "amaru"
    ]
    monkeypatch.setenv("DWARF_GOLDEN_IMAGES", ",".join(support_images))

    interface_known = [True]

    def runner(argv, timeout, execution_id):
        assert argv[1:3] == ("image", "inspect")
        if argv[3] != registration["config_image_id"]:
            assert argv[3] in support_images
            return DockerResult(0, "[]", "")
        return DockerResult(0, json.dumps([{
            "Id": registration["config_image_id"],
            "RepoDigests": [registration["image_reference"]],
            "Os": "linux",
            "Architecture": "amd64",
            "Config": {
                "Entrypoint": (
                    ["/usr/local/bin/amaru"]
                    if interface_known[0]
                    else ["/opt/custom/start"]
                ),
                "Cmd": ["run"],
            },
        }]), "")

    def adapter(argv, timeout, execution_id):
        _retain_verified_amaru_control_runtime(
            runtime, deployment, runtime_root
        )
        return DockerResult(0, "healthy", "")

    runtime = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path / "controller-state",
        host_state_dir="/srv/dwarf",
        runner=runner,
        adapter_runner=adapter,
    )
    deployment = runtime.prepare_topology(topology)

    selected = [
        node for node in deployment["substrate"]["nodes"]
        if node["impl"] == "amaru"
    ]
    assert selected
    assert {node["image"] for node in selected} == {registration["config_image_id"]}
    assert all(node["source_revision"] is None for node in selected)
    assert all(node["operator_registration"] == registration for node in selected)
    assert all(
        node["amaru_runtime_interface"] == "extracted-binary"
        for node in selected
    )

    adapter_root = tmp_path / "adapter-stage"
    adapter_config = runtime._deployment_config(deployment, adapter_root)
    validate_runtime_request(adapter_config)
    assert adapter_config["amaru_runtime_interface"] == "extracted-binary"
    staged = _stage_runtime_assets(
        adapter_config, base_package=AMARU_CONTROL_BASE_PACKAGE
    )
    assert staged["amaru_image"] == registration["config_image_id"]
    assert Path(staged["runtime_asset_root"]).is_dir()
    cardano = {"image": "old-cardano"}
    relay = {
        "image": "old-amaru-wrapper",
        "entrypoint": "amaru-relay-bootstrap",
        "environment": {},
    }
    baseline = {
        "services": {
            "configurator": {"image": "configurator"},
            "tracer": {"image": "tracer"},
            "tracer-sidecar": {"image": "tracer-sidecar"},
            "log-tailer": {"image": "log-tailer"},
            "sidecar": {"image": "sidecar"},
            "p1": dict(cardano),
            "p2": dict(cardano),
            "p3": dict(cardano),
            "relay1": dict(cardano),
            "relay2": dict(cardano),
            "amaru-consumer": dict(cardano),
            "amaru-consumer-seed": {"image": "bootstrap"},
            "amaru-relay-1": dict(relay),
            "amaru-relay-2": dict(relay),
        },
        "volumes": {},
        "networks": {"default": {}, "amaru-consumer-net": {}},
    }
    model = prepare_runtime_model(baseline, staged)
    for service_name in ("amaru-relay-1", "amaru-relay-2"):
        service = model["services"][service_name]
        assert service["image"] == CONTROL_AMARU_IMAGE
        assert service["entrypoint"] == "amaru-relay-bootstrap"
        assert service["environment"]["AMARU_BIN"] == "/target/amaru"
        assert service["depends_on"]["amaru-target-extract"] == {
            "condition": "service_completed_successfully",
            "required": True,
        }
    extractor = model["services"]["amaru-target-extract"]
    assert extractor["image"] == registration["config_image_id"]
    assert extractor["entrypoint"] == ["/bin/sh", "-ec"]

    runtime_root = tmp_path / "invoke"
    runtime_root.mkdir()
    invoked = runtime._invoke_deployment(
        deployment, runtime_root, "execution-registered"
    )
    assert invoked["passed"] is True
    assert invoked["retained"] is True

    interface_known[0] = False
    with pytest.raises(
        DockerRuntimeError, match="identity or platform differs"
    ):
        runtime._invoke_deployment(
            deployment,
            tmp_path / "changed-interface",
            "execution-changed-interface",
        )
    interface_known[0] = True

    current = ledger.read()
    ledger.append_registration(
        {
            **registration,
            "registration_id": "unrelated-later-registration",
            "version": "10.12.1-local",
        },
        expected_revision=current["revision"],
    )
    with pytest.raises(
        DockerRuntimeError, match="operator registration state differs"
    ):
        runtime._invoke_deployment(
            deployment,
            tmp_path / "stale-overlay",
            "execution-stale",
        )
    with pytest.raises(
        DockerRuntimeError, match="catalog-resolved topology"
    ):
        DockerRuntime(
            installation_id="install",
            state_dir=tmp_path / "controller-state-2",
            host_state_dir="/srv/dwarf",
            runner=runner,
        ).prepare_topology(topology)


def test_operator_registered_stock_adapter_rejects_untrusted_config_chain(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("DWARF_HOST_OS", "linux")
    monkeypatch.setenv("DWARF_HOST_ARCH", "amd64")
    overlay_root = tmp_path / "dashboard-state"
    registration = {
        "registration_id": "local-amaru-config",
        "implementation": "amaru",
        "version": "10.12.0-local",
        "image_reference": "local/amaru@sha256:" + "a" * 64,
        "manifest_digest": "sha256:" + "a" * 64,
        "config_image_id": "sha256:" + "b" * 64,
        "platform": "linux/amd64",
        "runtime_interface": "extracted-binary",
        "inspected_at": "2026-10-11T12:00:00Z",
        "provenance": {
            "source_revision": None,
            "public": None,
            "qualification": "unverified",
        },
    }
    ledger = OperatorVersionDecisionLedger(overlay_root)
    ledger.append_registration(registration, expected_revision=ledger.read()["revision"])
    monkeypatch.setenv("ADA2_DWARF_VERSION_CATALOG_STATE_DIR", str(overlay_root))
    profile = find_profile("profile-r-amaru-measurement-stock-control")
    preview = build_deployment_version_preview(
        asdict(profile), operator_registration_id=registration["registration_id"]
    )
    desired = desired_topology_for_profile(profile, preview)
    topology = {
        "topology_id": desired["topology_id"],
        "network_magic": desired["network_magic"],
        "catalog_revision": desired["catalog_revision"],
        "operator_registration_id": desired["operator_registration_id"],
        "operator_overlay_revision": desired["operator_overlay_revision"],
        "nodes": [
            {key: node[key] for key in ("id", "implementation", "version", "image")}
            for node in desired["nodes"]
        ],
    }
    support_images = [
        node["image"]
        for node in desired["nodes"]
        if node["implementation"] != "amaru"
    ]
    monkeypatch.setenv("DWARF_GOLDEN_IMAGES", ",".join(support_images))

    def runner(argv, timeout, execution_id):
        if argv[1:3] != ("image", "inspect"):
            return DockerResult(0, "[]", "")
        if argv[3] != registration["config_image_id"]:
            return DockerResult(0, "[]", "")
        return DockerResult(
            0,
            json.dumps(
                [
                    {
                        "Id": registration["config_image_id"],
                        "RepoDigests": [registration["image_reference"]],
                        "Os": "linux",
                        "Architecture": "amd64",
                            "Config": {
                                "Entrypoint": ["/usr/local/bin/amaru"],
                                "Cmd": ["run"],
                            },
                    }
                ]
            ),
            "",
        )

    runtime = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path / "controller-state",
        host_state_dir="/srv/dwarf",
        runner=runner,
    )
    deployment = runtime.prepare_topology(topology)
    config = runtime._deployment_config(deployment, tmp_path / "adapter")

    wrong_config = json.loads(json.dumps(config))
    wrong_config["amaru_image"] = "sha256:" + "c" * 64
    with pytest.raises(RuntimeControlError):
        validate_runtime_request(wrong_config)

    unregistered = json.loads(json.dumps(config))
    unregistered["substrate"].pop("operator_registration_id")
    unregistered["substrate"].pop("operator_overlay_revision")
    for node in unregistered["substrate"]["nodes"]:
        node.pop("operator_registration", None)
    unregistered["operator_registration_verified"] = True
    with pytest.raises(RuntimeControlError):
        validate_runtime_request(unregistered)

    changed_embedded = json.loads(json.dumps(config))
    selected = next(
        node
        for node in changed_embedded["substrate"]["nodes"]
        if isinstance(node.get("operator_registration"), dict)
    )
    selected["operator_registration"]["config_image_id"] = "sha256:" + "c" * 64
    with pytest.raises(RuntimeControlError):
        validate_runtime_request(changed_embedded)

    monkeypatch.setenv("DWARF_HOST_ARCH", "arm64")
    with pytest.raises(RuntimeControlError):
        validate_runtime_request(config)
    monkeypatch.setenv("DWARF_HOST_ARCH", "amd64")

    current = ledger.read()
    ledger.append_registration(
        {
            **registration,
            "registration_id": "later-registration",
            "version": "10.12.1-local",
        },
        expected_revision=current["revision"],
    )
    with pytest.raises(RuntimeControlError):
        validate_runtime_request(config)


def test_operator_registered_manifest_requires_daemon_repo_digest(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("DWARF_HOST_OS", "linux")
    monkeypatch.setenv("DWARF_HOST_ARCH", "amd64")
    overlay_root = tmp_path / "dashboard-state"
    registration = {
        "registration_id": "local-amaru-config",
        "implementation": "amaru",
        "version": "10.12.0-local",
        "image_reference": "local/amaru@sha256:" + "a" * 64,
        "manifest_digest": "sha256:" + "a" * 64,
        "config_image_id": "sha256:" + "b" * 64,
        "platform": "linux/amd64",
        "runtime_interface": "extracted-binary",
        "inspected_at": "2026-10-11T12:00:00Z",
        "provenance": {
            "source_revision": None,
            "public": None,
            "qualification": "unverified",
        },
    }
    ledger = OperatorVersionDecisionLedger(overlay_root)
    ledger.append_registration(registration, expected_revision=ledger.read()["revision"])
    monkeypatch.setenv("ADA2_DWARF_VERSION_CATALOG_STATE_DIR", str(overlay_root))
    profile = find_profile("profile-r-amaru-measurement-stock-control")
    preview = build_deployment_version_preview(
        asdict(profile), operator_registration_id=registration["registration_id"]
    )
    desired = desired_topology_for_profile(profile, preview)
    topology = {
        "topology_id": desired["topology_id"],
        "network_magic": desired["network_magic"],
        "catalog_revision": desired["catalog_revision"],
        "operator_registration_id": desired["operator_registration_id"],
        "operator_overlay_revision": desired["operator_overlay_revision"],
        "nodes": [
            {key: node[key] for key in ("id", "implementation", "version", "image")}
            for node in desired["nodes"]
        ],
    }
    monkeypatch.setenv(
        "DWARF_GOLDEN_IMAGES",
        ",".join(
            node["image"]
            for node in desired["nodes"]
            if node["implementation"] != "amaru"
        ),
    )

    def runner(argv, timeout, execution_id):
        if argv[1:3] == ("image", "inspect") and argv[3] == registration["config_image_id"]:
            return DockerResult(
                0,
                json.dumps(
                    [
                        {
                            "Id": registration["config_image_id"],
                            "RepoDigests": [],
                            "Os": "linux",
                            "Architecture": "amd64",
                            "Config": {
                                "Entrypoint": ["/usr/local/bin/amaru"],
                                "Cmd": ["run"],
                            },
                        }
                    ]
                ),
                "",
            )
        return DockerResult(0, "[]", "")

    with pytest.raises(DockerRuntimeError, match="identity or platform"):
        DockerRuntime(
            installation_id="install",
            state_dir=tmp_path / "controller-state",
            host_state_dir="/srv/dwarf",
            runner=runner,
        ).prepare_topology(topology)


def test_redeploy_revalidates_operator_overlay_before_runtime_mutation(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("DWARF_HOST_OS", "linux")
    monkeypatch.setenv("DWARF_HOST_ARCH", "amd64")
    overlay_root = tmp_path / "dashboard-state"
    registration = {
        "registration_id": "local-amaru-config",
        "implementation": "amaru",
        "version": "10.12.0-local",
        "image_reference": "local/amaru:experimental",
        "manifest_digest": None,
        "config_image_id": "sha256:" + "b" * 64,
        "platform": "linux/amd64",
        "runtime_interface": "extracted-binary",
        "inspected_at": "2026-10-11T12:00:00Z",
        "provenance": {
            "source_revision": None,
            "public": None,
            "qualification": "unverified",
        },
    }
    ledger = OperatorVersionDecisionLedger(overlay_root)
    ledger.append_registration(registration, expected_revision=ledger.read()["revision"])
    monkeypatch.setenv("ADA2_DWARF_VERSION_CATALOG_STATE_DIR", str(overlay_root))
    profile = find_profile("profile-r-amaru-measurement-stock-control")
    preview = build_deployment_version_preview(
        asdict(profile), operator_registration_id=registration["registration_id"]
    )
    desired = desired_topology_for_profile(profile, preview)
    topology = {
        "topology_id": desired["topology_id"],
        "network_magic": desired["network_magic"],
        "catalog_revision": desired["catalog_revision"],
        "operator_registration_id": desired["operator_registration_id"],
        "operator_overlay_revision": desired["operator_overlay_revision"],
        "nodes": [
            {key: node[key] for key in ("id", "implementation", "version", "image")}
            for node in desired["nodes"]
        ],
    }
    support_images = [
        node["image"] for node in desired["nodes"]
        if node["implementation"] != "amaru"
    ]
    monkeypatch.setenv("DWARF_GOLDEN_IMAGES", ",".join(support_images))

    interface_known = [True]

    def image_runner(argv, timeout, execution_id):
        if argv[1:3] == ("image", "inspect"):
            if argv[3] == registration["config_image_id"]:
                return DockerResult(0, json.dumps([{
                    "Id": registration["config_image_id"],
                    "RepoDigests": [],
                    "Os": "linux",
                    "Architecture": "amd64",
                    "Config": {
                        "Entrypoint": (
                            ["/usr/local/bin/amaru"]
                            if interface_known[0]
                            else ["/opt/custom/start"]
                        ),
                        "Cmd": ["run"],
                    },
                }]), "")
            return DockerResult(0, "[]", "")
        raise AssertionError(argv)

    runtime = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path / "controller-state",
        host_state_dir="/srv/dwarf",
        runner=image_runner,
    )
    deployment = runtime.prepare_topology(topology)
    current = runtime._runtime_parent(profile.id) / "current"
    _retain_verified_amaru_control_runtime(runtime, deployment, current)

    mutation_calls = []
    monkeypatch.setattr(runtime, "_retained_project_roles", lambda **_kwargs: {})
    monkeypatch.setattr(runtime, "_retained_volume_specs", lambda **_kwargs: {})
    monkeypatch.setattr(runtime, "_project_volume_preflight", lambda **_kwargs: None)
    monkeypatch.setattr(runtime, "_project_preflight", lambda *_args: ([], None))
    monkeypatch.setattr(runtime, "_verify_absent_postcondition", lambda **_kwargs: None)
    monkeypatch.setattr(
        runtime,
        "_compose",
        lambda *_args: mutation_calls.append(_args) or DockerResult(0, "", ""),
    )

    current_ledger = ledger.read()
    ledger.append_registration(
        {
            **registration,
            "registration_id": "unrelated-later-registration",
            "version": "10.12.1-local",
        },
        expected_revision=current_ledger["revision"],
    )
    plan = {
        "action": "redeploy",
        "topology_id": profile.id,
        "deployment": deployment,
    }

    interface_known[0] = False
    with pytest.raises(
        DockerRuntimeError, match="identity or platform differs"
    ):
        runtime.apply(plan, "execution-changed-interface", threading.Event())
    assert mutation_calls == []
    interface_known[0] = True

    with pytest.raises(
        DockerRuntimeError, match="operator registration state differs"
    ):
        runtime.apply(plan, "execution-stale-overlay", threading.Event())

    assert mutation_calls == []


def test_prepare_topology_hashes_and_resolves_one_immutable_catalog_snapshot(
    monkeypatch, tmp_path
):
    from profile_manager.version_discovery import load_effective_version_catalog

    profile = find_profile("profile-s-cardano-measurement-stock-control")
    dashboard_catalog = load_effective_version_catalog()
    dashboard_preview = build_deployment_version_preview(
        asdict(profile), catalog=dashboard_catalog
    )
    dashboard_desired = desired_topology_for_profile(profile, dashboard_preview)
    wire_topology = {
        "topology_id": dashboard_desired["topology_id"],
        "network_magic": dashboard_desired["network_magic"],
        "catalog_revision": dashboard_preview["catalog_revision"],
        "nodes": [
            {key: node[key] for key in ("id", "implementation", "version", "image")}
            for node in dashboard_desired["nodes"]
        ],
    }
    changed_catalog = json.loads(json.dumps(dashboard_catalog))
    changed_catalog["updated_at"] = "2099-01-01T00:00:00Z"
    catalog_reads = []

    def changing_catalog_loader(*_args, **_kwargs):
        catalog_reads.append(len(catalog_reads) + 1)
        return dashboard_catalog if len(catalog_reads) == 1 else changed_catalog

    monkeypatch.setattr(
        "profile_manager.deployment_versions.load_effective_version_catalog",
        changing_catalog_loader,
    )
    monkeypatch.setattr(
        "profile_manager.version_discovery.load_effective_version_catalog",
        changing_catalog_loader,
    )
    monkeypatch.setenv(
        "DWARF_GOLDEN_IMAGES",
        ",".join(node["image"] for node in wire_topology["nodes"]),
    )

    deployment = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path,
        host_state_dir="/srv/dwarf",
        runner=lambda *_: None,
    ).prepare_topology(wire_topology)
    substrate = deployment["substrate"]
    observed_nodes = [
        {
            "node_id": node["id"],
            "implementation": node["impl"],
            "version": node["version"],
            "image": node["image"],
        }
        for node in substrate["nodes"]
    ]
    retained_desired = _verified_retained_desired(
        profile_id=profile.id,
        persisted={**deployment, "profile_id": profile.id},
        observed_nodes=observed_nodes,
    )

    assert catalog_reads == [1]
    assert substrate["catalog_snapshot"]["catalog_updated_at"] == dashboard_catalog.get(
        "updated_at"
    )
    assert retained_desired is not None
    assert retained_desired["plan_sha256"] == dashboard_desired["plan_sha256"]


def test_prepare_topology_rejects_catalog_revision_not_loaded_by_controller(
    monkeypatch, tmp_path
):
    profile = find_profile("profile-e-haskell-preview-proof")
    preview = build_deployment_version_preview(asdict(profile))
    desired = desired_topology_for_profile(profile, preview)
    topology = {
        "topology_id": desired["topology_id"],
        "network_magic": desired["network_magic"],
        "catalog_revision": "b" * 64,
        "nodes": [
            {key: node[key] for key in ("id", "implementation", "version", "image")}
            for node in desired["nodes"]
        ],
    }
    monkeypatch.setenv(
        "DWARF_GOLDEN_IMAGES",
        ",".join(node["image"] for node in topology["nodes"]),
    )

    with pytest.raises(
        DockerRuntimeError,
        match="catalog revision differs from the controller catalog",
    ):
        DockerRuntime(
            installation_id="install",
            state_dir=tmp_path,
            host_state_dir="/srv/dwarf",
            runner=lambda *_: None,
        ).prepare_topology(topology)


def test_prepare_topology_rejects_catalog_blocked_version_pair(monkeypatch, tmp_path):
    profile = find_profile("profile-p-mixed-latest-confirmed")
    profile_data = asdict(profile)
    profile_data.update(
        {
            "version_policy": "exact",
            "compatibility_pair": None,
            "cardano_version": "11.1.2",
            "amaru_version": "10.11.20260912",
        }
    )
    preview = build_deployment_version_preview(profile_data)
    assert preview["blocked"] is True
    desired = desired_topology_for_profile(profile, preview)
    topology = {
        "topology_id": desired["topology_id"],
        "network_magic": desired["network_magic"],
        "catalog_revision": preview["catalog_revision"],
        "nodes": [
            {key: node[key] for key in ("id", "implementation", "version", "image")}
            for node in desired["nodes"]
        ],
    }
    monkeypatch.setenv(
        "DWARF_GOLDEN_IMAGES",
        ",".join(node["image"] for node in topology["nodes"]),
    )

    with pytest.raises(DockerRuntimeError, match="catalog resolution is blocked"):
        DockerRuntime(
            installation_id="install",
            state_dir=tmp_path,
            host_state_dir="/srv/dwarf",
            runner=lambda *_: None,
        ).prepare_topology(topology)


def test_prepare_topology_independently_resolves_exact_requested_version(monkeypatch, tmp_path):
    profile = find_profile("profile-r-amaru-measurement-stock-control")
    profile_data = asdict(profile)
    profile_data["version_policy"] = "exact"
    profile_data["amaru_version"] = "10.11.20260918"
    preview = build_deployment_version_preview(profile_data)
    desired = desired_topology_for_profile(profile, preview)
    topology = {
        "topology_id": desired["topology_id"],
        "network_magic": desired["network_magic"],
        "nodes": [
            {key: node[key] for key in ("id", "implementation", "version", "image")}
            for node in desired["nodes"]
        ],
    }
    monkeypatch.setenv(
        "DWARF_GOLDEN_IMAGES",
        ",".join(node["image"] for node in topology["nodes"]),
    )

    runtime = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path,
        host_state_dir="/srv/dwarf",
        runner=lambda *_: None,
    )
    deployment = runtime.prepare_topology(topology)

    assert deployment["adapter"] == "runtime_amaru_control_substrate.py"
    assert deployment["substrate"]["version_policy"] == "exact"
    assert {
        (node["impl"], node["version"])
        for node in deployment["substrate"]["nodes"]
    } == {("amaru", "10.11.20260918"), ("cardano-node", "10.7.1")}


def test_prepare_topology_exact_versions_override_stale_mixed_pair(monkeypatch, tmp_path):
    profile = replace(
        find_profile("profile-p-mixed-latest-confirmed"),
        version_policy="exact",
        compatibility_pair="cardano-10.7.1__amaru-10.11.0",
    )
    requested_profile = asdict(profile)
    requested_profile.update(
        {
            "cardano_version": "11.1.2",
            "amaru_version": "10.11.20260918",
            "compatibility_pair": None,
        }
    )
    preview = build_deployment_version_preview(requested_profile)
    desired = desired_topology_for_profile(profile, preview)
    topology = {
        "topology_id": desired["topology_id"],
        "network_magic": desired["network_magic"],
        "nodes": [
            {key: node[key] for key in ("id", "implementation", "version", "image")}
            for node in desired["nodes"]
        ],
    }
    monkeypatch.setattr("profile_manager.profiles.find_profile", lambda _id: profile)
    monkeypatch.setenv(
        "DWARF_GOLDEN_IMAGES",
        ",".join(node["image"] for node in topology["nodes"]),
    )

    runtime = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path,
        host_state_dir="/srv/dwarf",
        runner=lambda *_: None,
    )
    deployment = runtime.prepare_topology(topology)

    assert deployment["adapter"] == "runtime_amaru_control_substrate.py"
    assert deployment["substrate"]["version_policy"] == "exact"
    assert {
        (node["impl"], node["version"])
        for node in deployment["substrate"]["nodes"]
    } == {("amaru", "10.11.20260918"), ("cardano-node", "11.1.2")}
    config = runtime._deployment_config(deployment, tmp_path / "current")
    assert config["controller_node_services"] == {
        "node1": "p1",
        "node2": "p2",
        "amaru1": "amaru-relay-1",
    }
    assert config["cardano_experimental_protocols"] is False


def test_prepare_default_mixed_profile_uses_imported_target_config_identity(
    monkeypatch, tmp_path
):
    profile = find_profile(
        "profile-zb-mixed-1112-amaru-20260918-nanoseconds-v3"
    )
    state = tmp_path / "controller-state"
    registry = state / "measurement-targets"
    manifest_digest = "sha256:" + "1" * 64
    config_digest = "sha256:" + "2" * 64
    source_reference = "dwarf/amaru-measurement@" + manifest_digest
    probe_image = "wrapper@sha256:" + "9" * 64
    record = {
        "schema_version": 1,
        "implementation": "amaru",
        "mode": "patched",
        "version": profile.amaru_version,
        "source_revision": profile.measurement_patch_revision,
        "patch_set_sha256": profile.measurement_patch_set_sha256,
        "image_reference": source_reference,
        "qualification_image_id": "sha256:" + "3" * 64,
        "image_manifest_digest": manifest_digest,
        "image_config_digest": config_digest,
        "runtime_image_reference": config_digest,
        "image_digest": config_digest,
        "executable_digest": "sha256:" + "5" * 64,
        "build_result_sha256": "sha256:" + "6" * 64,
        "runtime_probe_image": probe_image,
        "runtime_probe_log_sha256": "sha256:" + "8" * 64,
        "transfer_source_installation_id": "install-source",
        "transfer_manifest_sha256": "sha256:" + "4" * 64,
    }
    record_path = (
        registry
        / "amaru"
        / profile.measurement_patch_revision
        / f"{profile.measurement_patch_set_sha256}.json"
    )
    record_path.parent.mkdir(parents=True)
    record_path.write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setenv("ADA2_DWARF_MEASUREMENT_TARGET_REGISTRY", str(registry))
    monkeypatch.setenv("DWARF_HOST_OS", "linux")
    monkeypatch.setenv("DWARF_HOST_ARCH", "amd64")
    preview = build_deployment_version_preview(asdict(profile))
    desired = desired_topology_for_profile(profile, preview)
    topology = {
        "topology_id": desired["topology_id"],
        "network_magic": desired["network_magic"],
        "nodes": [
            {
                key: node[key]
                for key in ("id", "implementation", "version", "image")
            }
            for node in desired["nodes"]
        ],
    }
    monkeypatch.setenv(
        "DWARF_GOLDEN_IMAGES",
        ",".join(
            node["image"]
            for node in topology["nodes"]
            if "@sha256:" in node["image"]
        ),
    )

    def runner(argv, timeout, execution_id):
        assert argv[:3] == ("docker", "image", "inspect")
        inspected = argv[3]
        if inspected == probe_image:
            image_id = "sha256:" + "7" * 64
            repo_digests = [probe_image]
        else:
            assert inspected == config_digest
            image_id = config_digest
            repo_digests = []
        return DockerResult(
            0,
            json.dumps(
                [
                    {
                        "Id": image_id,
                        "RepoDigests": repo_digests,
                        "Os": "linux",
                        "Architecture": "amd64",
                    }
                ]
            ),
            "",
        )

    request = parse_request(
        {
            "schema_version": "v1",
            "request_id": str(uuid.uuid4()),
            "operation": "topology.plan",
            "timeout_seconds": 30,
            "action": "deploy",
            "topology": topology,
        }
    )
    wire_topology = json.loads(json.dumps(request.parameters["topology"]))
    runtime = DockerRuntime(
        installation_id="install",
        state_dir=state,
        host_state_dir="/srv/dwarf",
        runner=runner,
    )
    deployment = runtime.prepare_topology(wire_topology)

    amaru = next(
        node
        for node in deployment["substrate"]["nodes"]
        if node["impl"] == "amaru"
    )
    assert amaru["image"] == config_digest
    assert amaru["image_digest"] == config_digest
    assert amaru["image_manifest_digest"] == manifest_digest
    assert amaru["source_image_reference"] == source_reference
    assert amaru["transfer_source_installation_id"] == "install-source"

    generated = runtime._deployment_config(deployment, tmp_path / "runtime")
    validate_runtime_request(generated)
    identity = {
        "matched": True,
        "services": {
            "amaru-relay-1": {
                "container": "dwarf-profile-amaru-relay-1",
                "matched": True,
                "expected_image": config_digest,
                "expected_image_id": config_digest,
                "artifact_image_id": config_digest,
                "executable_digest_reported": record["executable_digest"],
                "executable_digest_matched": True,
            }
        },
    }
    metadata = build_runtime_metadata(
        generated,
        identity=identity,
        observation={"state": "healthy"},
        compose_file="/srv/dwarf/runtime/docker-compose.json",
    )
    assert metadata["network_magic"] == 42
    runtime_path = tmp_path / "runtime.json"
    runtime_path.write_text(json.dumps(metadata), encoding="utf-8")
    scenario = load_scenario(
        "dwarf/scenarios/amaru-measurement-boundary-proof-patched-20260918-mixed-1112.yaml"
    )
    prepared = prepare_scenario_measurements(
        scenario,
        runtime_metadata_path=runtime_path,
        tip_probe=lambda: {"block_height": 1, "block_hash": "a" * 64},
    )
    measured = prepared.resolution["target_identity"]
    assert measured["image_manifest_digest"] == manifest_digest
    assert measured["image_config_digest"] == config_digest
    assert measured["qualification_image_id"] == record["qualification_image_id"]
    assert measured["transfer_source_installation_id"] == "install-source"
    assert measured["transfer_manifest_sha256"] == record[
        "transfer_manifest_sha256"
    ]

    with pytest.raises(DockerRuntimeError, match="runtime probe differs"):
        runtime._verify_qualified_runtime_probe_image(
            probe_image,
            {
                **record,
                "runtime_probe_image": "other@sha256:" + "0" * 64,
            },
        )


def test_amaru_control_config_preserves_exact_logical_nodes_and_versions(monkeypatch, tmp_path):
    profile = find_profile("profile-r-amaru-measurement-stock-control")
    profile_data = asdict(profile)
    profile_data.update(
        {"version_policy": "exact", "amaru_version": "10.11.20260918"}
    )
    preview = build_deployment_version_preview(profile_data)
    desired = desired_topology_for_profile(profile, preview)
    topology = {
        "topology_id": desired["topology_id"],
        "network_magic": desired["network_magic"],
        "nodes": [
            {key: node[key] for key in ("id", "implementation", "version", "image")}
            for node in desired["nodes"]
        ],
    }
    monkeypatch.setenv(
        "DWARF_GOLDEN_IMAGES",
        ",".join(node["image"] for node in topology["nodes"]),
    )
    runtime = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path,
        host_state_dir="/srv/dwarf",
        runner=lambda *_: None,
    )
    deployment = runtime.prepare_topology(topology)

    config = runtime._deployment_config(deployment, tmp_path / "current")

    assert config["lifecycle"] == "cardano_amaru_relay_bootstrap_control"
    assert config["scope"] == "amaru-only"
    assert config["supporting_cardano_version"] == "10.7.1"
    assert config["amaru_version"] == "10.11.20260918"
    assert config["controller_node_services"] == {
        "bootstrap-cardano": "p1",
        "amaru1": "amaru-relay-1",
    }
    assert config["substrate"]["target_node_count"] == 1
    assert config["substrate"]["support_node_count"] == 1
    assert config["peer_sharing"] is False

    for invalid_peer_sharing in (None, True):
        invalid = {
            **deployment,
            "substrate": {
                **deployment["substrate"],
                "peer_sharing": invalid_peer_sharing,
            },
        }
        with pytest.raises(DockerRuntimeError, match="peer_sharing=false"):
            runtime._deployment_config(invalid, tmp_path / "current")

    tampered = {
        **deployment,
        "substrate": {
            **deployment["substrate"],
            "docker_host_runtime_root": "/tmp/not-owned-by-this-topology",
        },
    }
    with pytest.raises(DockerRuntimeError, match="host path"):
        runtime._deployment_config(tampered, tmp_path / "current")

    missing_magic = {
        **deployment,
        "substrate": {
            key: value
            for key, value in deployment["substrate"].items()
            if key != "network_magic"
        },
    }
    with pytest.raises(DockerRuntimeError, match="network magic"):
        runtime._deployment_config(missing_magic, tmp_path / "current")

    wrong_magic = {
        **deployment,
        "substrate": {**deployment["substrate"], "network_magic": 43},
    }
    with pytest.raises(DockerRuntimeError, match="network magic 42"):
        runtime._deployment_config(wrong_magic, tmp_path / "current")


def test_amaru_control_config_rejects_more_selected_nodes_than_proven_lifecycle(tmp_path):
    deployment = {
        "adapter": "runtime_amaru_control_substrate.py",
        "compose_project": "dwarf-profile-install-profile-over-capacity",
        "substrate": {
            "profile_id": "profile-over-capacity",
            "scope": "mixed",
            "network_magic": 42,
            "peer_sharing": False,
            "deployment_adapter": "amaru-control",
            "target_node_count": 4,
            "support_node_count": 0,
            "controller_ownership": {
                "installation_id": "install",
                "topology_id": "profile-over-capacity",
            },
            "docker_host_runtime_root": "/srv/dwarf/controller/topology-runtimes/profile-over-capacity/current",
            "nodes": [
                {
                    "id": f"node{index}",
                    "impl": "cardano-node",
                    "version": "11.1.2",
                    "image": "example.invalid/cardano@" + DIGEST,
                    "supporting": False,
                    "target_mode": "stock",
                }
                for index in range(1, 4)
            ]
            + [
                {
                    "id": "amaru1",
                    "impl": "amaru",
                    "version": "10.11.20260918",
                    "image": IMAGE,
                    "supporting": False,
                    "target_mode": "stock",
                }
            ],
        },
    }
    runtime = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path,
        host_state_dir="/srv/dwarf",
        runner=lambda *_: None,
    )

    with pytest.raises(DockerRuntimeError, match="cannot honor selected Cardano node count"):
        runtime._deployment_config(deployment, tmp_path / "current")


def test_amaru_control_retained_verification_covers_every_service_and_network(
    monkeypatch, tmp_path
):
    cardano_image = "example.invalid/cardano@" + DIGEST
    amaru_image = IMAGE
    amaru_wrapper_image = "example.invalid/amaru-bootstrap@sha256:" + "b" * 64
    amaru_wrapper_config_id = "sha256:" + "7" * 64
    monkeypatch.setenv("DWARF_GOLDEN_IMAGES", f"{cardano_image},{amaru_image}")
    topology_id = "profile-p-mixed-latest-confirmed"
    project = f"dwarf-profile-install-{topology_id}"
    ownership = {"installation_id": "install", "topology_id": topology_id}
    nodes = [
        {
            "id": "node1",
            "impl": "cardano-node",
            "version": "11.1.2",
            "image": cardano_image,
            "supporting": False,
        },
        {
            "id": "amaru1",
            "impl": "amaru",
            "version": "10.11.20260918",
            "image": amaru_image,
            "supporting": False,
        },
    ]
    actual_topology = {
        "lifecycle": "cardano_amaru_relay_bootstrap_control",
        "selected_node_services": {
            "node1": "p1",
            "amaru1": "amaru-relay-1",
        },
        "requested_target_nodes": 2,
        "requested_support_nodes": 0,
        "physical_cardano_node_count": 6,
        "physical_amaru_node_count": 2,
        "all_services": [
            "p1",
            "amaru-relay-1",
            "amaru-target-extract",
            "configurator",
        ],
        "network_names": ["default"],
        "fixed_additional_node_services": [
            "p2",
            "p3",
            "relay1",
            "relay2",
            "amaru-consumer",
            "amaru-relay-2",
        ],
    }
    config = {
        "adapter": "runtime_amaru_control_substrate.py",
        "profile_id": topology_id,
        "scope": "mixed",
        "lifecycle": "cardano_amaru_relay_bootstrap_control",
        "compose_project": project,
        "cardano_image": cardano_image,
        "amaru_image": amaru_image,
        "controller_node_services": {
            "node1": "p1",
            "amaru1": "amaru-relay-1",
        },
        "substrate": {
            "profile_id": topology_id,
            "network_magic": 42,
            "deployment_adapter": "amaru-control",
            "controller_ownership": ownership,
            "docker_host_runtime_root": "/srv/dwarf/controller/topology-runtimes/"
            + topology_id
            + "/current",
            "nodes": nodes,
            "actual_topology": dict(actual_topology),
        },
    }
    common = {
        "io.dwarf.managed": "true",
        "io.dwarf.installation": "install",
        "io.dwarf.topology": topology_id,
        "io.dwarf.hardening.exception": "proven-relay-bootstrap-writable-rootfs",
    }
    compose = {
        "services": {
            "p1": {
                "image": cardano_image,
                "labels": {
                    **common,
                    "io.dwarf.component": "node",
                    "io.dwarf.node_id": "node1",
                    "io.dwarf.implementation": "cardano-node",
                    "io.dwarf.version": "11.1.2",
                },
            },
            "amaru-relay-1": {
                "image": amaru_wrapper_image,
                "labels": {
                    **common,
                    "io.dwarf.component": "node",
                    "io.dwarf.node_id": "amaru1",
                    "io.dwarf.implementation": "amaru",
                    "io.dwarf.version": "10.11.20260918",
                },
            },
            "amaru-target-extract": {
                "image": amaru_image,
                "labels": {**common, "io.dwarf.component": "support"},
            },
            "configurator": {
                "image": "example.invalid/configurator@" + DIGEST,
                "labels": {**common, "io.dwarf.component": "support"},
            },
        },
        "networks": {
            "default": {
                "name": project + "-default",
                "labels": {**common, "io.dwarf.component": "network"},
            }
        },
        "volumes": {
            "p1-state": {
                "labels": {**common, "io.dwarf.component": "volume"}
            }
        },
    }
    report = {
        "passed": True,
        "retained": True,
        "profile_id": topology_id,
        "scope": "mixed",
        "project": project,
        "identity": {
            "matched": True,
            "services": {
                "p1": {
                    "matched": True,
                    "expected_image": cardano_image,
                    "expected_image_id": "sha256:" + "1" * 64,
                    "artifact_image_id": "sha256:" + "1" * 64,
                    "running_image_id": "sha256:" + "1" * 64,
                    "wrapper_expected_image": None,
                    "wrapper_expected_image_id": None,
                    "wrapper_matched": True,
                },
                "amaru-relay-1": {
                    "matched": True,
                    "expected_image": amaru_image,
                    "expected_image_id": "sha256:" + "2" * 64,
                    "artifact_image_id": "sha256:" + "2" * 64,
                    "running_image_id": amaru_wrapper_config_id,
                    "wrapper_expected_image": amaru_wrapper_image,
                    "wrapper_expected_image_id": amaru_wrapper_config_id,
                    "wrapper_matched": True,
                },
            },
        },
        "selected_nodes": [
            {
                "id": "node1",
                "service": "p1",
                "implementation": "cardano-node",
                "version": "11.1.2",
                "image": cardano_image,
                "supporting": False,
            },
            {
                "id": "amaru1",
                "service": "amaru-relay-1",
                "implementation": "amaru",
                "version": "10.11.20260918",
                "image": amaru_image,
                "supporting": False,
            },
        ],
        "actual_topology": dict(actual_topology),
        "observation": {"state": "healthy"},
        "required_gates": ["chain_progress", "exact_identity"],
        "gates": {"chain_progress": True, "exact_identity": True},
    }
    runtime_metadata = {
        "profile_id": topology_id,
        "network_magic": 42,
        "scope": "mixed",
        "compose_project": project,
        "actual_topology": {
            **actual_topology,
            "selected_nodes": report["selected_nodes"],
        },
        "images": {"cardano-node": cardano_image, "amaru": amaru_image},
    }
    runtime = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path,
        host_state_dir="/srv/dwarf",
        runner=lambda *_: None,
    )

    runtime._verify_amaru_control_retained(
        config=config,
        compose=compose,
        report=report,
        runtime_metadata=runtime_metadata,
        topology_id=topology_id,
        compose_project=project,
        enforce_golden=True,
    )

    for invalid_magic in (None, True, 43):
        if invalid_magic is None:
            runtime_metadata.pop("network_magic")
        else:
            runtime_metadata["network_magic"] = invalid_magic
        with pytest.raises(DockerRuntimeError, match="semantic readiness"):
            runtime._verify_amaru_control_retained(
                config=config,
                compose=compose,
                report=report,
                runtime_metadata=runtime_metadata,
                topology_id=topology_id,
                compose_project=project,
                enforce_golden=True,
            )
        runtime_metadata["network_magic"] = 42

    for invalid_magic in (None, True, 43):
        if invalid_magic is None:
            config["substrate"].pop("network_magic")
            runtime_metadata.pop("network_magic")
        else:
            config["substrate"]["network_magic"] = invalid_magic
            runtime_metadata["network_magic"] = invalid_magic
        with pytest.raises(DockerRuntimeError, match="semantic readiness"):
            runtime._verify_amaru_control_retained(
                config=config,
                compose=compose,
                report=report,
                runtime_metadata=runtime_metadata,
                topology_id=topology_id,
                compose_project=project,
                enforce_golden=True,
            )
        config["substrate"]["network_magic"] = 42
        runtime_metadata["network_magic"] = 42

    mapping = config["controller_node_services"]
    node1_service = mapping.pop("node1")
    with pytest.raises(DockerRuntimeError, match="logical node mapping"):
        runtime._verify_amaru_control_retained(
            config=config,
            compose=compose,
            report=report,
            runtime_metadata=runtime_metadata,
            topology_id=topology_id,
            compose_project=project,
            enforce_golden=True,
        )
    mapping["node1"] = node1_service
    amaru_service = mapping["amaru1"]
    mapping["amaru1"] = node1_service
    with pytest.raises(DockerRuntimeError, match="logical node mapping"):
        runtime._verify_amaru_control_retained(
            config=config,
            compose=compose,
            report=report,
            runtime_metadata=runtime_metadata,
            topology_id=topology_id,
            compose_project=project,
            enforce_golden=True,
        )
    mapping["amaru1"] = amaru_service

    compose["services"]["p2"] = {
        "image": cardano_image,
        "labels": {**common, "io.dwarf.component": "support"},
    }
    with pytest.raises(DockerRuntimeError, match="physical shape"):
        runtime._verify_amaru_control_retained(
            config=config,
            compose=compose,
            report=report,
            runtime_metadata=runtime_metadata,
            topology_id=topology_id,
            compose_project=project,
            enforce_golden=True,
        )
    compose["services"].pop("p2")

    compose["networks"]["other"] = {
        "name": project + "-other",
        "labels": {**common, "io.dwarf.component": "network"},
    }
    with pytest.raises(DockerRuntimeError, match="physical shape"):
        runtime._verify_amaru_control_retained(
            config=config,
            compose=compose,
            report=report,
            runtime_metadata=runtime_metadata,
            topology_id=topology_id,
            compose_project=project,
            enforce_golden=True,
        )
    compose["networks"].pop("other")

    report["actual_topology"]["physical_cardano_node_count"] = 5
    with pytest.raises(DockerRuntimeError, match="semantic readiness"):
        runtime._verify_amaru_control_retained(
            config=config,
            compose=compose,
            report=report,
            runtime_metadata=runtime_metadata,
            topology_id=topology_id,
            compose_project=project,
            enforce_golden=True,
        )
    report["actual_topology"]["physical_cardano_node_count"] = 6

    config["substrate"]["docker_host_runtime_root"] = ""
    with pytest.raises(DockerRuntimeError, match="host path"):
        runtime._verify_amaru_control_retained(
            config=config,
            compose=compose,
            report=report,
            runtime_metadata=runtime_metadata,
            topology_id=topology_id,
            compose_project=project,
            enforce_golden=True,
        )
    config["substrate"]["docker_host_runtime_root"] = (
        "/srv/dwarf/controller/topology-runtimes/" + topology_id + "/current"
    )

    compose["services"]["p1"]["ports"] = ["0.0.0.0:39001:3001"]
    with pytest.raises(DockerRuntimeError, match="container policy"):
        runtime._verify_amaru_control_retained(
            config=config,
            compose=compose,
            report=report,
            runtime_metadata=runtime_metadata,
            topology_id=topology_id,
            compose_project=project,
            enforce_golden=True,
        )
    compose["services"]["p1"].pop("ports")

    compose["services"]["p1"]["volumes"] = ["./rogue:/state"]
    with pytest.raises(DockerRuntimeError, match="bind source"):
        runtime._verify_amaru_control_retained(
            config=config,
            compose=compose,
            report=report,
            runtime_metadata=runtime_metadata,
            topology_id=topology_id,
            compose_project=project,
            enforce_golden=True,
        )
    compose["services"]["p1"].pop("volumes")

    report["identity"]["services"]["p1"]["expected_image"] = amaru_image
    with pytest.raises(DockerRuntimeError, match="per-node image identity"):
        runtime._verify_amaru_control_retained(
            config=config,
            compose=compose,
            report=report,
            runtime_metadata=runtime_metadata,
            topology_id=topology_id,
            compose_project=project,
            enforce_golden=True,
        )
    report["identity"]["services"]["p1"]["expected_image"] = cardano_image

    report["identity"]["services"]["amaru-relay-1"][
        "wrapper_expected_image"
    ] = "example.invalid/wrong-wrapper@sha256:" + "c" * 64
    with pytest.raises(DockerRuntimeError, match="per-node image identity"):
        runtime._verify_amaru_control_retained(
            config=config,
            compose=compose,
            report=report,
            runtime_metadata=runtime_metadata,
            topology_id=topology_id,
            compose_project=project,
            enforce_golden=True,
        )
    report["identity"]["services"]["amaru-relay-1"][
        "wrapper_expected_image"
    ] = amaru_wrapper_image

    del compose["services"]["configurator"]["labels"]["io.dwarf.installation"]
    with pytest.raises(DockerRuntimeError, match="service ownership"):
        runtime._verify_amaru_control_retained(
            config=config,
            compose=compose,
            report=report,
            runtime_metadata=runtime_metadata,
            topology_id=topology_id,
            compose_project=project,
            enforce_golden=True,
        )
    compose["services"]["configurator"]["labels"]["io.dwarf.installation"] = (
        "install"
    )

    compose["services"]["configurator"]["labels"][
        "io.dwarf.hardening.exception"
    ] = "unapproved"
    with pytest.raises(DockerRuntimeError, match="hardening policy"):
        runtime._verify_amaru_control_retained(
            config=config,
            compose=compose,
            report=report,
            runtime_metadata=runtime_metadata,
            topology_id=topology_id,
            compose_project=project,
            enforce_golden=True,
        )


def test_amaru_control_health_requires_current_exact_owned_runtime(monkeypatch, tmp_path):
    topology_id = "profile-r-amaru-measurement-stock-control"
    project = f"dwarf-profile-install-{topology_id}"
    cardano_image = "example.invalid/cardano@sha256:" + "1" * 64
    amaru_target_image = IMAGE
    amaru_wrapper_image = "example.invalid/amaru-bootstrap@sha256:" + "2" * 64
    cardano_config_id = "sha256:" + "4" * 64
    amaru_target_config_id = "sha256:" + "5" * 64
    amaru_wrapper_config_id = "sha256:" + "6" * 64
    runtime_root = tmp_path / "current"
    (runtime_root / "evidence").mkdir(parents=True)
    config = {
        "adapter": "runtime_amaru_control_substrate.py",
        "profile_id": topology_id,
        "scope": "amaru-only",
        "compose_project": project,
        "controller_node_services": {
            "bootstrap-cardano": "p1",
            "amaru1": "amaru-relay-1",
        },
        "substrate": {
            "network_magic": 42,
            "nodes": [
                {
                    "id": "bootstrap-cardano",
                    "impl": "cardano-node",
                    "version": "10.7.1",
                    "image": cardano_image,
                },
                {
                    "id": "amaru1",
                    "impl": "amaru",
                    "version": "10.11.20260918",
                    "image": amaru_target_image,
                },
            ],
        },
    }
    compose = {
        "services": {
            "p1": {
                "restart": "always",
                "image": cardano_image,
                "labels": {"io.dwarf.component": "node"},
            },
            "amaru-relay-1": {
                "restart": "always",
                "image": amaru_wrapper_image,
                "labels": {"io.dwarf.component": "node"},
            },
            "configurator": {
                "image": IMAGE,
                "labels": {"io.dwarf.component": "support"},
            },
        }
    }
    report = {
        "schema_version": 1,
        "profile_id": topology_id,
        "observed_at": "2026-10-10T00:00:02Z",
        "passed": True,
        "classification": "retained-runtime-ready",
        "failed_gates": [],
        "retained": True,
        "identity": {
            "matched": True,
            "services": {
                "p1": {
                    "matched": True,
                    "expected_image": cardano_image,
                    "expected_image_id": cardano_config_id,
                    "artifact_image_id": cardano_config_id,
                    "running_image_id": cardano_config_id,
                    "wrapper_expected_image": None,
                    "wrapper_expected_image_id": None,
                    "wrapper_matched": True,
                },
                "amaru-relay-1": {
                    "matched": True,
                    "expected_image": amaru_target_image,
                    "expected_image_id": amaru_target_config_id,
                    "artifact_image_id": amaru_target_config_id,
                    "running_image_id": amaru_wrapper_config_id,
                    "wrapper_expected_image": amaru_wrapper_image,
                    "wrapper_expected_image_id": amaru_wrapper_config_id,
                    "wrapper_matched": True,
                },
            },
        },
        "observation": {
            "state": "healthy",
            "observation": {"samples": [{"slot": 1}, {"slot": 2}]},
        },
        "required_gates": ["chain_progress"],
        "gates": {"chain_progress": True},
    }
    (runtime_root / "docker-compose.json").write_text(json.dumps(compose))
    (runtime_root / "versioned-deployment.json").write_text(json.dumps(config))
    (runtime_root / "evidence" / "deployment-report.json").write_text(
        json.dumps(report)
    )
    common = {
        "io.dwarf.managed": "true",
        "io.dwarf.installation": "install",
        "io.dwarf.topology": topology_id,
        "com.docker.compose.project": project,
    }
    owned = [
        _container(
            f"{project}-p1-1",
            {
                **common,
                "com.docker.compose.service": "p1",
                "io.dwarf.component": "node",
                "io.dwarf.node_id": "bootstrap-cardano",
            },
            image=cardano_image,
            image_id=cardano_config_id,
        ),
        _container(
            f"{project}-amaru-relay-1-1",
            {
                **common,
                "com.docker.compose.service": "amaru-relay-1",
                "io.dwarf.component": "node",
                "io.dwarf.node_id": "amaru1",
            },
            image=amaru_wrapper_image,
            image_id=amaru_wrapper_config_id,
        ),
        _container(
            f"{project}-configurator-1",
            {
                **common,
                "com.docker.compose.service": "configurator",
                "io.dwarf.component": "support",
            },
            state="exited",
        ),
    ]
    runtime = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path,
        host_state_dir="/srv/dwarf",
        runner=lambda *_: None,
    )
    fresh_report = {**report, "fresh_observation": True}
    fresh_report_path = runtime_root / "evidence" / "fresh.json"
    fresh_report_path.write_text(json.dumps(fresh_report))
    monkeypatch.setattr(
        runtime,
        "_fresh_amaru_control_report",
        lambda *_: (fresh_report, fresh_report_path),
    )

    healthy = runtime._amaru_control_health(
        runtime_root=runtime_root,
        config=config,
        topology_id=topology_id,
        owned=owned,
        checked_at="2026-10-10T00:00:00Z",
    )
    assert healthy["state"] == "healthy"
    assert healthy["semantic_health"] == {
        "schema_version": 1,
        "profile_id": topology_id,
        "completed_at": "2026-10-10T00:00:02Z",
        "fresh_observation": True,
        "retained": True,
        "passed": True,
        "classification": "retained-runtime-ready",
        "required_gates": ["chain_progress"],
        "failed_gates": [],
        "gates": {"chain_progress": True},
        "identity_matched": True,
        "sample_count": 2,
    }
    assert {node["id"] for node in healthy["nodes"]} == {
        "bootstrap-cardano",
        "amaru1",
    }
    amaru = next(node for node in healthy["nodes"] if node["id"] == "amaru1")
    assert amaru["image"] == amaru_target_image
    assert amaru["runtime_image"] == amaru_wrapper_image
    assert amaru["target_image_id"] == amaru_target_config_id
    assert amaru["artifact_image_id"] == amaru_target_config_id
    assert amaru["runtime_image_id"] == amaru_wrapper_config_id
    assert amaru["runtime_container_image_id"] == amaru_wrapper_config_id
    assert amaru["target_image_id"] != amaru_target_image.rsplit("@", 1)[-1]
    assert amaru["runtime_image_id"] != amaru_wrapper_image.rsplit("@", 1)[-1]

    unhealthy = runtime._amaru_control_health(
        runtime_root=runtime_root,
        config=config,
        topology_id=topology_id,
        owned=owned[:-2] + owned[-1:],
        checked_at="2026-10-10T00:01:00Z",
    )
    assert unhealthy["state"] == "unhealthy"

    wrong_wrapper = json.loads(json.dumps(owned))
    wrong_wrapper[1]["Image"] = "sha256:" + "3" * 64
    rejected = runtime._amaru_control_health(
        runtime_root=runtime_root,
        config=config,
        topology_id=topology_id,
        owned=wrong_wrapper,
        checked_at="2026-10-10T00:02:00Z",
    )
    assert rejected["state"] == "unhealthy"
    assert rejected["reason_code"] == "amaru_control_runtime_identity_mismatch"


def test_topology_health_supplies_all_owned_amaru_compose_roles(monkeypatch, tmp_path):
    topology_id = "profile-r-amaru-measurement-stock-control"
    current = (
        tmp_path / "controller" / "topology-runtimes" / topology_id / "current"
    )
    current.mkdir(parents=True)
    config = {
        "adapter": "runtime_amaru_control_substrate.py",
        "compose_project": "dwarf-profile-install-profile-r",
        "substrate": {},
    }
    (current / "versioned-deployment.json").write_text(json.dumps(config))
    common = {
        "io.dwarf.managed": "true",
        "io.dwarf.installation": "install",
        "io.dwarf.topology": topology_id,
        "com.docker.compose.project": "dwarf-profile-install-profile-r",
    }
    node = _container(
        "dwarf-profile-install-profile-r-amaru-relay-1-1",
        {
            **common,
            "com.docker.compose.service": "amaru-relay-1",
            "io.dwarf.component": "node",
        },
    )
    support = _container(
        "dwarf-profile-install-profile-r-sidecar-1",
        {
            **common,
            "com.docker.compose.service": "sidecar",
            "io.dwarf.component": "support",
        },
    )
    foreign = _container(
        "foreign",
        {
            **common,
            "io.dwarf.installation": "another-installation",
            "io.dwarf.topology": "another-topology",
            "com.docker.compose.service": "foreign",
            "io.dwarf.component": "support",
        },
    )
    runtime = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path,
        runner=lambda *_: None,
    )
    monkeypatch.setattr(runtime, "_all_containers", lambda: [node, support, foreign])
    monkeypatch.setattr(runtime, "_verify_retained_runtime", lambda **_kwargs: None)
    captured = {}

    def health(**kwargs):
        captured["names"] = {
            str(item.get("Name") or "").lstrip("/") for item in kwargs["owned"]
        }
        return {"state": "healthy", "nodes": []}

    monkeypatch.setattr(runtime, "_amaru_control_health", health)

    runtime.topology_health(topology_id)

    assert captured["names"] == {
        "dwarf-profile-install-profile-r-amaru-relay-1-1",
        "dwarf-profile-install-profile-r-sidecar-1",
    }


def test_amaru_project_preflight_scopes_node_and_support_services(monkeypatch, tmp_path):
    topology_id = "profile-owned"
    project = "dwarf-profile-install-profile-owned"

    def service(name, role):
        return _container(
            f"{project}-{name}-1",
            {
                "io.dwarf.managed": "true",
                "io.dwarf.installation": "install",
                "io.dwarf.topology": topology_id,
                "io.dwarf.component": role,
                "com.docker.compose.project": project,
                "com.docker.compose.service": name,
            },
        )

    runtime = DockerRuntime(installation_id="install", state_dir=tmp_path)
    monkeypatch.setattr(
        runtime,
        "_all_containers",
        lambda: [service("p1", "node"), service("p2", "support")],
    )
    monkeypatch.setattr(runtime, "_inspect_optional", lambda *_args: None)

    before, network = runtime._project_preflight(
        topology_id,
        project,
        "runtime_amaru_control_substrate.py",
    )

    assert network is None
    assert {
        str(item.get("Name") or "").lstrip("/") for item in before
    } == {f"{project}-p1-1", f"{project}-p2-1"}

    with pytest.raises(DockerRuntimeError, match="unmanaged container collision"):
        runtime._project_preflight(
            topology_id,
            project,
            "runtime_amaru_control_substrate.py",
            {"p1": "node", "p2": "node"},
        )

    mismatched = service("p2", "support")
    mismatched["Config"]["Labels"]["com.docker.compose.project"] = "other-project"
    monkeypatch.setattr(runtime, "_all_containers", lambda: [mismatched])
    with pytest.raises(DockerRuntimeError, match="unmanaged container collision"):
        runtime._project_preflight(
            topology_id,
            project,
            "runtime_amaru_control_substrate.py",
        )


def test_amaru_repair_refuses_running_containers_with_stale_semantic_gates(
    monkeypatch, tmp_path
):
    runtime = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path,
        host_state_dir="/srv/dwarf",
        runner=lambda *_: None,
    )
    monkeypatch.setattr(
        runtime,
        "topology_health",
        lambda _topology_id: {
            "state": "unhealthy",
            "reason_code": "fresh_chain_progress_failed",
            "nodes": [],
        },
    )

    with pytest.raises(DockerRuntimeError, match="did not restore semantic readiness"):
        runtime._verify_running_postcondition(
            topology_id="profile-r-amaru-measurement-stock-control",
            compose_project="dwarf-profile-install-profile-r-amaru-measurement-stock-control",
            config={"adapter": "runtime_amaru_control_substrate.py"},
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("version", "0.0.0"),
        ("image", "example.invalid/cardano@sha256:" + "9" * 64),
    ],
)
def test_prepare_topology_rejects_client_identity_different_from_catalog(
    monkeypatch, tmp_path, field, value
):
    profile = find_profile("profile-e-haskell-preview-proof")
    preview = build_deployment_version_preview(asdict(profile))
    desired = desired_topology_for_profile(profile, preview)
    topology = {
        "topology_id": desired["topology_id"],
        "network_magic": desired["network_magic"],
        "catalog_revision": preview["catalog_revision"],
        "nodes": [
            {key: node[key] for key in ("id", "implementation", "version", "image")}
            for node in desired["nodes"]
        ],
    }
    topology["nodes"][0][field] = value
    monkeypatch.setenv("DWARF_GOLDEN_IMAGES", topology["nodes"][0]["image"])

    with pytest.raises(DockerRuntimeError, match="catalog-resolved topology"):
        DockerRuntime(
            installation_id="install", state_dir=tmp_path, runner=lambda *_: None
        ).prepare_topology(topology)


def test_inventory_exposes_owned_and_read_only_unmanaged_resources():
    owned = _container(
        "dwarf-install-local-lab-amaru-1",
        {
            "io.dwarf.managed": "true",
            "io.dwarf.installation": "install",
            "io.dwarf.topology": "local-lab",
            "io.dwarf.component": "node",
            "io.dwarf.node_id": "amaru-1",
            "io.dwarf.implementation": "amaru",
            "io.dwarf.version": "0.3.0",
        },
    )
    unmanaged = _container("unrelated", {"some.label": "value"})
    support = _container(
        "dwarf-install-local-lab-p1-1",
        {
            "io.dwarf.managed": "true",
            "io.dwarf.installation": "install",
            "io.dwarf.topology": "local-lab",
            "io.dwarf.component": "support",
            "io.dwarf.node_id": "bootstrap-cardano",
            "io.dwarf.implementation": "cardano-node",
            "io.dwarf.version": "10.7.1",
        },
    )
    network = {
        "Id": "network-id",
        "Name": "dwarf-install-local-lab-net",
        "Labels": {
            "io.dwarf.managed": "true",
            "io.dwarf.installation": "install",
            "io.dwarf.topology": "local-lab",
            "io.dwarf.component": "network",
        },
    }
    volume = {
        "Name": "dwarf-install-local-lab_chain-db",
        "Labels": {
            "io.dwarf.managed": "true",
            "io.dwarf.installation": "install",
            "io.dwarf.topology": "local-lab",
            "io.dwarf.component": "volume",
        },
    }
    unrelated_host_workload = _container(
        "private-database",
        {"some.label": "private"},
        image="postgres@" + DIGEST,
    )

    def runner(argv, timeout, execution_id):
        if argv[1:] == ("ps", "-aq"):
            return DockerResult(0, "owned-id\nsupport-id\nunmanaged-id\ndatabase-id\n", "")
        if argv[1:3] == ("inspect", "owned-id"):
            return DockerResult(
                0, json.dumps([owned, support, unmanaged, unrelated_host_workload]), ""
            )
        if argv[1:3] == ("network", "ls"):
            return DockerResult(0, "network-id\n", "")
        if argv[1:] == ("network", "inspect", "network-id"):
            return DockerResult(0, json.dumps([network]), "")
        if argv[1:3] == ("volume", "ls"):
            return DockerResult(0, "dwarf-install-local-lab_chain-db\n", "")
        if argv[1:] == (
            "volume",
            "inspect",
            "dwarf-install-local-lab_chain-db",
        ):
            return DockerResult(0, json.dumps([volume]), "")
        raise AssertionError(argv)

    inventory = DockerRuntime(installation_id="install", runner=runner).inventory()
    assert inventory["managed_count"] == 1
    assert inventory["unmanaged_count"] == 2
    assert inventory["owned_resource_count"] == 4
    assert {row["component"] for row in inventory["owned_resources"]} == {
        "node",
        "network",
        "support",
        "volume",
    }
    assert inventory["visible_unmanaged_count"] == 1
    assert inventory["unmanaged_total_count"] == 2
    assert inventory["managed"][0]["name"] == "dwarf-install-local-lab-amaru-1"
    assert inventory["managed"][0]["ownership"] == "managed"
    assert inventory["unmanaged"] == [
        {
            "id": "unrelated-id",
            "name": "unrelated",
            "image": IMAGE,
            "state": "running",
            "node_id": "",
            "implementation": "",
            "version": "",
            "topology_id": "",
            "ownership": "unmanaged",
            "mutation_allowed": False,
        }
    ]


@pytest.mark.parametrize(
    "labels",
    [
        {
            "io.dwarf.managed": "true",
            "io.dwarf.installation": "install",
            "io.dwarf.topology": "",
            "io.dwarf.component": "network",
        },
        {
            "io.dwarf.managed": "true",
            "io.dwarf.installation": "another-installation",
            "io.dwarf.topology": "local-lab",
            "io.dwarf.component": "network",
        },
        {
            "io.dwarf.managed": "true",
            "io.dwarf.installation": "install",
            "io.dwarf.topology": "local-lab",
        },
    ],
)
def test_inventory_rejects_malformed_or_foreign_owned_auxiliary_resources(labels):
    def runner(argv, timeout, execution_id):
        if argv[1:] == ("ps", "-aq"):
            return DockerResult(0, "", "")
        if argv[1:3] == ("network", "ls"):
            return DockerResult(0, "network-id\n", "")
        if argv[1:] == ("network", "inspect", "network-id"):
            return DockerResult(
                0,
                json.dumps(
                    [{"Id": "network-id", "Name": "owned-network", "Labels": labels}]
                ),
                "",
            )
        raise AssertionError(argv)

    with pytest.raises(DockerRuntimeError, match="owned network inventory"):
        DockerRuntime(installation_id="install", runner=runner).inventory()


@pytest.mark.parametrize("component", ["node", "support"])
def test_inventory_rejects_owned_container_without_topology_identity(component):
    malformed = _container(
        f"malformed-{component}",
        {
            "io.dwarf.managed": "true",
            "io.dwarf.installation": "install",
            "io.dwarf.topology": "",
            "io.dwarf.component": component,
        },
    )

    def runner(argv, timeout, execution_id):
        if argv[1:] == ("ps", "-aq"):
            return DockerResult(0, "malformed-id\n", "")
        if argv[1:] == ("inspect", "malformed-id"):
            return DockerResult(0, json.dumps([malformed]), "")
        raise AssertionError(argv)

    with pytest.raises(DockerRuntimeError, match="owned container inventory"):
        DockerRuntime(installation_id="install", runner=runner).inventory()


def test_topology_health_proves_owned_node_listener_and_live_chain_tip(
    monkeypatch, tmp_path
):
    cardano_image = "ghcr.io/intersectmbo/cardano-node@" + DIGEST
    monkeypatch.setenv("DWARF_GOLDEN_IMAGES", cardano_image)
    node = {
        "id": "node1",
        "implementation": "cardano-node",
        "version": "11.1.2",
        "image": cardano_image,
    }
    plan = _plan(nodes=[node])
    deployment = plan["deployment"]
    runtime_root = tmp_path / "controller" / "topology-runtimes" / "local-lab" / "current"
    runtime_root.mkdir(parents=True)
    config = {
        "schema_version": "v1",
        "substrate": deployment["substrate"],
        "output_dir": str(runtime_root / "evidence"),
        "runtime_root": str(runtime_root),
        "compose_project": deployment["compose_project"],
        "healthy_timeout_seconds": 300,
    }
    (runtime_root / "versioned-deployment.json").write_text(
        json.dumps(config), encoding="utf-8"
    )
    project = deployment["compose_project"]
    host_root = deployment["substrate"]["docker_host_runtime_root"]
    compose_body = {
        "services": {
            "node1": {
                "image": cardano_image,
                "container_name": f"{project}-node1-1",
                "labels": {
                    "io.dwarf.managed": "true",
                    "io.dwarf.installation": "install",
                    "io.dwarf.topology": "local-lab",
                    "io.dwarf.component": "node",
                    "io.dwarf.node_id": "node1",
                },
                "volumes": [f"{host_root}/env:/env", f"{host_root}/logs:/logs"],
                "ports": ["127.0.0.1:39100:3001"],
                "read_only": True,
                "cap_drop": ["ALL"],
                "security_opt": ["no-new-privileges:true"],
            }
        },
        "networks": {
            "default": {
                "labels": {
                    "io.dwarf.managed": "true",
                    "io.dwarf.installation": "install",
                    "io.dwarf.topology": "local-lab",
                    "io.dwarf.component": "network",
                }
            }
        },
    }
    (runtime_root / "docker-compose.yml").write_text(
        json.dumps(compose_body), encoding="utf-8"
    )
    evidence = runtime_root / "evidence"
    evidence.mkdir()
    (evidence / "compose-report.json").write_text(
        json.dumps({"healthy": True, "network_magic": 42}), encoding="utf-8"
    )
    owned = _container(
        f"{project}-node1-1",
        {
            "io.dwarf.managed": "true",
            "io.dwarf.installation": "install",
            "io.dwarf.topology": "local-lab",
            "io.dwarf.component": "node",
            "io.dwarf.node_id": "node1",
            "io.dwarf.implementation": "cardano-node",
            "io.dwarf.version": "11.1.2",
        },
        image=cardano_image,
    )
    owned["NetworkSettings"] = {
        "Ports": {"3001/tcp": [{"HostIp": "127.0.0.1", "HostPort": "39100"}]}
    }

    def runner(argv, timeout, execution_id):
        if argv[1:] == ("ps", "-aq"):
            return DockerResult(0, "owned-id\n", "")
        if argv[1:3] == ("inspect", "owned-id"):
            return DockerResult(0, json.dumps([owned]), "")
        if argv[1:3] == ("exec", f"{project}-node1-1"):
            return DockerResult(
                0,
                json.dumps(
                    {
                        "hash": "abc",
                        "slot": 12345,
                        "block": 678,
                        "syncProgress": "100.00",
                    }
                ),
                "",
            )
        raise AssertionError(argv)

    runtime = DockerRuntime(
        installation_id="install", state_dir=tmp_path, runner=runner
    )
    runtime._write_runtime_integrity(
        runtime_root=runtime_root,
        topology_id="local-lab",
        compose_project=project,
    )

    result = runtime.topology_health("local-lab")

    assert result["state"] == "healthy"
    assert result["reason_code"] == "node_and_chain_healthy"
    assert result["nodes"][0]["healthy"] is True
    assert result["nodes"][0]["tip"]["block"] == 678
    assert result["persisted_deployment"] == config


def test_topology_health_rejects_divergent_or_unsynchronized_node_tips(
    monkeypatch, tmp_path
):
    cardano_image = "ghcr.io/intersectmbo/cardano-node@" + DIGEST
    monkeypatch.setenv("DWARF_GOLDEN_IMAGES", cardano_image)
    nodes = [
        {
            "id": node_id,
            "implementation": "cardano-node",
            "version": "11.1.2",
            "image": cardano_image,
        }
        for node_id in ("node1", "node2")
    ]
    plan = _plan(nodes=nodes)
    deployment = plan["deployment"]
    runtime_root = tmp_path / "controller" / "topology-runtimes" / "local-lab" / "current"
    (runtime_root / "evidence").mkdir(parents=True)
    config = {
        "schema_version": "v1",
        "substrate": deployment["substrate"],
        "output_dir": str(runtime_root / "evidence"),
        "runtime_root": str(runtime_root),
        "compose_project": deployment["compose_project"],
        "healthy_timeout_seconds": 300,
    }
    (runtime_root / "versioned-deployment.json").write_text(
        json.dumps(config), encoding="utf-8"
    )
    (runtime_root / "evidence" / "compose-report.json").write_text(
        json.dumps({"healthy": True, "network_magic": 42}), encoding="utf-8"
    )
    project = deployment["compose_project"]
    owned = []
    for node_id in ("node1", "node2"):
        item = _container(
            f"{project}-{node_id}-1",
            {
                "io.dwarf.managed": "true",
                "io.dwarf.installation": "install",
                "io.dwarf.topology": "local-lab",
                "io.dwarf.component": "node",
                "io.dwarf.node_id": node_id,
                "io.dwarf.implementation": "cardano-node",
                "io.dwarf.version": "11.1.2",
            },
            image=cardano_image,
        )
        item["NetworkSettings"] = {
            "Ports": {"3001/tcp": [{"HostIp": "127.0.0.1", "HostPort": "39100"}]}
        }
        owned.append(item)

    tips = {
        f"{project}-node1-1": {
            "hash": "chain-a",
            "slot": 100,
            "block": 10,
            "syncProgress": "100.00",
        },
        f"{project}-node2-1": {
            "hash": "chain-b",
            "slot": 100,
            "block": 10,
            "syncProgress": "5.00",
        },
    }

    def runner(argv, timeout, execution_id):
        if argv[1:] == ("ps", "-aq"):
            return DockerResult(0, "node1-id\nnode2-id\n", "")
        if argv[1:3] == ("inspect", "node1-id"):
            return DockerResult(0, json.dumps(owned), "")
        if argv[1] == "exec":
            return DockerResult(0, json.dumps(tips[argv[2]]), "")
        raise AssertionError(argv)

    runtime = DockerRuntime(
        installation_id="install", state_dir=tmp_path, runner=runner
    )
    monkeypatch.setattr(runtime, "_verify_retained_runtime", lambda **_kwargs: config)

    result = runtime.topology_health("local-lab")

    assert result["state"] == "unhealthy"
    assert result["reason_code"] == "node_chain_not_converged"
    assert result["nodes"][0]["healthy"] is True
    assert result["nodes"][1]["healthy"] is False


def test_apply_refuses_unmanaged_compose_collision_before_adapter(tmp_path):
    project = "dwarf-profile-install-local-lab"
    unmanaged = _container(project + "-amaru-1-1", {"io.dwarf.managed": "false"})
    calls = []

    def runner(argv, timeout, execution_id):
        calls.append(argv)
        if argv[1:] == ("ps", "-aq"):
            return DockerResult(0, "unmanaged-id\n", "")
        if argv[1:] == ("inspect", "unmanaged-id"):
            return DockerResult(0, json.dumps([unmanaged]), "")
        raise AssertionError(argv)

    runtime = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path,
        runner=runner,
        adapter_runner=lambda *_: (_ for _ in ()).throw(AssertionError("adapter called")),
    )
    with pytest.raises(DockerRuntimeError, match="unmanaged container collision"):
        runtime.apply(_plan(), "execution-1", threading.Event())


def test_snapshot_includes_the_exact_owned_topology_network(tmp_path):
    project = "dwarf-profile-install-local-lab"
    network = {
        "Id": "network-id",
        "Name": project + "-net",
        "Labels": {
            "io.dwarf.managed": "true",
            "io.dwarf.installation": "install",
            "io.dwarf.topology": "local-lab",
            "io.dwarf.component": "network",
        },
    }

    def runner(argv, timeout, execution_id):
        if argv[1:] == ("ps", "-aq"):
            return DockerResult(0, "", "")
        if argv[1:3] == ("network", "inspect"):
            return DockerResult(0, json.dumps([network]), "")
        raise AssertionError(argv)

    snapshot = DockerRuntime(
        installation_id="install", state_dir=tmp_path, runner=runner
    ).snapshot("local-lab")

    assert snapshot["resources"] == []
    assert snapshot["networks"] == [
        {
            "id": "network-id",
            "name": project + "-net",
            "installation_id": "install",
            "topology_id": "local-lab",
            "ownership": "managed",
        }
    ]


def test_snapshot_fails_closed_when_network_absence_cannot_be_inspected(tmp_path):
    def runner(argv, timeout, execution_id):
        if argv[1:] == ("ps", "-aq"):
            return DockerResult(0, "", "")
        if argv[1:3] == ("network", "inspect"):
            return DockerResult(1, "", "Cannot connect to the Docker daemon")
        raise AssertionError(argv)

    runtime = DockerRuntime(
        installation_id="install", state_dir=tmp_path, runner=runner
    )

    with pytest.raises(DockerRuntimeError, match="network inspect failed"):
        runtime.snapshot("local-lab")


def test_failed_deploy_tears_down_partial_project_and_archives_evidence(tmp_path):
    calls = []

    def runner(argv, timeout, execution_id):
        calls.append(argv)
        if argv[1:] == ("ps", "-aq"):
            return DockerResult(0, "", "")
        if argv[1:3] == ("image", "inspect"):
            return DockerResult(0, "{}", "")
        if argv[1:3] == ("network", "inspect"):
            return DockerResult(1, "", "not found")
        if argv[1] == "compose" and "down" in argv:
            return DockerResult(0, "removed", "")
        raise AssertionError(argv)

    def failed_adapter(argv, timeout, execution_id):
        _emit_healthy_adapter_report(argv)
        report = Path(json.loads(Path(argv[-1]).read_text())["output_dir"]) / "compose-report.json"
        report.write_text(json.dumps({"healthy": False}), encoding="utf-8")
        return DockerResult(1, "", "deliberate failure")

    runtime = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path,
        runner=runner,
        adapter_runner=failed_adapter,
    )
    with pytest.raises(DockerRuntimeError, match="semantic readiness"):
        runtime.apply(_plan(), "execution-rollback", threading.Event())

    parent = tmp_path / "controller" / "topology-runtimes" / "local-lab"
    assert not (parent / "current").exists()
    assert (parent / "failed-execution-rollback").is_dir()
    assert any(argv[1] == "compose" and "down" in argv for argv in calls)


def test_failed_deploy_refuses_successful_rollback_claim_when_owned_network_remains(
    tmp_path,
):
    network_inspections = 0
    project = "dwarf-profile-install-local-lab"
    owned_network = {
        "Id": "network-id",
        "Name": project + "-net",
        "Labels": {
            "io.dwarf.managed": "true",
            "io.dwarf.installation": "install",
            "io.dwarf.topology": "local-lab",
            "io.dwarf.component": "network",
        },
    }

    def runner(argv, timeout, execution_id):
        nonlocal network_inspections
        if argv[1:] == ("ps", "-aq"):
            return DockerResult(0, "", "")
        if argv[1:3] == ("image", "inspect"):
            return DockerResult(0, "{}", "")
        if argv[1:3] == ("network", "inspect"):
            network_inspections += 1
            if network_inspections == 1:
                return DockerResult(1, "", "not found")
            return DockerResult(0, json.dumps([owned_network]), "")
        if argv[1] == "compose" and "down" in argv:
            return DockerResult(1, "", "network removal failed")
        raise AssertionError(argv)

    def failed_adapter(argv, timeout, execution_id):
        _emit_healthy_adapter_report(argv)
        report = Path(json.loads(Path(argv[-1]).read_text())["output_dir"]) / "compose-report.json"
        report.write_text(json.dumps({"healthy": False}), encoding="utf-8")
        return DockerResult(1, "", "deliberate failure")

    runtime = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path,
        runner=runner,
        adapter_runner=failed_adapter,
    )

    with pytest.raises(DockerRuntimeError, match="mutation and cleanup both failed"):
        runtime.apply(_plan(), "execution-network-rollback", threading.Event())

    assert network_inspections == 2


def test_repair_uses_retained_compose_runtime_without_recreating_plan(tmp_path):
    project = "dwarf-profile-install-local-lab"
    labels = {
        "io.dwarf.managed": "true",
        "io.dwarf.installation": "install",
        "io.dwarf.topology": "local-lab",
        "io.dwarf.component": "node",
        "io.dwarf.node_id": "amaru-1",
        "io.dwarf.implementation": "amaru",
        "io.dwarf.version": "0.3.0",
    }
    stopped = _container(project + "-amaru-1-1", labels, state="exited")
    network = {
        "Name": project + "-net",
        "Labels": {**labels, "io.dwarf.component": "network"},
    }
    current = tmp_path / "controller" / "topology-runtimes" / "local-lab" / "current"
    calls = []
    inventory_reads = 0

    def runner(argv, timeout, execution_id):
        nonlocal inventory_reads
        calls.append(argv)
        if argv[1:] == ("ps", "-aq"):
            inventory_reads += 1
            return DockerResult(0, "owned-id\n", "")
        if argv[1:] == ("inspect", "owned-id"):
            observed = stopped if inventory_reads == 1 else {**stopped, "State": {"Status": "running"}}
            return DockerResult(0, json.dumps([observed]), "")
        if argv[1:] == ("network", "inspect", project + "-net"):
            return DockerResult(0, json.dumps([network]), "")
        if argv[1] == "compose" and "up" in argv:
            return DockerResult(0, "started", "")
        raise AssertionError(argv)

    runtime = DockerRuntime(installation_id="install", state_dir=tmp_path, runner=runner)
    plan = _plan(action="repair")
    _retain_runtime(runtime, plan, current)
    result = runtime.apply(plan, "execution-repair", threading.Event())

    assert result["status"] == "applied"
    assert any(argv[1] == "compose" and argv[-2:] == ("up", "-d") for argv in calls)


def test_failed_remove_restores_prior_stopped_state_and_retains_exact_runtime(tmp_path):
    project = "dwarf-profile-install-local-lab"
    labels = {
        "io.dwarf.managed": "true",
        "io.dwarf.installation": "install",
        "io.dwarf.topology": "local-lab",
        "io.dwarf.component": "node",
    }
    stopped = _container(project + "-amaru-1-1", labels, state="exited")
    network = {
        "Name": project + "-net",
        "Labels": {**labels, "io.dwarf.component": "network"},
    }
    current = tmp_path / "controller" / "topology-runtimes" / "local-lab" / "current"
    calls = []

    def runner(argv, timeout, execution_id):
        calls.append(argv)
        if argv[1:] == ("ps", "-aq"):
            return DockerResult(0, "old-id\n", "")
        if argv[1:] == ("inspect", "old-id"):
            return DockerResult(0, json.dumps([stopped]), "")
        if argv[1:] == ("network", "inspect", project + "-net"):
            return DockerResult(0, json.dumps([network]), "")
        if argv[1] == "compose" and "down" in argv:
            return DockerResult(1, "", "deliberate failure")
        if argv[1] == "compose" and argv[-2:] == ("up", "-d"):
            return DockerResult(0, "restored", "")
        if argv[1:] == ("stop", project + "-amaru-1-1"):
            return DockerResult(0, "stopped", "")
        raise AssertionError(argv)

    runtime = DockerRuntime(installation_id="install", state_dir=tmp_path, runner=runner)
    plan = _plan(action="remove")
    _retain_runtime(runtime, plan, current)
    (current / "chain-state.marker").write_text("exact-before-state", encoding="utf-8")
    with pytest.raises(DockerRuntimeError, match="topology removal failed"):
        runtime.apply(plan, "execution-remove", threading.Event())

    assert (current / "chain-state.marker").read_text() == "exact-before-state"
    assert ("docker", "stop", project + "-amaru-1-1") in calls
    assert not (current.parent / "removed-execution-remove").exists()


def test_remove_deletes_owned_orphan_network_without_retained_runtime(tmp_path):
    project = "dwarf-profile-install-local-lab"
    labels = {
        "io.dwarf.managed": "true",
        "io.dwarf.installation": "install",
        "io.dwarf.topology": "local-lab",
        "io.dwarf.component": "network",
    }
    network = {"Name": project + "-net", "Labels": labels}
    network_present = True
    calls = []

    def runner(argv, timeout, execution_id):
        nonlocal network_present
        calls.append(argv)
        if argv[1:] == ("ps", "-aq"):
            return DockerResult(0, "", "")
        if argv[1:] == ("network", "inspect", project + "-net"):
            if network_present:
                return DockerResult(0, json.dumps([network]), "")
            return DockerResult(1, "", "not found")
        if argv[1:] == ("network", "rm", project + "-net"):
            network_present = False
            return DockerResult(0, project + "-net", "")
        raise AssertionError(argv)

    runtime = DockerRuntime(installation_id="install", state_dir=tmp_path, runner=runner)

    result = runtime.apply(
        _plan(action="remove"), "execution-remove-orphan-network", threading.Event()
    )

    assert result == {"status": "applied", "topology_id": "local-lab", "containers": []}
    assert ("docker", "network", "rm", project + "-net") in calls
    assert network_present is False


def test_failed_repair_removes_created_container_and_restores_prior_running_state(tmp_path):
    project = "dwarf-profile-install-local-lab"
    labels = {
        "io.dwarf.managed": "true",
        "io.dwarf.installation": "install",
        "io.dwarf.topology": "local-lab",
        "io.dwarf.component": "node",
    }
    original = _container(project + "-amaru-1-1", labels, state="running")
    created = _container(project + "-unexpected-1", labels, state="running")
    network = {
        "Name": project + "-net",
        "Labels": {**labels, "io.dwarf.component": "network"},
    }
    current = tmp_path / "controller" / "topology-runtimes" / "local-lab" / "current"
    inventory_reads = 0
    calls = []

    def runner(argv, timeout, execution_id):
        nonlocal inventory_reads
        calls.append(argv)
        if argv[1:] == ("ps", "-aq"):
            inventory_reads += 1
            ids = "old-id\n" if inventory_reads == 1 else "old-id\ncreated-id\n"
            return DockerResult(0, ids, "")
        if argv[1:] == ("inspect", "old-id"):
            return DockerResult(0, json.dumps([original]), "")
        if argv[1:] == ("inspect", "old-id", "created-id"):
            return DockerResult(0, json.dumps([original, created]), "")
        if argv[1:] == ("network", "inspect", project + "-net"):
            return DockerResult(0, json.dumps([network]), "")
        if argv[1] == "compose" and argv[-2:] == ("up", "-d"):
            return DockerResult(1, "", "deliberate failure")
        if argv[1:] == ("container", "rm", "--force", project + "-unexpected-1"):
            return DockerResult(0, "removed", "")
        if argv[1:] == ("start", project + "-amaru-1-1"):
            return DockerResult(0, "started", "")
        raise AssertionError(argv)

    runtime = DockerRuntime(installation_id="install", state_dir=tmp_path, runner=runner)
    plan = _plan(action="repair")
    _retain_runtime(runtime, plan, current)
    with pytest.raises(DockerRuntimeError, match="Compose repair failed"):
        runtime.apply(plan, "execution-repair", threading.Event())

    assert ("docker", "container", "rm", "--force", project + "-unexpected-1") in calls
    assert ("docker", "start", project + "-amaru-1-1") in calls


def test_failed_repair_removes_network_that_did_not_exist_before_mutation(tmp_path):
    project = "dwarf-profile-install-local-lab"
    labels = {
        "io.dwarf.managed": "true",
        "io.dwarf.installation": "install",
        "io.dwarf.topology": "local-lab",
        "io.dwarf.component": "network",
    }
    network = {"Name": project + "-net", "Labels": labels}
    current = tmp_path / "controller" / "topology-runtimes" / "local-lab" / "current"
    network_inspections = 0
    calls = []

    def runner(argv, timeout, execution_id):
        nonlocal network_inspections
        calls.append(argv)
        if argv[1:] == ("ps", "-aq"):
            return DockerResult(0, "", "")
        if argv[1:] == ("network", "inspect", project + "-net"):
            network_inspections += 1
            if network_inspections == 1 or network_inspections == 3:
                return DockerResult(1, "", "not found")
            return DockerResult(0, json.dumps([network]), "")
        if argv[1] == "compose" and argv[-2:] == ("up", "-d"):
            return DockerResult(1, "", "deliberate failure after network creation")
        if argv[1:] == ("network", "rm", project + "-net"):
            return DockerResult(0, project + "-net", "")
        raise AssertionError(argv)

    runtime = DockerRuntime(installation_id="install", state_dir=tmp_path, runner=runner)
    plan = _plan(action="repair")
    _retain_runtime(runtime, plan, current)

    with pytest.raises(DockerRuntimeError, match="Compose repair failed"):
        runtime.apply(plan, "execution-repair-network", threading.Event())

    assert network_inspections == 3
    assert ("docker", "network", "rm", project + "-net") in calls


def test_cancelled_adapter_deploy_removes_partial_project_and_retains_evidence(tmp_path):
    calls = []
    cancelled = threading.Event()

    def runner(argv, timeout, execution_id):
        calls.append(argv)
        if argv[1:] == ("ps", "-aq"):
            return DockerResult(0, "", "")
        if argv[1:3] == ("image", "inspect"):
            return DockerResult(0, "{}", "")
        if argv[1:3] == ("network", "inspect"):
            return DockerResult(1, "", "not found")
        if argv[1] == "compose" and "down" in argv:
            return DockerResult(0, "removed", "")
        raise AssertionError(argv)

    def adapter(argv, timeout, execution_id):
        _emit_healthy_adapter_report(argv)
        cancelled.set()
        return DockerResult(0, "healthy=true", "")

    result = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path,
        runner=runner,
        adapter_runner=adapter,
    ).apply(_plan(), "execution-cancelled", cancelled)

    assert result["status"] == "cancelled"
    assert any(argv[1] == "compose" and "down" in argv for argv in calls)


def test_adapter_cancellation_waits_until_signal_ignoring_child_is_gone(tmp_path):
    child = tmp_path / "child.py"
    parent = tmp_path / "parent.py"
    ready = tmp_path / "child-ready"
    escaped = tmp_path / "orphan-completed"
    child.write_text(
        "import signal,sys,time\n"
        "from pathlib import Path\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "Path(sys.argv[1]).write_text('ready')\n"
        "time.sleep(2)\n"
        "Path(sys.argv[2]).write_text('escaped')\n",
        encoding="utf-8",
    )
    parent.write_text(
        "import subprocess,sys,time\n"
        "subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2], sys.argv[3]])\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    runtime = DockerRuntime(installation_id="install", state_dir=tmp_path)
    result = []
    thread = threading.Thread(
        target=lambda: result.append(
            runtime._run_adapter(
                (sys.executable, str(parent), str(child), str(ready), str(escaped)),
                30,
                "execution-process-group",
            )
        )
    )
    thread.start()
    deadline = time.monotonic() + 3
    while not ready.is_file() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert ready.is_file()

    runtime.cancel("execution-process-group")
    thread.join(timeout=8)

    assert not thread.is_alive()
    assert result[0].returncode != 0
    time.sleep(2.2)
    assert not escaped.exists()


def test_failed_redeploy_restores_exact_runtime_directory_and_stopped_state(tmp_path):
    project = "dwarf-profile-install-local-lab"
    labels = {
        "io.dwarf.managed": "true",
        "io.dwarf.installation": "install",
        "io.dwarf.topology": "local-lab",
        "io.dwarf.component": "node",
        "io.dwarf.node_id": "amaru-1",
        "io.dwarf.implementation": "amaru",
        "io.dwarf.version": "0.3.0",
    }
    old = _container(project + "-amaru-1-1", labels, state="exited")
    network = {
        "Name": project + "-net",
        "Labels": {**labels, "io.dwarf.component": "network"},
    }
    current = tmp_path / "controller" / "topology-runtimes" / "local-lab" / "current"
    calls = []
    present = True

    def runner(argv, timeout, execution_id):
        nonlocal present
        calls.append(argv)
        if argv[1:] == ("ps", "-aq"):
            return DockerResult(0, "old-id\n" if present else "", "")
        if argv[1:3] == ("image", "inspect"):
            return DockerResult(0, "{}", "")
        if argv[1:] == ("inspect", "old-id"):
            return DockerResult(0, json.dumps([old]), "")
        if argv[1:] == ("network", "inspect", project + "-net"):
            return (
                DockerResult(0, json.dumps([network]), "")
                if present
                else DockerResult(1, "", "not found")
            )
        if argv[1] == "compose" and "down" in argv:
            present = False
            return DockerResult(0, "ok", "")
        if argv[1] == "compose" and argv[-2:] == ("up", "-d"):
            present = True
            return DockerResult(0, "ok", "")
        if argv[1:] == ("stop", project + "-amaru-1-1"):
            return DockerResult(0, "stopped", "")
        raise AssertionError(argv)

    def failed_adapter(argv, timeout, execution_id):
        _emit_healthy_adapter_report(argv)
        report = Path(json.loads(Path(argv[-1]).read_text())["output_dir"]) / "compose-report.json"
        report.write_text(json.dumps({"healthy": False}), encoding="utf-8")
        return DockerResult(1, "", "deliberate failure")

    runtime = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path,
        runner=runner,
        adapter_runner=failed_adapter,
    )
    plan = _plan(action="redeploy")
    _retain_runtime(runtime, plan, current)
    (current / "chain-state.marker").write_text("exact-before-state", encoding="utf-8")
    with pytest.raises(DockerRuntimeError, match="semantic readiness"):
        runtime.apply(plan, "execution-redeploy", threading.Event())

    assert (current / "chain-state.marker").read_text() == "exact-before-state"
    assert any(argv[1] == "compose" and argv[-2:] == ("up", "-d") for argv in calls)
    assert ("docker", "stop", project + "-amaru-1-1") in calls


def test_redeploy_replaces_legacy_substrate_but_repair_requires_exact_match(tmp_path):
    project = "dwarf-profile-install-local-lab"
    labels = {
        "io.dwarf.managed": "true",
        "io.dwarf.installation": "install",
        "io.dwarf.topology": "local-lab",
        "io.dwarf.component": "node",
        "io.dwarf.node_id": "amaru-1",
        "io.dwarf.implementation": "amaru",
        "io.dwarf.version": "0.3.0",
    }
    old = _container(project + "-amaru-1-1", labels, state="exited")
    network = {
        "Name": project + "-net",
        "Labels": {**labels, "io.dwarf.component": "network"},
    }
    current = tmp_path / "controller" / "topology-runtimes" / "local-lab" / "current"
    present = True

    def runner(argv, timeout, execution_id):
        nonlocal present
        if argv[1:] == ("ps", "-aq"):
            return DockerResult(0, "old-id\n" if present else "", "")
        if argv[1:3] == ("image", "inspect"):
            return DockerResult(0, "{}", "")
        if argv[1:] == ("inspect", "old-id"):
            return DockerResult(0, json.dumps([old]), "")
        if argv[1:] == ("network", "inspect", project + "-net"):
            return (
                DockerResult(0, json.dumps([network]), "")
                if present
                else DockerResult(1, "", "not found")
            )
        if argv[1] == "compose" and "down" in argv:
            present = False
            return DockerResult(0, "ok", "")
        if argv[1] == "compose" and argv[-2:] == ("up", "-d"):
            present = True
            return DockerResult(0, "ok", "")
        if argv[1:] == ("stop", project + "-amaru-1-1"):
            return DockerResult(0, "stopped", "")
        raise AssertionError(argv)

    def failed_adapter(argv, timeout, execution_id):
        _emit_healthy_adapter_report(argv)
        report = Path(json.loads(Path(argv[-1]).read_text())["output_dir"]) / "compose-report.json"
        report.write_text(json.dumps({"healthy": False}), encoding="utf-8")
        return DockerResult(1, "", "deliberate failure")

    runtime = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path,
        runner=runner,
        adapter_runner=failed_adapter,
    )
    plan = _plan(action="redeploy")
    plan["deployment"]["substrate"]["peer_sharing"] = False
    _retain_runtime(runtime, plan, current)
    retained_path = current / "versioned-deployment.json"
    retained = json.loads(retained_path.read_text())
    del retained["substrate"]["peer_sharing"]
    runtime._write_json(retained_path, retained)
    runtime._write_runtime_integrity(
        runtime_root=current,
        topology_id="local-lab",
        compose_project=project,
    )

    with pytest.raises(DockerRuntimeError, match="semantic readiness"):
        runtime.apply(plan, "execution-legacy-peer-sharing", threading.Event())

    repair = {**plan, "action": "repair"}
    with pytest.raises(DockerRuntimeError, match="retained topology differs"):
        runtime.apply(repair, "execution-repair-legacy-drift", threading.Event())


def test_failed_redeploy_refuses_rollback_when_predecessor_network_is_not_restored(
    tmp_path,
):
    project = "dwarf-profile-install-local-lab"
    labels = {
        "io.dwarf.managed": "true",
        "io.dwarf.installation": "install",
        "io.dwarf.topology": "local-lab",
        "io.dwarf.component": "node",
        "io.dwarf.node_id": "amaru-1",
        "io.dwarf.implementation": "amaru",
        "io.dwarf.version": "0.3.0",
    }
    old = _container(project + "-amaru-1-1", labels, state="exited")
    network = {
        "Name": project + "-net",
        "Labels": {**labels, "io.dwarf.component": "network"},
    }
    current = tmp_path / "controller" / "topology-runtimes" / "local-lab" / "current"
    network_inspections = 0

    def runner(argv, timeout, execution_id):
        nonlocal network_inspections
        if argv[1:] == ("ps", "-aq"):
            return DockerResult(0, "old-id\n", "")
        if argv[1:3] == ("image", "inspect"):
            return DockerResult(0, "{}", "")
        if argv[1:] == ("inspect", "old-id"):
            return DockerResult(0, json.dumps([old]), "")
        if argv[1:] == ("network", "inspect", project + "-net"):
            network_inspections += 1
            if network_inspections == 1:
                return DockerResult(0, json.dumps([network]), "")
            return DockerResult(1, "", "not found")
        if argv[1] == "compose":
            return DockerResult(0, "ok", "")
        if argv[1:] == ("stop", project + "-amaru-1-1"):
            return DockerResult(0, "stopped", "")
        raise AssertionError(argv)

    def failed_adapter(argv, timeout, execution_id):
        _emit_healthy_adapter_report(argv)
        report = Path(json.loads(Path(argv[-1]).read_text())["output_dir"]) / "compose-report.json"
        report.write_text(json.dumps({"healthy": False}), encoding="utf-8")
        return DockerResult(1, "", "deliberate failure")

    runtime = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path,
        runner=runner,
        adapter_runner=failed_adapter,
    )
    plan = _plan(action="redeploy")
    _retain_runtime(runtime, plan, current)

    with pytest.raises(DockerRuntimeError, match="mutation and rollback both failed"):
        runtime.apply(plan, "execution-redeploy-network", threading.Event())

    assert network_inspections == 2


def test_failed_redeploy_teardown_restores_prior_runtime_and_container_state(tmp_path):
    project = "dwarf-profile-install-local-lab"
    labels = {
        "io.dwarf.managed": "true",
        "io.dwarf.installation": "install",
        "io.dwarf.topology": "local-lab",
        "io.dwarf.component": "node",
    }
    stopped = _container(project + "-amaru-1-1", labels, state="exited")
    network = {
        "Name": project + "-net",
        "Labels": {**labels, "io.dwarf.component": "network"},
    }
    current = tmp_path / "controller" / "topology-runtimes" / "local-lab" / "current"
    calls = []

    def runner(argv, timeout, execution_id):
        calls.append(argv)
        if argv[1:] == ("ps", "-aq"):
            return DockerResult(0, "old-id\n", "")
        if argv[1:] == ("inspect", "old-id"):
            return DockerResult(0, json.dumps([stopped]), "")
        if argv[1:] == ("network", "inspect", project + "-net"):
            return DockerResult(0, json.dumps([network]), "")
        if argv[1] == "compose" and "down" in argv:
            return DockerResult(1, "", "deliberate teardown failure")
        if argv[1] == "compose" and argv[-2:] == ("up", "-d"):
            return DockerResult(0, "restored", "")
        if argv[1:] == ("stop", project + "-amaru-1-1"):
            return DockerResult(0, "stopped", "")
        raise AssertionError(argv)

    runtime = DockerRuntime(installation_id="install", state_dir=tmp_path, runner=runner)
    plan = _plan(action="redeploy")
    _retain_runtime(runtime, plan, current)
    (current / "chain-state.marker").write_text("exact-before-state", encoding="utf-8")
    with pytest.raises(DockerRuntimeError, match="redeploy teardown failed"):
        runtime.apply(plan, "execution-redeploy-down", threading.Event())

    assert (current / "chain-state.marker").read_text() == "exact-before-state"
    assert ("docker", "stop", project + "-amaru-1-1") in calls
    assert not (current.parent / "rollback-execution-redeploy-down").exists()


def test_redeploy_refuses_residual_owned_resource_and_restores_exact_prior_state(tmp_path):
    project = "dwarf-profile-install-local-lab"
    labels = {
        "io.dwarf.managed": "true",
        "io.dwarf.installation": "install",
        "io.dwarf.topology": "local-lab",
        "io.dwarf.component": "node",
    }
    stopped = _container(project + "-amaru-1-1", labels, state="exited")
    network = {
        "Name": project + "-net",
        "Labels": {**labels, "io.dwarf.component": "network"},
    }
    current = tmp_path / "controller" / "topology-runtimes" / "local-lab" / "current"
    calls = []
    adapter_calls = []
    network_reads = 0

    def runner(argv, timeout, execution_id):
        nonlocal network_reads
        calls.append(argv)
        if argv[1:] == ("ps", "-aq"):
            return DockerResult(0, "old-id\n", "")
        if argv[1:] == ("inspect", "old-id"):
            return DockerResult(0, json.dumps([stopped]), "")
        if argv[1:] == ("network", "inspect", project + "-net"):
            network_reads += 1
            return DockerResult(0, json.dumps([network]), "")
        if argv[1] == "compose" and "down" in argv:
            return DockerResult(0, "claimed removed", "")
        if argv[1] == "compose" and argv[-2:] == ("up", "-d"):
            return DockerResult(0, "restored", "")
        if argv[1:] == ("stop", project + "-amaru-1-1"):
            return DockerResult(0, "stopped", "")
        raise AssertionError(argv)

    runtime = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path,
        runner=runner,
        adapter_runner=lambda *args: adapter_calls.append(args),
    )
    plan = _plan(action="redeploy")
    _retain_runtime(runtime, plan, current)
    (current / "chain-state.marker").write_text("exact-before-state", encoding="utf-8")

    with pytest.raises(DockerRuntimeError, match="managed containers behind"):
        runtime.apply(plan, "execution-redeploy-residual", threading.Event())

    assert adapter_calls == []
    assert (current / "chain-state.marker").read_text(encoding="utf-8") == "exact-before-state"
    assert any(argv[1] == "compose" and argv[-2:] == ("up", "-d") for argv in calls)
    assert ("docker", "stop", project + "-amaru-1-1") in calls
    assert network_reads >= 2


def test_retained_compose_tampering_is_rejected_before_repair_mutation(tmp_path):
    project = "dwarf-profile-install-local-lab"
    labels = {
        "io.dwarf.managed": "true",
        "io.dwarf.installation": "install",
        "io.dwarf.topology": "local-lab",
        "io.dwarf.component": "node",
    }
    owned = _container(project + "-amaru-1-1", labels, state="exited")
    network = {"Name": project + "-net", "Labels": {**labels, "io.dwarf.component": "network"}}
    calls = []

    def runner(argv, timeout, execution_id):
        calls.append(argv)
        if argv[1:] == ("ps", "-aq"):
            return DockerResult(0, "owned-id\n", "")
        if argv[1:] == ("inspect", "owned-id"):
            return DockerResult(0, json.dumps([owned]), "")
        if argv[1:] == ("network", "inspect", project + "-net"):
            return DockerResult(0, json.dumps([network]), "")
        raise AssertionError(argv)

    runtime = DockerRuntime(installation_id="install", state_dir=tmp_path, runner=runner)
    plan = _plan(action="repair")
    current = runtime._runtime_parent("local-lab") / "current"
    _retain_runtime(runtime, plan, current)
    compose_path = current / "docker-compose.yml"
    compose = json.loads(compose_path.read_text())
    compose["services"]["amaru-1"]["image"] = "evil.invalid/root@sha256:" + "b" * 64
    compose_path.write_text(json.dumps(compose), encoding="utf-8")

    with pytest.raises(DockerRuntimeError, match="compose integrity"):
        runtime.apply(plan, "execution-tamper", threading.Event())

    assert not any(argv[1] == "compose" for argv in calls)


def test_repair_rechecks_current_golden_allowlist_before_compose(monkeypatch, tmp_path):
    project = "dwarf-profile-install-local-lab"
    labels = {
        "io.dwarf.managed": "true",
        "io.dwarf.installation": "install",
        "io.dwarf.topology": "local-lab",
        "io.dwarf.component": "node",
    }
    owned = _container(project + "-amaru-1-1", labels, state="exited")
    network = {"Name": project + "-net", "Labels": {**labels, "io.dwarf.component": "network"}}
    calls = []

    def runner(argv, timeout, execution_id):
        calls.append(argv)
        if argv[1:] == ("ps", "-aq"):
            return DockerResult(0, "owned-id\n", "")
        if argv[1:] == ("inspect", "owned-id"):
            return DockerResult(0, json.dumps([owned]), "")
        if argv[1:] == ("network", "inspect", project + "-net"):
            return DockerResult(0, json.dumps([network]), "")
        raise AssertionError(argv)

    runtime = DockerRuntime(installation_id="install", state_dir=tmp_path, runner=runner)
    plan = _plan(action="repair")
    _retain_runtime(runtime, plan, runtime._runtime_parent("local-lab") / "current")
    monkeypatch.setenv("DWARF_GOLDEN_IMAGES", "ghcr.io/example/other@sha256:" + "b" * 64)

    with pytest.raises(DockerRuntimeError, match="approved golden image"):
        runtime.apply(plan, "execution-revoked-image", threading.Event())

    assert not any(argv[1] == "compose" for argv in calls)


@pytest.mark.parametrize("action", ["remove", "repair"])
def test_pre_set_cancellation_never_mutates_retained_topology(action, tmp_path):
    project = "dwarf-profile-install-local-lab"
    labels = {
        "io.dwarf.managed": "true",
        "io.dwarf.installation": "install",
        "io.dwarf.topology": "local-lab",
        "io.dwarf.component": "node",
    }
    owned = _container(project + "-amaru-1-1", labels)
    network = {"Name": project + "-net", "Labels": {**labels, "io.dwarf.component": "network"}}
    calls = []

    def runner(argv, timeout, execution_id):
        calls.append(argv)
        if argv[1:] == ("ps", "-aq"):
            return DockerResult(0, "owned-id\n", "")
        if argv[1:] == ("inspect", "owned-id"):
            return DockerResult(0, json.dumps([owned]), "")
        if argv[1:] == ("network", "inspect", project + "-net"):
            return DockerResult(0, json.dumps([network]), "")
        raise AssertionError(argv)

    runtime = DockerRuntime(installation_id="install", state_dir=tmp_path, runner=runner)
    current = runtime._runtime_parent("local-lab") / "current"
    plan = _plan(action=action)
    _retain_runtime(runtime, plan, current)
    cancelled = threading.Event()
    cancelled.set()

    result = runtime.apply(plan, "execution-pre-cancelled", cancelled)

    assert result["status"] == "cancelled"
    assert current.is_dir()
    assert not any(argv[1] == "compose" for argv in calls)


def test_remove_cancellation_after_down_rolls_back_and_does_not_archive(tmp_path):
    project = "dwarf-profile-install-local-lab"
    labels = {
        "io.dwarf.managed": "true",
        "io.dwarf.installation": "install",
        "io.dwarf.topology": "local-lab",
        "io.dwarf.component": "node",
    }
    stopped = _container(project + "-amaru-1-1", labels, state="exited")
    network = {"Name": project + "-net", "Labels": {**labels, "io.dwarf.component": "network"}}
    cancelled = threading.Event()
    calls = []

    def runner(argv, timeout, execution_id):
        calls.append(argv)
        if argv[1:] == ("ps", "-aq"):
            return DockerResult(0, "owned-id\n", "")
        if argv[1:] == ("inspect", "owned-id"):
            return DockerResult(0, json.dumps([stopped]), "")
        if argv[1:] == ("network", "inspect", project + "-net"):
            return DockerResult(0, json.dumps([network]), "")
        if argv[1] == "compose" and "down" in argv:
            cancelled.set()
            return DockerResult(0, "removed", "")
        if argv[1] == "compose" and argv[-2:] == ("up", "-d"):
            return DockerResult(0, "restored", "")
        if argv[1:] == ("stop", project + "-amaru-1-1"):
            return DockerResult(0, "stopped", "")
        raise AssertionError(argv)

    runtime = DockerRuntime(installation_id="install", state_dir=tmp_path, runner=runner)
    plan = _plan(action="remove")
    current = runtime._runtime_parent("local-lab") / "current"
    _retain_runtime(runtime, plan, current)

    result = runtime.apply(plan, "execution-cancel-after-down", cancelled)

    assert result["status"] == "cancelled"
    assert current.is_dir()
    assert not (current.parent / "removed-execution-cancel-after-down").exists()
    assert any(argv[1] == "compose" and argv[-2:] == ("up", "-d") for argv in calls)


def test_remove_cancellation_refuses_success_when_predecessor_network_is_missing(tmp_path):
    project = "dwarf-profile-install-local-lab"
    labels = {
        "io.dwarf.managed": "true",
        "io.dwarf.installation": "install",
        "io.dwarf.topology": "local-lab",
        "io.dwarf.component": "node",
    }
    stopped = _container(project + "-amaru-1-1", labels, state="exited")
    network = {
        "Name": project + "-net",
        "Labels": {**labels, "io.dwarf.component": "network"},
    }
    cancelled = threading.Event()
    current = tmp_path / "controller" / "topology-runtimes" / "local-lab" / "current"
    network_inspections = 0

    def runner(argv, timeout, execution_id):
        nonlocal network_inspections
        if argv[1:] == ("ps", "-aq"):
            return DockerResult(0, "owned-id\n", "")
        if argv[1:] == ("inspect", "owned-id"):
            return DockerResult(0, json.dumps([stopped]), "")
        if argv[1:] == ("network", "inspect", project + "-net"):
            network_inspections += 1
            if network_inspections == 1:
                return DockerResult(0, json.dumps([network]), "")
            return DockerResult(1, "", "not found")
        if argv[1] == "compose" and "down" in argv:
            cancelled.set()
            return DockerResult(0, "removed", "")
        if argv[1] == "compose" and argv[-2:] == ("up", "-d"):
            return DockerResult(0, "restored", "")
        if argv[1:] == ("stop", project + "-amaru-1-1"):
            return DockerResult(0, "stopped", "")
        raise AssertionError(argv)

    runtime = DockerRuntime(installation_id="install", state_dir=tmp_path, runner=runner)
    plan = _plan(action="remove")
    _retain_runtime(runtime, plan, current)

    with pytest.raises(DockerRuntimeError, match="mutation and rollback both failed"):
        runtime.apply(plan, "execution-remove-missing-network", cancelled)

    assert network_inspections == 2


def test_repair_cancellation_after_up_restores_exact_prior_state(tmp_path):
    project = "dwarf-profile-install-local-lab"
    labels = {
        "io.dwarf.managed": "true",
        "io.dwarf.installation": "install",
        "io.dwarf.topology": "local-lab",
        "io.dwarf.component": "node",
    }
    stopped = _container(project + "-amaru-1-1", labels, state="exited")
    network = {"Name": project + "-net", "Labels": {**labels, "io.dwarf.component": "network"}}
    cancelled = threading.Event()
    calls = []

    def runner(argv, timeout, execution_id):
        calls.append(argv)
        if argv[1:] == ("ps", "-aq"):
            return DockerResult(0, "owned-id\n", "")
        if argv[1:] == ("inspect", "owned-id"):
            return DockerResult(0, json.dumps([stopped]), "")
        if argv[1:] == ("network", "inspect", project + "-net"):
            return DockerResult(0, json.dumps([network]), "")
        if argv[1] == "compose" and argv[-2:] == ("up", "-d"):
            cancelled.set()
            return DockerResult(0, "started", "")
        if argv[1:] == ("stop", project + "-amaru-1-1"):
            return DockerResult(0, "stopped", "")
        raise AssertionError(argv)

    runtime = DockerRuntime(installation_id="install", state_dir=tmp_path, runner=runner)
    plan = _plan(action="repair")
    current = runtime._runtime_parent("local-lab") / "current"
    _retain_runtime(runtime, plan, current)

    result = runtime.apply(plan, "execution-cancel-after-up", cancelled)

    assert result["status"] == "cancelled"
    assert ("docker", "stop", project + "-amaru-1-1") in calls


def test_amaru_repair_cancellation_removes_recreated_declared_support_service(
    monkeypatch, tmp_path
):
    topology_id = "profile-owned"
    project = "dwarf-profile-install-profile-owned"
    substrate = {"profile_id": topology_id}
    retained = {
        "adapter": "runtime_amaru_control_substrate.py",
        "compose_project": project,
        "substrate": substrate,
        "cardano_image": "example.invalid/cardano@" + DIGEST,
    }
    roles = {"p1": "node", "p2": "support"}
    states = {"p1": "exited"}
    cancelled = threading.Event()
    calls = []

    def inventory():
        return [
            _container(
                f"{project}-{service}-1",
                {
                    "io.dwarf.managed": "true",
                    "io.dwarf.installation": "install",
                    "io.dwarf.topology": topology_id,
                    "io.dwarf.component": roles[service],
                    "com.docker.compose.project": project,
                    "com.docker.compose.service": service,
                },
                state=state,
            )
            for service, state in states.items()
        ]

    def runner(argv, timeout, execution_id):
        calls.append(argv)
        if argv[1:] == ("ps", "-aq"):
            return DockerResult(0, "\n".join(states) + "\n", "")
        if argv[1] == "inspect":
            return DockerResult(0, json.dumps(inventory()), "")
        if argv[1:] == ("container", "rm", "--force", f"{project}-p2-1"):
            states.pop("p2")
            return DockerResult(0, f"{project}-p2-1", "")
        if argv[1:] == ("stop", f"{project}-p1-1"):
            states["p1"] = "exited"
            return DockerResult(0, f"{project}-p1-1", "")
        raise AssertionError(argv)

    runtime = DockerRuntime(
        installation_id="install",
        state_dir=tmp_path,
        runner=runner,
    )
    current = runtime._runtime_parent(topology_id) / "current"
    current.mkdir(parents=True)
    (current / "versioned-deployment.json").write_text(json.dumps(retained))
    (current / "docker-compose.json").write_text(
        json.dumps(
            {
                "services": {
                    service: {"labels": {"io.dwarf.component": role}}
                    for service, role in roles.items()
                }
            }
        )
    )
    monkeypatch.setattr(runtime, "_verify_retained_runtime", lambda **_: retained)
    monkeypatch.setattr(runtime, "_retained_volume_specs", lambda **_: {})
    monkeypatch.setattr(runtime, "_project_volume_preflight", lambda **_: None)
    monkeypatch.setattr(runtime, "_inspect_optional", lambda *_args: None)
    monkeypatch.setattr(runtime, "_restore_network_state", lambda **_: None)

    def compose(_root, _project, arguments, _execution_id, **_kwargs):
        assert arguments == ("up", "-d")
        states.update({"p1": "running", "p2": "running"})
        cancelled.set()
        return DockerResult(0, "repaired", "")

    monkeypatch.setattr(runtime, "_compose", compose)

    result = runtime.apply(
        {
            "action": "repair",
            "topology_id": topology_id,
            "deployment": {
                "adapter": "runtime_amaru_control_substrate.py",
                "compose_project": project,
                "substrate": substrate,
            },
        },
        "execution-cancel-amaru-repair",
        cancelled,
    )

    assert result["status"] == "cancelled"
    assert states == {"p1": "exited"}
    assert (
        "docker",
        "container",
        "rm",
        "--force",
        f"{project}-p2-1",
    ) in calls


def test_amaru_volume_preflight_refuses_unowned_project_volume(tmp_path):
    project = "dwarf-profile-install-profile-owned"
    volume_name = project + "_p1-state"
    volume = {
        "Name": volume_name,
        "Labels": {
            "com.docker.compose.project": project,
            "com.docker.compose.volume": "p1-state",
        },
    }
    calls = []

    def runner(argv, timeout, execution_id):
        calls.append(argv)
        if argv[1:3] == ("volume", "ls"):
            return DockerResult(0, volume_name + "\n", "")
        if argv[1:] == ("volume", "inspect", volume_name):
            return DockerResult(0, json.dumps([volume]), "")
        raise AssertionError(argv)

    runtime = DockerRuntime(installation_id="install", state_dir=tmp_path, runner=runner)

    with pytest.raises(DockerRuntimeError, match="unmanaged or shared volume"):
        runtime._project_volume_preflight(
            topology_id="profile-owned",
            compose_project=project,
            specs={volume_name: "p1-state"},
            require_complete=True,
        )

    assert not any("rm" in argv for argv in calls)


def test_amaru_remove_cancellation_uses_scoped_volumes_and_restores_snapshot(
    monkeypatch, tmp_path
):
    topology_id = "profile-owned"
    project = "dwarf-profile-install-profile-owned"
    current = tmp_path / "controller" / "topology-runtimes" / topology_id / "current"
    current.mkdir(parents=True)
    (current / "docker-compose.json").write_text(
        json.dumps(
            {
                "services": {
                    "p1": {"labels": {"io.dwarf.component": "node"}}
                }
            }
        ),
        encoding="utf-8",
    )
    runtime = DockerRuntime(installation_id="install", state_dir=tmp_path)
    retained = {
        "adapter": "runtime_amaru_control_substrate.py",
        "cardano_image": "example.invalid/cardano@" + DIGEST,
    }
    specs = {project + "_p1-state": "p1-state"}
    snapshot = current.parent / ".volume-rollback-test"
    cancelled = threading.Event()
    events = []

    monkeypatch.setattr(runtime, "_project_preflight", lambda *_: ([{"Name": "owned"}], None))
    monkeypatch.setattr(runtime, "_verify_retained_runtime", lambda **_: retained)
    monkeypatch.setattr(runtime, "_retained_volume_specs", lambda **_: specs)
    monkeypatch.setattr(runtime, "_project_volume_preflight", lambda **_: {})
    monkeypatch.setattr(
        runtime,
        "_quiesce_owned_containers",
        lambda **_: events.append(("quiesce", None)),
    )

    def snapshot_volumes(**_):
        snapshot.mkdir()
        events.append(("snapshot", None))
        return snapshot

    monkeypatch.setattr(runtime, "_snapshot_owned_volumes", snapshot_volumes)
    monkeypatch.setattr(
        runtime,
        "_restore_owned_volumes",
        lambda **_: events.append(("restore-volumes", None)),
    )
    monkeypatch.setattr(
        runtime,
        "_restore_container_states",
        lambda **_: events.append(("restore-states", None)),
    )
    monkeypatch.setattr(runtime, "_restore_network_state", lambda **_: None)

    def compose(runtime_root, compose_project, arguments, execution_id, **_):
        events.append(("compose", arguments))
        if arguments[0] == "down":
            cancelled.set()
        return DockerResult(0, "ok", "")

    monkeypatch.setattr(runtime, "_compose", compose)

    result = runtime.apply(
        {
            "action": "remove",
            "topology_id": topology_id,
            "deployment": {
                "adapter": "runtime_amaru_control_substrate.py",
                "compose_project": project,
            },
        },
        "execution-remove-volumes",
        cancelled,
    )

    assert result["status"] == "cancelled"
    assert events[:4] == [
        ("quiesce", None),
        ("snapshot", None),
        ("compose", ("down", "--volumes", "--remove-orphans")),
        ("restore-volumes", None),
    ]
    assert ("compose", ("up", "-d")) in events
    assert current.is_dir()


def test_amaru_remove_cancellation_during_snapshot_never_reaches_down(
    monkeypatch, tmp_path
):
    topology_id = "profile-owned"
    project = "dwarf-profile-install-profile-owned"
    current = tmp_path / "controller" / "topology-runtimes" / topology_id / "current"
    current.mkdir(parents=True)
    (current / "docker-compose.json").write_text(
        json.dumps(
            {
                "services": {
                    "p1": {"labels": {"io.dwarf.component": "node"}}
                }
            }
        ),
        encoding="utf-8",
    )
    runtime = DockerRuntime(installation_id="install", state_dir=tmp_path)
    specs = {project + "_p1-state": "p1-state"}
    snapshot = current.parent / ".volume-rollback-cancel"
    cancelled = threading.Event()
    events = []

    monkeypatch.setattr(runtime, "_project_preflight", lambda *_: ([{"Name": "owned"}], None))
    monkeypatch.setattr(
        runtime,
        "_verify_retained_runtime",
        lambda **_: {
            "adapter": "runtime_amaru_control_substrate.py",
            "cardano_image": "example.invalid/cardano@" + DIGEST,
        },
    )
    monkeypatch.setattr(runtime, "_retained_volume_specs", lambda **_: specs)
    monkeypatch.setattr(runtime, "_project_volume_preflight", lambda **_: {})
    monkeypatch.setattr(
        runtime,
        "_quiesce_owned_containers",
        lambda **_: events.append(("quiesce", None)),
    )

    def snapshot_volumes(**_):
        snapshot.mkdir()
        events.append(("snapshot", None))
        cancelled.set()
        return snapshot

    monkeypatch.setattr(runtime, "_snapshot_owned_volumes", snapshot_volumes)
    monkeypatch.setattr(
        runtime,
        "_restore_container_states",
        lambda **_: events.append(("restore-states", None)),
    )
    monkeypatch.setattr(
        runtime,
        "_compose",
        lambda *_args, **_kwargs: events.append(("compose", None)),
    )

    result = runtime.apply(
        {
            "action": "remove",
            "topology_id": topology_id,
            "deployment": {
                "adapter": "runtime_amaru_control_substrate.py",
                "compose_project": project,
            },
        },
        "execution-cancel-snapshot",
        cancelled,
    )

    assert result["status"] == "cancelled"
    assert events == [
        ("quiesce", None),
        ("snapshot", None),
        ("restore-states", None),
    ]
    assert not snapshot.exists()


def test_volume_quiescence_stops_only_prior_running_owned_writers(tmp_path):
    topology_id = "profile-owned"
    project = "dwarf-profile-install-profile-owned"
    writer_roles = {
        "p1": "node",
        "p2": "support",
        "p3": "support",
        "relay1": "support",
        "relay2": "support",
        "amaru-consumer": "support",
    }
    stopped_service = "configurator"
    states = {service: "running" for service in writer_roles}
    states[stopped_service] = "exited"
    calls = []

    def inventory():
        return [
            _container(
                f"{project}-{service}-1",
                {
                    "io.dwarf.managed": "true",
                    "io.dwarf.installation": "install",
                    "io.dwarf.topology": topology_id,
                    "io.dwarf.component": writer_roles.get(service, "support"),
                    "com.docker.compose.project": project,
                    "com.docker.compose.service": service,
                },
                state=states[service],
            )
            for service in (*writer_roles, stopped_service)
        ]

    before = inventory()
    ids = [f"id-{index}" for index in range(len(before))]

    def runner(argv, timeout, execution_id):
        calls.append(argv)
        if argv[1:] == ("ps", "-aq"):
            return DockerResult(0, "\n".join(ids) + "\n", "")
        if argv[1:] == ("inspect", *ids):
            return DockerResult(0, json.dumps(inventory()), "")
        if argv[1:3] == ("stop", "--time") and argv[3] == "60":
            service = argv[4][len(project) + 1 : -2]
            states[service] = "exited"
            return DockerResult(0, argv[4], "")
        raise AssertionError(argv)

    runtime = DockerRuntime(installation_id="install", state_dir=tmp_path, runner=runner)
    current = runtime._runtime_parent(topology_id) / "current"
    current.mkdir(parents=True)
    (current / "docker-compose.json").write_text(
        json.dumps(
            {
                "services": {
                    service: {
                        "labels": {
                            "io.dwarf.component": writer_roles.get(
                                service, "support"
                            )
                        }
                    }
                    for service in (*writer_roles, stopped_service)
                }
            }
        )
    )
    runtime._quiesce_owned_containers(
        topology_id=topology_id,
        before=before,
        execution_id="execution-quiesce",
    )

    assert states == {service: "exited" for service in states}
    assert all(
        ("docker", "stop", "--time", "60", f"{project}-{service}-1") in calls
        for service in writer_roles
    )
    assert (
        "docker",
        "stop",
        "--time",
        "60",
        f"{project}-{stopped_service}-1",
    ) not in calls


def test_volume_quiescence_failure_restores_original_running_states(tmp_path):
    topology_id = "profile-owned"
    project = "dwarf-profile-install-profile-owned"
    services = {"p1": "node", "p2": "support"}
    names = [f"{project}-{service}-1" for service in services]
    states = {name: "running" for name in names}

    def inventory():
        return [
            _container(
                name,
                {
                    "io.dwarf.managed": "true",
                    "io.dwarf.installation": "install",
                    "io.dwarf.topology": topology_id,
                    "io.dwarf.component": services[name[len(project) + 1 : -2]],
                    "com.docker.compose.project": project,
                    "com.docker.compose.service": name[len(project) + 1 : -2],
                },
                state=states[name],
            )
            for name in names
        ]

    before = inventory()

    def runner(argv, timeout, execution_id):
        if argv[1:] == ("ps", "-aq"):
            return DockerResult(0, "id-first\nid-second\n", "")
        if argv[1:] == ("inspect", "id-first", "id-second"):
            return DockerResult(0, json.dumps(inventory()), "")
        if argv[1:] == ("stop", "--time", "60", names[0]):
            states[names[0]] = "exited"
            return DockerResult(0, names[0], "")
        if argv[1:] == ("stop", "--time", "60", names[1]):
            return DockerResult(1, "", "deliberate stop failure")
        if argv[1] == "start" and argv[2] in names:
            states[argv[2]] = "running"
            return DockerResult(0, argv[2], "")
        raise AssertionError(argv)

    runtime = DockerRuntime(installation_id="install", state_dir=tmp_path, runner=runner)
    current = runtime._runtime_parent(topology_id) / "current"
    current.mkdir(parents=True)
    (current / "docker-compose.json").write_text(
        json.dumps(
            {
                "services": {
                    service: {"labels": {"io.dwarf.component": role}}
                    for service, role in services.items()
                }
            }
        )
    )
    with pytest.raises(DockerRuntimeError, match="quiescence failed"):
        runtime._quiesce_owned_containers(
            topology_id=topology_id,
            before=before,
            execution_id="execution-quiesce-failure",
        )

    assert states == {name: "running" for name in names}


def test_remove_with_missing_retained_compose_fails_without_false_success(tmp_path):
    project = "dwarf-profile-install-local-lab"
    labels = {
        "io.dwarf.managed": "true",
        "io.dwarf.installation": "install",
        "io.dwarf.topology": "local-lab",
        "io.dwarf.component": "node",
    }
    owned = _container(project + "-amaru-1-1", labels)
    network = {"Name": project + "-net", "Labels": {**labels, "io.dwarf.component": "network"}}

    def runner(argv, timeout, execution_id):
        if argv[1:] == ("ps", "-aq"):
            return DockerResult(0, "owned-id\n", "")
        if argv[1:] == ("inspect", "owned-id"):
            return DockerResult(0, json.dumps([owned]), "")
        if argv[1:] == ("network", "inspect", project + "-net"):
            return DockerResult(0, json.dumps([network]), "")
        raise AssertionError(argv)

    runtime = DockerRuntime(installation_id="install", state_dir=tmp_path, runner=runner)
    current = runtime._runtime_parent("local-lab") / "current"
    current.mkdir(parents=True)

    with pytest.raises(DockerRuntimeError, match="integrity record"):
        runtime.apply(_plan(action="remove"), "execution-missing-compose", threading.Event())

    assert current.is_dir()
