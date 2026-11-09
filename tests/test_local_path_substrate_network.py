"""F66 (2026-11-08): the controller joins a run-owned substrate network.

For profile-bound runs the controller connects itself to the profile's network
(``_attach_run_network``), which is why probes can reach node containers by
their container address.  A self-composed substrate has no profile: its
network is created by the compose script during the run and nothing attached
the controller, so the F63 container endpoint timed out and the published
loopback port was refused.  The compose script, which runs inside the
controller with the Docker socket, now connects the controller to the
substrate's network after ``up`` and the teardown disconnects it before
``down``.
"""
import json
import subprocess

from scripts import runtime_compose_substrate as compose
from scripts import runtime_teardown_substrate as teardown


def _runner(calls, returncode=0, stderr=""):
    def run(command, **kwargs):
        calls.append(list(command))
        return subprocess.CompletedProcess(command, returncode, "", stderr)
    return run


def test_controller_joins_the_substrate_network_when_it_owns_the_topology(monkeypatch):
    monkeypatch.setenv("HOSTNAME", "485be25a27dc")
    calls = []
    attachment = compose._attach_controller_to_substrate_network(
        docker_project="dwarf-substrate-run1",
        controller_ownership={"installation_id": "install-x", "topology_id": "dwarf-substrate-run1"},
        controller_attach=True,
        runner=_runner(calls),
    )
    assert calls == [["docker", "network", "connect", "dwarf-substrate-run1-net", "485be25a27dc"]]
    assert attachment == {"network": "dwarf-substrate-run1-net", "container": "485be25a27dc"}


def test_an_already_connected_controller_is_not_an_error(monkeypatch):
    monkeypatch.setenv("HOSTNAME", "485be25a27dc")
    calls = []
    attachment = compose._attach_controller_to_substrate_network(
        docker_project="dwarf-substrate-run1",
        controller_ownership={"installation_id": "install-x", "topology_id": "dwarf-substrate-run1"},
        controller_attach=True,
        runner=_runner(calls, returncode=1, stderr="Error response from daemon: endpoint with name 485be25a27dc already exists in network dwarf-substrate-run1-net"),
    )
    assert attachment == {"network": "dwarf-substrate-run1-net", "container": "485be25a27dc"}


def test_host_runs_without_controller_ownership_attach_nothing(monkeypatch):
    monkeypatch.setenv("HOSTNAME", "laptop")
    calls = []
    assert compose._attach_controller_to_substrate_network(
        docker_project="dwarf-substrate-run1", controller_ownership=None, controller_attach=True, runner=_runner(calls)
    ) is None
    assert calls == []


def test_profile_deploys_do_not_attach_the_controller_even_though_the_controller_owns_them(monkeypatch):
    """F90: profile deploys are controller-owned too, but the run's own attach step joins (and leaves)
    the profile network; a deploy-time attachment made the first run's join fail."""
    monkeypatch.setenv("HOSTNAME", "485be25a27dc")
    calls = []
    assert compose._attach_controller_to_substrate_network(
        docker_project="dwarf-profile-install-x-profile-a",
        controller_ownership={"installation_id": "install-x", "topology_id": "profile-a"},
        controller_attach=False,
        runner=_runner(calls),
    ) is None
    assert calls == []


def test_teardown_disconnects_the_controller_before_compose_down(tmp_path):
    metadata = tmp_path / "runtime.json"
    metadata.write_text(json.dumps({
        "compose_mode": "docker", "compose_project": "dwarf-substrate-run1", "runtime_root": str(tmp_path),
        "controller_attachment": {"network": "dwarf-substrate-run1-net", "container": "485be25a27dc"},
        "nodes": [{"id": "node1", "container_name": "dwarf-substrate-run1-node1-1"}],
    }), encoding="utf-8")
    calls = []
    report = teardown.teardown_substrate(runtime_metadata_path=metadata, output_dir=tmp_path / "out", runner=_runner(calls))
    assert calls[0] == ["docker", "network", "disconnect", "dwarf-substrate-run1-net", "485be25a27dc"]
    assert calls[-1][:5] == ["docker", "compose", "--project-name", "dwarf-substrate-run1", "down"]
    assert report["controller_detached"] is True


def test_teardown_keeps_each_node_state_and_log_tail_before_removal(tmp_path):
    """F104: a node that stopped answering left no evidence once compose down removed it."""
    metadata = tmp_path / "runtime.json"
    metadata.write_text(json.dumps({
        "compose_mode": "docker", "compose_project": "dwarf-substrate-run1", "runtime_root": str(tmp_path),
        "nodes": [{"id": "node1", "container_name": "dwarf-substrate-run1-node1-1"}],
    }), encoding="utf-8")
    calls = []
    state = {"Status": "running", "Running": True, "ExitCode": 0, "OOMKilled": False, "StartedAt": "t0", "FinishedAt": ""}

    def run(command, **kwargs):
        calls.append(list(command))
        if command[:2] == ["docker", "inspect"]:
            return subprocess.CompletedProcess(command, 0, json.dumps([{"State": state, "RestartCount": 2}]), "")
        if command[:2] == ["docker", "logs"]:
            return subprocess.CompletedProcess(command, 0, "stdout line\n", "thread 'main' has overflowed its stack\n")
        return subprocess.CompletedProcess(command, 0, "", "")

    report = teardown.teardown_substrate(runtime_metadata_path=metadata, output_dir=tmp_path / "out", runner=run)
    down = next(i for i, c in enumerate(calls) if c[:2] == ["docker", "compose"] and "down" in c)
    assert max(i for i, c in enumerate(calls) if c[:2] in (["docker", "inspect"], ["docker", "logs"])) < down
    node = report["nodes"][0]
    assert node["state_before_teardown"] == {**state, "RestartCount": 2}
    log = (tmp_path / "out" / node["log_path"]).read_text()
    assert "has overflowed its stack" in log and "stdout line" in log
    assert ["--tail", str(teardown.NODE_LOG_TAIL_LINES)] == calls[[c[:2] for c in calls].index(["docker", "logs"])][2:4]

