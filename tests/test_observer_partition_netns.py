"""F110: the observer partition reaches container nodes through their network namespace.

runtime_partition_rejoin_check.py ran `sudo iptables` on the controller's loopback: the controller
has no sudo, and container nodes are not on its loopback. The partition now drops only the
traffic between the controller and the node's container port, inside the node's namespace,
with the same helper settings as the F99 port faults.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from scripts import runtime_common as rc


def _config(tmp_path, *, container=True):
    entry = {"id": "node2", "name": "node2", "port": 34302}
    if container:
        entry.update({"container_name": "dwarf-p-node2-1", "container_ip": "10.9.0.3",
                      "container_listen_address": "node2:3001"})
    (tmp_path / "runtime.json").write_text(json.dumps({"haskell_nodes": [entry]}), encoding="utf-8")
    return rc.RuntimeConfig(**{**rc.PROFILE_A_CONFIG.__dict__, "runtime_root": tmp_path})


def _runner(calls):
    def run(argv, **kwargs):
        calls.append(list(argv))
        if argv[:2] == ["docker", "inspect"] and "{{.Image}}" in " ".join(argv):
            return SimpleNamespace(returncode=0, stdout="sha256:" + "c" * 64 + "\n", stderr="")
        if argv[:2] == ["docker", "inspect"]:
            return SimpleNamespace(returncode=0, stdout="10.9.0.9\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    return run


def test_container_partition_drops_only_controller_traffic_in_the_node_namespace(tmp_path, monkeypatch):
    monkeypatch.setenv("HOSTNAME", "controllerid")
    calls = []
    rc.observer_partition(_config(tmp_path), "node2", apply=True, runner=_runner(calls))
    (helper,) = [c for c in calls if c[:2] == ["docker", "run"]]
    joined = " ".join(helper)
    assert "--network container:dwarf-p-node2-1" in joined and "--user 0:0" in joined
    assert "--cap-drop ALL --cap-add NET_ADMIN" in joined and "--security-opt no-new-privileges" in joined
    assert "sudo" not in joined
    script = helper[-1]
    assert "iptables -w -I INPUT -p tcp -s 10.9.0.9 --dport 3001 -j DROP" in script
    assert "iptables -w -I OUTPUT -p tcp -d 10.9.0.9 --sport 3001 -j DROP" in script


def test_container_partition_removal_deletes_the_same_rules(tmp_path, monkeypatch):
    monkeypatch.setenv("HOSTNAME", "controllerid")
    calls = []
    rc.observer_partition(_config(tmp_path), "node2", apply=False, runner=_runner(calls))
    script = [c for c in calls if c[:2] == ["docker", "run"]][0][-1]
    assert "iptables -w -D INPUT -p tcp -s 10.9.0.9 --dport 3001 -j DROP" in script


def test_host_nodes_keep_the_loopback_rules(tmp_path):
    calls = []
    rc.observer_partition(_config(tmp_path, container=False), "node2", apply=True, runner=_runner(calls))
    assert ["sudo", "iptables", "-w", "-I", "OUTPUT", "-p", "tcp", "-d", "127.0.0.1", "--dport", "34302", "-j", "DROP"] in calls
