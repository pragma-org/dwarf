"""F90: the controller's run-network join tolerates an existing membership."""
from __future__ import annotations

import json

from controller.docker_runtime import DockerResult, DockerRuntime


def test_run_attach_treats_an_existing_membership_as_attached(tmp_path, monkeypatch):
    monkeypatch.setenv("HOSTNAME", "controller-container-id")
    calls = []

    def docker_runner(argv, timeout, execution_id):
        calls.append(tuple(argv))
        if tuple(argv[-4:-2]) == ("network", "connect"):
            return DockerResult(1, "", "Error response from daemon: endpoint with name controller-container-id already exists in network net-x")
        raise AssertionError(argv)

    runtime = DockerRuntime(installation_id="install", state_dir=tmp_path / "controller-state", host_state_dir="/srv/dwarf", runner=docker_runner)
    monkeypatch.setattr(runtime, "resolve_run_network", lambda profile_id: "net-x")
    attachment = runtime._attach_run_network("profile-a", "run-execution-x")
    assert attachment["state"] == "attached" and attachment["preexisting"] is True
    assert any(tuple(c[-4:]) == ("network", "connect", "net-x", "controller-container-id") for c in calls)
