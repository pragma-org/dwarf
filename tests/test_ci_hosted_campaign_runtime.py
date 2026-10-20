from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from scripts import ci_hosted_campaign_runtime as api


def test_controller_command_preserves_installed_runtime_contract(tmp_path):
    workspace = tmp_path / "workspace"
    runtime = workspace / "campaign-runtime"
    signer = tmp_path / "signer.pem"
    workspace.mkdir()
    runtime.mkdir()
    signer.write_text("private key fixture")
    lock = tmp_path / "campaign.lock"
    lock.touch()
    controller = "sha256:" + "a" * 64
    helper = "ghcr.io/j-gainsec/dwarf-localtx-submit-raw@sha256:" + "b" * 64

    command = api.build_controller_command(
        controller_image=controller,
        helper_image=helper,
        workspace=workspace,
        runtime_directory=runtime,
        signing_key=signer,
        host_lock=lock,
        docker_socket=Path("/var/run/docker.sock"),
        docker_socket_gid=998,
        host_uid=1001,
        host_gid=1001,
        installation_id="github-run-123",
        artifact_base_url="http://172.17.0.1:8787",
    )

    assert command[:3] == ["docker", "run", "--rm"]
    controller_name = "dwarf-controller-github-run-123"
    assert command[command.index("--name") + 1] == controller_name
    assert "--network" in command and command[command.index("--network") + 1] == "host"
    assert "--hostname" not in command
    assert "io.dwarf.application=true" in command
    assert "io.dwarf.component=controller" in command
    assert "io.dwarf.installation=github-run-123" in command
    assert (
        f"type=bind,src={runtime / 'runs'},dst=/var/dwarf/runs"
        in command
    )
    assert f"type=bind,src={workspace},dst={workspace},readonly" in command
    assert f"type=bind,src={signer},dst=/run/workflow-evidence.pem,readonly" in command
    assert "ADA2_DWARF_RUNS_DIR=/var/dwarf/runs" in command
    assert "ADA2_DWARF_STATE_DIR=/var/dwarf/runtime-state" in command
    assert f"DWARF_CONTROLLER_HOST_STATE_DIR={runtime / 'runtime-state'}" in command
    assert f"DWARF_CONTROLLER_CONTAINER_ID={controller_name}" in command
    assert f"DWARF_LOCALTXSUBMISSION_HELPER_IMAGE={helper}" in command
    assert "DWARF_RUN_ARTIFACT_BASE_URL=http://172.17.0.1:8787" in command
    assert command[command.index("--entrypoint") + 1] == "python3"
    assert command[command.index(controller) + 1 : command.index(controller) + 4] == [
        "-m",
        "scripts.ci_hosted_campaign_runtime",
        "inside",
    ]


def test_helper_identity_requires_exact_amd64_provenance():
    reference = "ghcr.io/j-gainsec/dwarf-localtx-submit-raw@sha256:" + "b" * 64
    inspected = {
        "Id": "sha256:" + "c" * 64,
        "Os": "linux",
        "Architecture": "amd64",
        "RepoDigests": [reference],
        "Config": {
            "Labels": {
                "io.dwarf.component": "dwarf-localtx-submit-raw",
                "org.opencontainers.image.revision": "d" * 40,
            }
        },
    }

    assert api.validate_helper_identity(reference, inspected)["source_revision"] == "d" * 40

    inspected["Architecture"] = "arm64"
    try:
        api.validate_helper_identity(reference, inspected)
    except ValueError as error:
        assert "linux/amd64" in str(error)
    else:
        raise AssertionError("wrong-platform helper was accepted")


def test_controller_identity_requires_exact_config_and_trusted_revision():
    image_id = "sha256:" + "a" * 64
    revision = "b" * 40
    inspected = {
        "Id": image_id,
        "Os": "linux",
        "Architecture": "amd64",
        "Config": {
            "Labels": {
                "io.dwarf.component.role": "controller",
                "org.opencontainers.image.revision": revision,
            }
        },
    }

    assert api.validate_controller_identity(
        image_id, expected_revision=revision, inspected=inspected
    ) == {"image_id": image_id, "source_revision": revision}

    inspected["Config"]["Labels"]["org.opencontainers.image.revision"] = "c" * 40
    with pytest.raises(ValueError, match="trusted revision"):
        api.validate_controller_identity(
            image_id, expected_revision=revision, inspected=inspected
        )


def test_bridge_gateway_requires_one_ipv4_gateway():
    inspected = {
        "IPAM": {
            "Config": [
                {"Subnet": "172.17.0.0/16", "Gateway": "172.17.0.1"},
                {"Subnet": "fd00::/64", "Gateway": "fd00::1"},
            ]
        }
    }
    assert api.bridge_gateway(inspected) == "172.17.0.1"
    with pytest.raises(ValueError, match="one IPv4 gateway"):
        api.bridge_gateway({"IPAM": {"Config": []}})


def test_inside_runtime_serves_artifacts_while_campaign_runs(tmp_path, monkeypatch):
    directory = tmp_path / "runtime"
    directory.mkdir()
    observed = {}

    class Process:
        def terminate(self):
            observed["terminated"] = True

        def wait(self, timeout):
            observed["wait_timeout"] = timeout
            return 0

        def kill(self):
            raise AssertionError("healthy dashboard should terminate cleanly")

    def popen(command, **kwargs):
        observed["dashboard"] = (command, kwargs)
        return Process()

    monkeypatch.setattr(api.subprocess, "Popen", popen)
    monkeypatch.setattr(api, "_wait_for_listener", lambda *args: observed.setdefault("ready", True))
    monkeypatch.setattr(
        api,
        "_campaign_main",
        lambda argv: observed.setdefault("campaign", argv) and 7,
    )
    args = SimpleNamespace(
        manifest=tmp_path / "manifest.json",
        stock_manifest=tmp_path / "stock.json",
        build_observations=tmp_path / "builds.json",
        directory=directory,
        catalog=tmp_path / "catalog.json",
        signing_key=tmp_path / "signer.pem",
    )

    assert api.run_inside(args) == 7
    assert observed["ready"] is True
    assert observed["terminated"] is True
    assert observed["wait_timeout"] == 10
    dashboard_command = observed["dashboard"][0]
    assert dashboard_command[-6:] == [
        "--output-dir",
        "/var/dwarf/dashboard-static",
        "--bind",
        "0.0.0.0",
        "--port",
        "8787",
    ]
    assert observed["campaign"] == [
        "--manifest",
        str(args.manifest),
        "--stock-manifest",
        str(args.stock_manifest),
        "--build-observations",
        str(args.build_observations),
        "--directory",
        str(directory),
        "--catalog",
        str(args.catalog),
        "--signing-key",
        str(args.signing_key),
    ]


def test_outer_runtime_verifies_images_and_uses_bridge_artifact_route(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = tmp_path / "runtime"
    signer = tmp_path / "signer.pem"
    signer.write_text("private key fixture")
    docker_socket = tmp_path / "docker.sock"
    docker_socket.touch()
    controller = "sha256:" + "a" * 64
    revision = "b" * 40
    helper = "ghcr.io/j-gainsec/dwarf-localtx-submit-raw@sha256:" + "c" * 64
    observed = []

    def run(command, *, check=True):
        observed.append((command, check))
        if command[:3] == ["docker", "image", "inspect"]:
            reference = command[3]
            if reference == helper:
                document = {
                    "Id": "sha256:" + "d" * 64,
                    "Os": "linux",
                    "Architecture": "amd64",
                    "RepoDigests": [helper],
                    "Config": {"Labels": {
                        "io.dwarf.component": "dwarf-localtx-submit-raw",
                        "org.opencontainers.image.revision": "e" * 40,
                    }},
                }
            else:
                document = {
                    "Id": controller,
                    "Os": "linux",
                    "Architecture": "amd64",
                    "Config": {"Labels": {
                        "io.dwarf.component.role": "controller",
                        "org.opencontainers.image.revision": revision,
                    }},
                }
            return SimpleNamespace(returncode=0, stdout=json.dumps([document]))
        if command == ["docker", "network", "inspect", "bridge"]:
            document = {"IPAM": {"Config": [
                {"Subnet": "172.17.0.0/16", "Gateway": "172.17.0.1"}
            ]}}
            return SimpleNamespace(returncode=0, stdout=json.dumps([document]))
        return SimpleNamespace(returncode=9 if not check else 0, stdout="")

    monkeypatch.setattr(api, "_run_command", run)
    args = SimpleNamespace(
        controller_image=controller,
        expected_controller_revision=revision,
        helper_image=helper,
        workspace=workspace,
        runtime_directory=runtime,
        signing_key=signer,
        installation_id="github-run-123-1",
        docker_socket=docker_socket,
    )

    assert api.run_outer(args) == 9
    assert observed[0] == (["docker", "pull", helper], True)
    command = observed[-1][0]
    assert observed[-1][1] is False
    assert command[:3] == ["docker", "run", "--rm"]
    assert controller in command
    assert "DWARF_RUN_ARTIFACT_BASE_URL=http://172.17.0.1:8787" in command
    assert all((runtime / name).is_dir() for name in (
        "runs", "runtime-state", "bundles", "launches", "run-ledger"
    ))
