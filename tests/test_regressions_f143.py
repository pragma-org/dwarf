"""F143: time-skew imported the removed fake-observation helper (ImportError, live ac6916db);
the observation threw when a node container had exited instead of recording the node as down
(live 9f4ac73a, fc5026ab: Amaru crashed by design; the crash is the evidence)."""
from __future__ import annotations

from types import SimpleNamespace


def test_time_skew_imports_and_writes_no_fabricated_observation():
    from scripts import runtime_time_skew as skew
    assert not hasattr(skew, "_default_observation_overrides")


def test_an_exited_container_is_recorded_as_down(monkeypatch):
    from scripts import runtime_multi_node_observation as obs
    monkeypatch.setattr(obs, "_run", lambda argv, timeout=10: SimpleNamespace(returncode=0, stdout="0\n", stderr=""))
    node = {"id": "node2", "container_name": "dwarf-x-node2-1", "port": 34002, "container_listen_address": "node2:3001"}
    from pathlib import Path
    state = obs._observe_connection_state(runtime_metadata_path=Path("/nonexistent"), node=node, known_ports={},
                                          known_container_ips={}, target_host="127.0.0.1", connect_attempts=1)
    assert state["responsive"] is False
    assert state["container_running"] is False
