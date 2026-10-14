from types import SimpleNamespace

from scripts import runtime_multi_node_observation as observation


def test_container_connection_state_is_observed_in_container_namespace(monkeypatch, tmp_path):
    def forbidden_host_process_resolution(*_args, **_kwargs):
        raise AssertionError("container observation must not inspect the controller process namespace")

    def fake_run(command, **_kwargs):
        if command[:3] == ["docker", "inspect", "--format"]:
            return SimpleNamespace(returncode=0, stdout="1234\n", stderr="")
        if command[:3] == ["docker", "exec", "cardano-node1"] and command[3:5] == ["bash", "-lc"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command == ["docker", "exec", "cardano-node1", "ss", "-tan"]:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "LISTEN 0 128 0.0.0.0:3001 0.0.0.0:*\n"
                    "ESTAB 0 0 10.0.0.2:3001 10.0.0.3:40100\n"
                ),
                stderr="",
            )
        raise AssertionError(command)

    monkeypatch.setattr(observation, "resolve_target_process", forbidden_host_process_resolution)
    monkeypatch.setattr(observation, "_run", fake_run)

    result = observation._observe_connection_state(
        runtime_metadata_path=tmp_path / "runtime.json",
        node={
            "name": "node1",
            "port": 34401,
            "container_name": "cardano-node1",
            "container_listen_address": "node1:3001",
        },
        known_ports={34401: "node1", 34402: "node2"},
        known_container_ips={"10.0.0.2": "node1", "10.0.0.3": "node2"},
        target_host="127.0.0.1",
        connect_attempts=2,
    )

    assert result["pid"] == 1234
    assert result["pid_scope"] == "docker-host"
    assert result["connect_scope"] == "container-loopback"
    assert result["connect_successes"] == 2
    assert result["peer_nodes_connected"] == ["node2"]
    assert result["responsive"] is True
