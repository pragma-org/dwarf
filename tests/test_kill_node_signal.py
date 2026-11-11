"""runtime_kill_node can send SIGKILL (an unclean exit) instead of the default SIGTERM.

The restart-after-SIGKILL finding (cardano-node 10.7.1 GC crash on Antithesis) needs the local
path to reproduce an *unclean* stop; SIGTERM lets the node flush its ChainDB and never reaches it."""
from __future__ import annotations

from pathlib import Path

from profile_manager.primitives import RuntimeKillNode
from scripts import runtime_recovery_fault as rf


def _docker_stub(monkeypatch, calls):
    monkeypatch.setattr(rf, "_docker_available", lambda: True)
    monkeypatch.setattr(rf, "_container_exists", lambda name: True)
    monkeypatch.setattr(rf, "_container_running", lambda name: True)
    monkeypatch.setattr(rf, "_wait", lambda predicate, **kw: True)
    monkeypatch.setattr(rf, "_docker", lambda *args, timeout=60: calls.append(args))


def test_kill_node_sends_sigkill_when_the_scenario_asks_for_it(monkeypatch):
    calls = []
    _docker_stub(monkeypatch, calls)
    metadata = {"nodes": [{"id": "node2", "container_name": "c-node2"}]}
    out = rf.apply_recovery_mode(metadata=metadata, mode="kill_node",
                                 config={"target_node": "node2", "signal": "KILL"})
    assert calls == [("kill", "-s", "KILL", "c-node2")]
    assert out["result"]["signal"] == "SIGKILL" and out["result"]["stopped"] is True
    assert out["observation_overrides"]["node_lifecycle"]["node2"]["signal"] == "SIGKILL"


def test_kill_node_default_stays_sigterm_and_the_primitive_forwards_the_signal(monkeypatch):
    calls = []
    _docker_stub(monkeypatch, calls)
    rf.apply_recovery_mode(metadata={"nodes": [{"id": "n", "container_name": "c"}]},
                           mode="kill_node", config={"target_node": "n"})
    assert calls == [("kill", "-s", "TERM", "c")]
    primitive = RuntimeKillNode(params={"target_node": "node2", "signal": "kill", "output_dir": "outputs/k"})
    assert primitive._build_config(Path("."), {})["signal"] == "KILL"
