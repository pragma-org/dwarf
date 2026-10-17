from types import SimpleNamespace

import json

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


def test_missing_container_ss_is_optional_but_tip_and_responsiveness_are_retained(
    monkeypatch, tmp_path
):
    runtime_metadata_path = tmp_path / "runtime.json"
    runtime_metadata_path.write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(
        observation,
        "_load_runtime_nodes",
        lambda _path: (
            {},
            {
                "node1": {
                    "name": "node1",
                    "implementation": "amaru",
                    "version": "10.11.20260925",
                    "port": 34401,
                    "container_name": "amaru-node1",
                    "container_listen_address": "node1:3001",
                }
            },
        ),
    )
    monkeypatch.setattr(
        observation, "_resolve_network_magic", lambda _metadata, value: value
    )
    monkeypatch.setattr(
        observation,
        "_observe_tip_state",
        lambda **_kwargs: {
            "latest_tip": {"ok": True, "slot": 324074, "hash": "ab" * 32, "block": 3270},
            "samples": [{"ok": True, "slot": 324074, "hash": "ab" * 32, "block": 3270}],
        },
    )

    def fake_run(command, **_kwargs):
        if command[:3] == ["docker", "inspect", "--format"]:
            return SimpleNamespace(returncode=0, stdout="4321\n", stderr="")
        if command[:3] == ["docker", "exec", "amaru-node1"] and command[3:5] == ["bash", "-lc"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command == ["docker", "exec", "amaru-node1", "ss", "-tan"]:
            return SimpleNamespace(returncode=127, stdout="", stderr="ss: command not found\n")
        raise AssertionError(command)

    monkeypatch.setattr(observation, "_run", fake_run)

    result = observation.run_multi_node_observation(
        runtime_metadata_path=runtime_metadata_path,
        node_ids=["node1"],
        observation_window_seconds=1,
        observation_primitives=["tip_state", "connection_state"],
        output_dir=tmp_path / "observation",
        network_magic=42,
        connect_attempts=1,
    )

    connection = result["per_node"]["node1"]["connection_state"]
    assert connection["responsive"] is True
    assert connection["responsiveness_basis"] == ["docker-pid", "container-loopback-connect"]
    assert connection["socket_observation"] == {
        "status": "unavailable",
        "reason": "ss-command-failed",
        "return_code": 127,
    }
    assert connection["ss_match_count"] is None
    assert connection["ss_listen_count"] is None
    assert connection["ss_established_count"] is None
    assert connection["peer_nodes_connected"] is None
    assert result["summary"]["responsive_nodes"] == ["node1"]
    assert result["summary"]["latest_tips"]["node1"]["slot"] == 324074
    persisted = json.loads(
        (
            tmp_path
            / "observation"
            / "per-node"
            / "node1"
            / "connection-state.json"
        ).read_text()
    )
    assert persisted["socket_observation"]["status"] == "unavailable"
