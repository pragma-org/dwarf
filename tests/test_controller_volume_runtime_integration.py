from __future__ import annotations

import os
import json
import subprocess
import uuid
from pathlib import Path

import pytest

from controller.docker_runtime import DockerRuntime


pytestmark = pytest.mark.skipif(
    not os.environ.get("DWARF_DOCKER_VOLUME_TEST_IMAGE"),
    reason="set DWARF_DOCKER_VOLUME_TEST_IMAGE to a locally present digest-pinned image",
)


def _docker(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("docker", *arguments),
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _create_owned_volume(
    *, name: str, project: str, key: str, installation: str, topology: str
) -> None:
    _docker(
        "volume",
        "create",
        "--label",
        "io.dwarf.managed=true",
        "--label",
        f"io.dwarf.installation={installation}",
        "--label",
        f"io.dwarf.topology={topology}",
        "--label",
        "io.dwarf.component=volume",
        "--label",
        f"com.docker.compose.project={project}",
        "--label",
        f"com.docker.compose.volume={key}",
        name,
    )


def _copy_into_volume(*, volume: str, source: Path, image: str, token: str) -> None:
    helper = f"dwarf-volume-test-seed-{token}"
    _docker(
        "create",
        "--name",
        helper,
        "--network",
        "none",
        "--mount",
        f"type=volume,source={volume},target=/volume",
        "--entrypoint",
        "/bin/true",
        image,
    )
    try:
        _docker("cp", "--archive", str(source) + "/.", f"{helper}:/volume")
    finally:
        _docker("container", "rm", "--force", helper)


def _create_writer(
    *,
    project: str,
    service: str,
    role: str,
    volume: str,
    image: str,
    installation: str,
    topology: str,
) -> None:
    name = f"{project}-{service}-1"
    _docker(
        "create",
        "--name",
        name,
        "--network",
        "none",
        "--label",
        "io.dwarf.managed=true",
        "--label",
        f"io.dwarf.installation={installation}",
        "--label",
        f"io.dwarf.topology={topology}",
        "--label",
        f"io.dwarf.component={role}",
        "--label",
        f"com.docker.compose.project={project}",
        "--label",
        f"com.docker.compose.service={service}",
        "--mount",
        f"type=volume,source={volume},target=/volume",
        "--entrypoint",
        "/bin/sh",
        image,
        "-c",
        "trap 'exit 0' TERM INT; while :; do sleep 60 & wait $!; done",
    )


def test_owned_volume_snapshot_scoped_remove_and_verified_cancellation_restore(
    tmp_path,
):
    image = os.environ["DWARF_DOCKER_VOLUME_TEST_IMAGE"]
    token = uuid.uuid4().hex[:12]
    installation = f"volume-test-{token}"
    topology = f"profile-volume-test-{token}"
    project = f"dwarf-profile-{installation}-{topology}"
    key = "chain-state"
    volume = f"{project}_{key}"
    protected_project = f"protected-volume-test-{token}"
    protected = f"{protected_project}_sentinel"
    writer_roles = {
        "p1": "node",
        "p2": "support",
        "p3": "support",
        "relay1": "support",
        "relay2": "support",
        "amaru-consumer": "support",
    }
    writers = [f"{project}-{service}-1" for service in writer_roles]
    source = tmp_path / "seed"
    source.mkdir()
    (source / "chain.dat").write_bytes(b"exact-chain-state\x00\xff\n")
    nested = source / "nested"
    nested.mkdir()
    (nested / "tip.json").write_text('{"slot": 424242}\n', encoding="utf-8")

    runtime = DockerRuntime(installation_id=installation, state_dir=tmp_path)
    snapshot = None
    try:
        _create_owned_volume(
            name=volume,
            project=project,
            key=key,
            installation=installation,
            topology=topology,
        )
        _create_owned_volume(
            name=protected,
            project=protected_project,
            key="sentinel",
            installation="protected-installation",
            topology="protected-topology",
        )
        _copy_into_volume(volume=volume, source=source, image=image, token=token)

        current = runtime._runtime_parent(topology) / "current"
        current.mkdir(parents=True)
        (current / "docker-compose.json").write_text(
            json.dumps(
                {
                    "services": {
                        service: {"labels": {"io.dwarf.component": role}}
                        for service, role in writer_roles.items()
                    }
                }
            )
        )
        for service, role in writer_roles.items():
            _create_writer(
                project=project,
                service=service,
                role=role,
                volume=volume,
                image=image,
                installation=installation,
                topology=topology,
            )
        _docker("start", *writers)
        assert all(
            _docker("inspect", "--format", "{{.State.Status}}", writer)
            .stdout.strip()
            == "running"
            for writer in writers
        )
        before = runtime._project_runtime_containers(
            topology_id=topology,
            compose_project=project,
            expected_roles=writer_roles,
        )
        runtime._quiesce_owned_containers(
            topology_id=topology,
            before=before,
            execution_id=f"quiesce-{token}",
        )
        assert all(
            _docker("inspect", "--format", "{{.State.Status}}", writer)
            .stdout.strip()
            == "exited"
            for writer in writers
        )

        snapshot = runtime._snapshot_owned_volumes(
            parent=tmp_path,
            topology_id=topology,
            compose_project=project,
            specs={volume: key},
            image=image,
            execution_id=f"snapshot-{token}",
        )

        _docker("container", "rm", *writers)
        _docker("volume", "rm", volume)
        runtime._verify_absent_postcondition(
            topology_id=topology,
            compose_project=project,
            config={"adapter": "runtime_amaru_control_substrate.py"},
            volume_specs={volume: key},
        )
        assert _docker("volume", "inspect", protected).returncode == 0

        runtime._restore_owned_volumes(
            snapshot=snapshot,
            topology_id=topology,
            compose_project=project,
            specs={volume: key},
            image=image,
            execution_id=f"cancel-rollback-{token}",
        )

        for service, role in writer_roles.items():
            _create_writer(
                project=project,
                service=service,
                role=role,
                volume=volume,
                image=image,
                installation=installation,
                topology=topology,
            )
        runtime._restore_container_states(
            topology_id=topology,
            before=before,
            execution_id=f"cancel-states-{token}",
        )
        assert all(
            _docker("inspect", "--format", "{{.State.Status}}", writer)
            .stdout.strip()
            == "running"
            for writer in writers
        )
        _docker("stop", "--time", "10", *writers)

        restored = tmp_path / "restored"
        digest = runtime._copy_volume_to_directory(
            volume_name=volume,
            destination=restored,
            topology_id=topology,
            image=image,
            execution_id=f"verify-{token}",
        )
        record = json.loads(
            (snapshot / "manifest.json").read_text(encoding="utf-8")
        )["volumes"][volume]
        assert digest == record["content_sha256"]
        assert (restored / "chain.dat").read_bytes() == b"exact-chain-state\x00\xff\n"
        assert (restored / "nested" / "tip.json").read_text(encoding="utf-8") == (
            '{"slot": 424242}\n'
        )
        assert _docker("volume", "inspect", protected).returncode == 0
    finally:
        subprocess.run(
            ("docker", "container", "rm", "--force", *writers),
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        subprocess.run(
            ("docker", "volume", "rm", volume, protected),
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
